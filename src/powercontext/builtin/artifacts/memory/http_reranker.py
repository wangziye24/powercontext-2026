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

"""Cross-batch HTTP reranking for coarse Memory search candidates.

The remote service scores one bounded batch at a time, so a request only carries a
slice of the pool. Selection stays global: every batch reports scores back for the
original candidate ranks, and the decision is taken once over the whole pool.
"""

from __future__ import annotations

import math
from typing import Any

import httpx

from powercontext.builtin.artifacts.memory.models import MemoryHit
from powercontext.builtin.artifacts.memory.reranking import MemoryRerankDecision
from powercontext.builtin.inference import InferenceUsage

DEFAULT_HTTP_RERANK_POLICY_ID = "http-cross-encoder-v1"


class HTTPMemoryReranker:
    """Score coarse candidates with an external HTTP cross-encoder.

    ``policy_id`` names the scoring contract rather than a model revision, so the same
    identifier may back several deployments that share the wire format below.
    """

    def __init__(
        self,
        endpoint: str,
        *,
        client: httpx.AsyncClient,
        batch_size: int = 32,
        policy_id: str = DEFAULT_HTTP_RERANK_POLICY_ID,
    ) -> None:
        if isinstance(batch_size, bool) or not isinstance(batch_size, int):
            raise ValueError("batch_size must be a positive integer")  # noqa: TRY003
        if batch_size < 1:
            raise ValueError("batch_size must be a positive integer")  # noqa: TRY003
        self._endpoint = endpoint
        self._client = client
        self._batch_size = batch_size
        self.policy_id = policy_id

    async def rerank(
        self,
        query: str,
        candidates: tuple[MemoryHit, ...],
        limit: int,
        /,
    ) -> MemoryRerankDecision:
        """Request scores batch by batch, then select once over the whole pool."""

        _validate_bounds(len(candidates), limit)
        scored: dict[int, float] = {}
        requests_issued = 0
        for start in range(0, len(candidates), self._batch_size):
            batch_ranks = tuple(
                range(start + 1, min(start + self._batch_size, len(candidates)) + 1),
            )
            response = await self._client.post(
                self._endpoint,
                json={
                    "query": query,
                    "candidates": [
                        {"rank": rank, "text": candidates[rank - 1].text} for rank in batch_ranks
                    ],
                },
            )
            requests_issued += 1
            response.raise_for_status()
            scored.update(_read_batch_scores(response, batch_ranks))
        ordered = sorted(scored.items(), key=lambda item: (-item[1], item[0]))
        return MemoryRerankDecision(
            selected_ranks=tuple(rank for rank, _ in ordered[:limit]),
            usage=InferenceUsage(requests=requests_issued),
        )


def _read_batch_scores(
    response: httpx.Response,
    batch_ranks: tuple[int, ...],
) -> dict[int, float]:
    """Validate one batch response and map it back onto original candidate ranks."""

    try:
        payload: Any = response.json()
    except ValueError as exc:
        raise ValueError("rerank response body is not valid JSON") from exc
    if not isinstance(payload, dict):
        raise ValueError("rerank response must be a JSON object")  # noqa: TRY003
    if set(payload) != {"scores"}:
        raise ValueError("rerank response must contain exactly 'scores'")  # noqa: TRY003
    entries = payload["scores"]
    if not isinstance(entries, list):
        raise ValueError("rerank response 'scores' must be a list")  # noqa: TRY003
    expected = set(batch_ranks)
    parsed: dict[int, float] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError("each rerank score must be a JSON object")  # noqa: TRY003
        if set(entry) != {"rank", "score"}:
            raise ValueError("each rerank score must contain exactly 'rank' and 'score'")  # noqa: TRY003
        rank = entry["rank"]
        if isinstance(rank, bool) or not isinstance(rank, int):
            raise ValueError("rerank score rank must be an integer")  # noqa: TRY003
        raw_score = entry["score"]
        if isinstance(raw_score, bool) or not isinstance(raw_score, (int, float)):
            raise ValueError("rerank score must be a number")  # noqa: TRY003
        value = float(raw_score)
        if not math.isfinite(value):
            raise ValueError("rerank score must be finite")  # noqa: TRY003
        if rank not in expected:
            raise ValueError("rerank score rank is outside the request batch")  # noqa: TRY003
        if rank in parsed:
            raise ValueError("rerank score rank was answered more than once")  # noqa: TRY003
        parsed[rank] = value
    if set(parsed) != expected:
        raise ValueError("rerank response did not answer every rank in its batch")  # noqa: TRY003
    return parsed


def _validate_bounds(candidate_count: int, limit: int) -> None:
    if candidate_count < 1:
        raise ValueError("rerank requires at least one candidate")  # noqa: TRY003
    if limit < 1 or limit > candidate_count:
        raise ValueError("rerank limit must be between one and the candidate count")  # noqa: TRY003


__all__ = [
    "DEFAULT_HTTP_RERANK_POLICY_ID",
    "HTTPMemoryReranker",
]
