# Copyright (c) 2026 OceanBase.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Coalesce concurrent embedding requests and cache their vectors.

Independent callers often ask for overlapping text at almost the same instant. Asking the
provider once per caller wastes requests and quota, so this wrapper resolves each distinct
text exactly once inside a short coalescing window, batches the result under the provider's
limit, and replays the cached vector for every waiter.

A text that already left the coalescing window is still shared: dispatched work stays in an
in-flight table until its batches settle, so a late caller joins the running request instead
of paying for a second one. That holds with caching disabled too, because the in-flight table
is the sharing mechanism and the cache only spares later, still newer requests.
"""

from __future__ import annotations

import asyncio
from collections import OrderedDict
from dataclasses import dataclass
from typing import TYPE_CHECKING

from powercontext.builtin.inference.models import EmbeddingResult, InferenceUsage

if TYPE_CHECKING:
    from powercontext.builtin.artifacts.memory.models import EmbeddingProfile

DEFAULT_MAX_BATCH_SIZE = 64
DEFAULT_COALESCE_MS = 5.0
DEFAULT_CACHE_SIZE = 1024


@dataclass(frozen=True, slots=True)
class _Resolved:
    """One text's vector plus the provider call that produced it, if it was not cached."""

    vector: tuple[float, ...]
    call_id: int | None


class CoalescingEmbeddingModel:
    """Deduplicate, batch, and cache embeddings for one wrapped provider model."""

    def __init__(
        self,
        model: object,
        *,
        max_batch_size: int = DEFAULT_MAX_BATCH_SIZE,
        coalesce_ms: float = DEFAULT_COALESCE_MS,
        cache_size: int = DEFAULT_CACHE_SIZE,
    ) -> None:
        if isinstance(max_batch_size, bool) or not isinstance(max_batch_size, int) or max_batch_size < 1:
            raise ValueError("max_batch_size must be a positive integer")  # noqa: TRY003
        if isinstance(coalesce_ms, bool) or not isinstance(coalesce_ms, (int, float)) or not coalesce_ms >= 0:
            raise ValueError("coalesce_ms must be a non-negative number")  # noqa: TRY003
        if isinstance(cache_size, bool) or not isinstance(cache_size, int) or cache_size < 0:
            raise ValueError("cache_size must be a non-negative integer")  # noqa: TRY003
        self._model = model
        self.profile = model.profile  # type: ignore[attr-defined]
        self._max_batch_size = max_batch_size
        self._coalesce_seconds = float(coalesce_ms) / 1_000
        self._cache_size = cache_size
        self._cache: OrderedDict[str, tuple[float, ...]] = OrderedDict()
        self._pending: dict[str, asyncio.Future[_Resolved]] = {}
        self._inflight: dict[str, asyncio.Future[_Resolved]] = {}
        self._call_usage: dict[int, InferenceUsage] = {}
        self._pump: asyncio.Task[None] | None = None
        self._call_seq = 0

    async def embed(self, texts: tuple[str, ...], /) -> EmbeddingResult:
        """Return one vector per input text, restoring the caller's original order."""

        texts = tuple(texts)
        if not texts:
            return EmbeddingResult(vectors=(), usage=InferenceUsage(requests=0))
        unique = tuple(dict.fromkeys(texts))
        resolved = await asyncio.gather(*(self._resolve_one(text) for text in unique))
        by_text = dict(zip(unique, resolved, strict=True))
        call_ids = {by_text[text].call_id for text in texts if by_text[text].call_id is not None}
        return EmbeddingResult(
            vectors=tuple(by_text[text].vector for text in texts),
            usage=self._aggregate_usage(call_ids),
        )

    def _aggregate_usage(self, call_ids: set[int]) -> InferenceUsage:
        """Report only the provider calls this caller actually paid for."""

        input_tokens = 0
        output_tokens = 0
        provider_requests = 0
        saw_input = False
        saw_output = False
        for call_id in call_ids:
            usage = self._call_usage.get(call_id)
            if usage is None:
                continue
            # A shared batch is counted once per waiter, and the provider's own
            # request count is what the budget is measured against.
            provider_requests += usage.requests or 0
            if usage.input_tokens is not None:
                input_tokens += usage.input_tokens
                saw_input = True
            if usage.output_tokens is not None:
                output_tokens += usage.output_tokens
                saw_output = True
        requests = provider_requests if provider_requests > 0 else len(call_ids)
        return InferenceUsage(
            requests=requests,
            input_tokens=input_tokens if saw_input else None,
            output_tokens=output_tokens if saw_output else None,
        )

    async def embed_query(self, texts: tuple[str, ...], /) -> EmbeddingResult:
        """Embed retrieval queries through the same coalescing and caching path."""

        return await self.embed(texts)

    async def _resolve_one(self, text: str) -> _Resolved:
        cached = self._cache_lookup(text)
        if cached is not None:
            return _Resolved(cached, None)
        future = self._pending.get(text)
        if future is None:
            # A text whose window already closed keeps sharing its dispatched batch.
            future = self._inflight.get(text)
        if future is None:
            future = asyncio.get_running_loop().create_future()
            self._pending[text] = future
            self._schedule_pump()
        # Shield the shared future so one waiter's cancellation cannot cancel the others.
        return await asyncio.shield(future)

    def _schedule_pump(self) -> None:
        if self._pump is None or self._pump.done():
            self._pump = asyncio.create_task(self._drain())

    async def _drain(self) -> None:
        await asyncio.sleep(self._coalesce_seconds)
        while self._pending:
            batch = list(self._pending.items())[: self._max_batch_size]
            for text, future in batch:
                self._pending.pop(text, None)
                self._inflight[text] = future
            await self._fetch(batch)

    async def _fetch(self, batch: list[tuple[str, asyncio.Future[_Resolved]]]) -> None:
        texts = tuple(text for text, _ in batch)
        self._call_seq += 1
        call_id = self._call_seq
        try:
            result = await self._model.embed(texts)  # type: ignore[attr-defined]
            vectors = tuple(result.vectors)
            if len(vectors) != len(texts):
                raise ValueError("embedding provider returned a vector count that does not match its input")
            self._call_usage[call_id] = result.usage
        except BaseException as error:  # noqa: BLE001 - propagate the provider failure to every waiter
            for text, future in batch:
                # A failed batch is never cached, so the same text can be retried.
                self._inflight.pop(text, None)
                if not future.done():
                    future.set_exception(error)
            if isinstance(error, asyncio.CancelledError):
                raise
            # Drain the rest of the window; one failed batch must not strand the others.
            return
        for (text, future), vector in zip(batch, vectors, strict=True):
            normalized = tuple(float(value) for value in vector)
            self._cache_store(text, normalized)
            self._inflight.pop(text, None)
            if not future.done():
                future.set_result(_Resolved(normalized, call_id))

    def _cache_lookup(self, text: str) -> tuple[float, ...] | None:
        if self._cache_size < 1:
            return None
        cached = self._cache.pop(text, None)
        if cached is None:
            return None
        self._cache[text] = cached
        return cached

    def _cache_store(self, text: str, vector: tuple[float, ...]) -> None:
        if self._cache_size < 1:
            return
        self._cache[text] = vector
        self._cache.move_to_end(text)
        while len(self._cache) > self._cache_size:
            self._cache.popitem(last=False)


__all__ = [
    "DEFAULT_CACHE_SIZE",
    "DEFAULT_COALESCE_MS",
    "DEFAULT_MAX_BATCH_SIZE",
    "CoalescingEmbeddingModel",
]
