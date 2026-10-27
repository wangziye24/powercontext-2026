"""Task state kept in PowerContext.

The module stores the progress of a development task as PowerContext Memory
entries. PowerContext owns the durable state, so a restarted module reads the
saved progress back through the Server API and continues from it.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any

import httpx

DEFAULT_BASE_URL = "http://127.0.0.1:8000"
STATE_KIND = "sdd-task-state"
ENV_BASE_URL = "POWERCONTEXT_SERVER_URL"


class TaskStateError(RuntimeError):
    """Raised when the PowerContext Server cannot serve the request."""


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


class PowerContextState:
    """Read and write the task state through the PowerContext HTTP API."""

    def __init__(self, base_url: str | None = None, timeout: float = 60.0) -> None:
        self.base_url = (base_url or os.environ.get(ENV_BASE_URL, DEFAULT_BASE_URL)).rstrip("/")
        self.timeout = timeout

    def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        url = f"{self.base_url}{path}"
        try:
            with httpx.Client(timeout=self.timeout) as client:
                response = client.post(url, json=payload)
            response.raise_for_status()
            return response.json()
        except Exception as error:  # noqa: BLE001 - surfaced to the caller with context
            raise TaskStateError(f"{path} failed: {type(error).__name__}: {error}") from error

    def resolve_scope(self) -> str:
        """Resolve the Scope that owns the task state."""

        body = self._post("/v1/scope-bindings/resolve", {"allow_default": True})
        scope_id = body.get("scope_id")
        if not scope_id:
            raise TaskStateError(f"the server did not return a scope_id: {body}")
        return str(scope_id)

    def remember(self, scope_id: str, kind: str, text: str, reason: str | None = None) -> dict[str, Any]:
        """Save one Memory entry."""

        payload: dict[str, Any] = {"scope_id": scope_id, "kind": kind, "text": text}
        if reason:
            payload["reason"] = reason
        return self._post("/v1/memory/remember", payload)

    def list_entries(self, scope_id: str) -> list[dict[str, Any]]:
        """List the active Memory entries of a Scope."""

        body = self._post("/v1/memory/entries/list", {"scope_id": scope_id})
        entries = body.get("entries") or []
        return [entry for entry in entries if entry.get("state", "active") == "active"]

    def save_task_state(self, scope_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Persist the progress of one development task."""

        record = dict(payload)
        record["updated_at"] = _now()
        return self.remember(
            scope_id,
            STATE_KIND,
            json.dumps(record, ensure_ascii=False, indent=2),
            reason="SDD task progress",
        )

    def load_task_state(self, scope_id: str, task_key: str) -> dict[str, Any] | None:
        """Read back the newest saved state of one development task."""

        for entry in self.list_entries(scope_id):
            if entry.get("kind") != STATE_KIND:
                continue
            try:
                record = json.loads(entry.get("text") or "{}")
            except json.JSONDecodeError:
                continue
            if record.get("task_key") == task_key:
                return record
        return None

    def list_task_states(self, scope_id: str) -> list[dict[str, Any]]:
        """Return every saved task state of a Scope, newest first."""

        states: list[dict[str, Any]] = []
        for entry in self.list_entries(scope_id):
            if entry.get("kind") != STATE_KIND:
                continue
            try:
                states.append(json.loads(entry.get("text") or "{}"))
            except json.JSONDecodeError:
                continue
        states.sort(key=lambda item: str(item.get("updated_at", "")), reverse=True)
        return states
