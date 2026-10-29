"""Minimal MiniMax client used to perform the real code change.

The client speaks the OpenAI compatible HTTP API of MiniMax. Every call is
persisted with the model name, the request, the raw response and the token
usage, so the submission can prove that MiniMax produced the submitted change.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

DEFAULT_BASE_URL = "https://api.minimaxi.com/v1"
DEFAULT_MODEL = "MiniMax-M3.1-Flash"
ENV_KEY = "MINIMAX_API_KEY"
ENV_BASE_URL = "MINIMAX_BASE_URL"
ENV_MODEL = "MINIMAX_MODEL"
# 通用别名：任何 OpenAI 兼容的"已有 AI 开发助手"都可以接管改码环节。
ENV_KEY_GENERIC = "SDDCTX_LLM_API_KEY"
ENV_BASE_URL_GENERIC = "SDDCTX_LLM_BASE_URL"
ENV_MODEL_GENERIC = "SDDCTX_LLM_MODEL"


def _env(*names: str, default: str = "") -> str:
    """Return the first non-empty environment variable among ``names``."""

    for name in names:
        value = os.environ.get(name)
        if value:
            return value
    return default


class MiniMaxError(RuntimeError):
    """Raised when the MiniMax API cannot serve the request."""


@dataclass
class MiniMaxCall:
    """One recorded call against the MiniMax API."""

    model: str
    base_url: str
    requested_at: str
    duration_ms: float
    request: dict[str, Any]
    response: dict[str, Any]
    usage: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    reasoning: str = ""

    @property
    def content(self) -> str:
        choices = self.response.get("choices") or []
        if not choices:
            return ""
        message = choices[0].get("message") or {}
        return message.get("content") or ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "model": self.model,
            "base_url": self.base_url,
            "requested_at": self.requested_at,
            "duration_ms": round(self.duration_ms, 3),
            "request": self.request,
            "response": self.response,
            "usage": self.usage,
            "error": self.error,
        }


class MiniMaxClient:
    """Call the MiniMax chat completion API."""

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
        timeout: float = 600.0,
    ) -> None:
        self.api_key = api_key or _env(ENV_KEY_GENERIC, ENV_KEY)
        self.base_url = (base_url or _env(ENV_BASE_URL_GENERIC, ENV_BASE_URL, default=DEFAULT_BASE_URL)).rstrip("/")
        self.model = model or _env(ENV_MODEL_GENERIC, ENV_MODEL, default=DEFAULT_MODEL)
        self.timeout = timeout

    def available(self) -> bool:
        """Return whether the client can issue a request."""

        return bool(self.api_key)

    def chat(
        self,
        system: str,
        user: str,
        *,
        max_tokens: int = 8192,
        temperature: float = 0.2,
    ) -> MiniMaxCall:
        """Send one chat completion request and return the recorded call."""

        if not self.api_key:
            raise MiniMaxError(
                f"{ENV_KEY_GENERIC}/{ENV_KEY} is not set. "
                "Export the API key of the AI assistant before running the apply step."
            )

        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "max_tokens": max_tokens,
            "temperature": temperature,
            "reasoning_split": True,
        }
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        started = time.perf_counter()
        requested_at = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
        try:
            with httpx.Client(timeout=self.timeout) as client:
                response = client.post(f"{self.base_url}/chat/completions", json=payload, headers=headers)
            response.raise_for_status()
            body = response.json()
        except Exception as error:  # noqa: BLE001 - the failure is recorded, not swallowed
            call = MiniMaxCall(
                model=self.model,
                base_url=self.base_url,
                requested_at=requested_at,
                duration_ms=(time.perf_counter() - started) * 1000,
                request=payload,
                response={},
                error=f"{type(error).__name__}: {error}",
            )
            raise MiniMaxError(call.error or "MiniMax request failed") from error

        call = MiniMaxCall(
            model=self.model,
            base_url=self.base_url,
            requested_at=requested_at,
            duration_ms=(time.perf_counter() - started) * 1000,
            request=payload,
            response=body,
            usage=body.get("usage") or {},
        )
        message = ((body.get("choices") or [{}])[0] or {}).get("message") or {}
        call.reasoning = message.get("reasoning_content") or ""
        return call


def save_call(call: MiniMaxCall, directory: Path, name: str = "minimax-call") -> Path:
    """Persist one recorded call as JSON.

    The reasoning trace of the model is written next to it but kept out of the
    submitted JSON, because the challenge only asks for the request, the answer
    and the resulting change.
    """

    directory.mkdir(parents=True, exist_ok=True)
    target = directory / f"{name}.json"
    target.write_text(json.dumps(call.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
    if call.reasoning:
        (directory / f"{name}.reasoning.txt").write_text(call.reasoning, encoding="utf-8")
    return target
