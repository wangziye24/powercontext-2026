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

"""Capture the original visible events of one Pi session branch.

A Pi archive is a tree: each record names its parent, and the selected leaf plus its
ancestors form the visible history. Capturing that path as individual Sources keeps the
uncompressed evidence that summaries later replace, while the connector's checkpoint makes
re-imports incremental and detects rewritten history.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote

from pydantic import BaseModel, ConfigDict

from powercontext.limits import MAX_SOURCE_ID_LENGTH
from powercontext.sources.connectors import ConnectorRunCompletion, ConnectorRunSession, ConnectorRunStatus
from powercontext.sources.models import Source, SourceMaterialization
from powercontext.sources.projections import TEXT_EVIDENCE_PROJECTION_KEY, TextEvidence

PI_EVENT_SOURCE_NAME = "pi-event"
PI_EVENT_DEFINITION_VERSION = "1"
PI_SESSION_CONNECTOR_NAME = "pi-session-history"
PI_SESSION_CONNECTOR_VERSION = "1"
PI_EVENT_SOURCE_TYPE = "pi-event"
PI_IDENTITY_PREFIX = "pi"
PI_SESSION_VERSION = 3


@dataclass(frozen=True, slots=True)
class PiSessionInput:
    """One Pi session archive and the branch leaf to walk up from."""

    content: bytes
    leaf_id: str | None = None


class PiEventInput(BaseModel):
    """One visible session event together with the record it came from."""

    model_config = ConfigDict(frozen=True)

    session_id: str
    event_id: str
    parent_id: str | None
    event_type: str
    role: str
    content: str
    record: dict[str, Any]


class PiEventSource(Source):
    """A captured session event addressed by session and event identity."""

    source_id: str
    session_id: str
    event_id: str
    parent_id: str | None
    event_type: str
    role: str
    content: str
    record: dict[str, Any]


def pi_event_source_id(session_id: str, event_id: str, /) -> str:
    """Compose the stable, delimiter-safe identity of one session event."""

    if not isinstance(session_id, str) or not session_id or session_id != session_id.strip():
        raise ValueError("session id must be a non-empty trimmed string")
    if not isinstance(event_id, str) or not event_id:
        raise ValueError("event id must be a non-empty string")
    identity = f"{PI_IDENTITY_PREFIX}:{quote(session_id, safe='')}:{quote(event_id, safe='')}"
    if len(identity) > MAX_SOURCE_ID_LENGTH:
        raise ValueError("event identity exceeds the maximum Source identifier length")
    return identity


def parse_pi_session(value: PiSessionInput, /) -> tuple[PiEventInput, ...]:
    """Return the visible events of the selected branch, ordered root to leaf."""

    records = _decode_records(value.content)
    header = records[0]
    if header.get("type") != "session" or header.get("version") != PI_SESSION_VERSION:
        raise ValueError("Pi archive must start with a version 3 session header")
    session_id = header.get("id")
    if not isinstance(session_id, str) or not session_id:
        raise ValueError("Pi session header must carry a non-empty id")
    by_id: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for record in records[1:]:
        record_id = record.get("id")
        if not isinstance(record_id, str) or not record_id:
            raise ValueError("every Pi record must carry a non-empty id")
        if record_id in by_id:
            raise ValueError("Pi record ids must be unique within a session")
        parent = record.get("parentId")
        if parent is not None:
            if not isinstance(parent, str) or not parent:
                raise ValueError("Pi record parents must be non-empty ids or null")
            if parent not in by_id:
                raise ValueError("Pi record parents must already appear in the archive")
        by_id[record_id] = record
        order.append(record_id)
    leaf = value.leaf_id if value.leaf_id is not None else (order[-1] if order else None)
    if leaf is None:
        return ()
    if leaf not in by_id:
        raise ValueError("the selected leaf does not exist in this Pi session")
    chain: list[dict[str, Any]] = []
    cursor: str | None = leaf
    while cursor is not None:
        record = by_id[cursor]
        chain.append(record)
        cursor = record.get("parentId")
    chain.reverse()
    events: list[PiEventInput] = []
    for record in chain:
        event = _to_event(session_id, record)
        if event is not None:
            events.append(event)
    return tuple(events)


def _to_event(session_id: str, record: dict[str, Any], /) -> PiEventInput | None:
    event_type = record.get("type")
    role: str | None = None
    body: str | None = None
    if event_type == "message":
        message = record.get("message")
        if not isinstance(message, dict):
            return None
        message_role = message.get("role")
        if not isinstance(message_role, str) or not message_role:
            return None
        role = message_role
        body = _message_body(message)
    elif event_type in ("compaction", "branch_summary"):
        role = str(event_type)
        summary = record.get("summary")
        body = summary if isinstance(summary, str) else None
    elif event_type == "custom_message":
        role = "custom"
        body = _text_body(record.get("content"))
    else:
        return None
    if not body:
        return None
    return PiEventInput(
        session_id=session_id,
        event_id=str(record["id"]),
        parent_id=record.get("parentId"),
        event_type=str(event_type),
        role=role,
        content=f"{role}: {body}",
        record=record,
    )


def _message_body(message: dict[str, Any], /) -> str | None:
    role = message.get("role")
    if role == "toolResult":
        name = message.get("toolName")
        call_id = message.get("toolCallId")
        text = _text_body(message.get("content")) or ""
        return f"[tool_result {name} {call_id}] {text}"
    if role == "bashExecution":
        command = message.get("command")
        output = message.get("output")
        return f"$ {command}\n{output}" if output is not None else f"$ {command}"
    return _text_body(message.get("content"))


def _text_body(content: object, /) -> str | None:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return None
    parts: list[str] = []
    for block in content:
        if not isinstance(block, dict):
            continue
        block_type = block.get("type")
        if block_type == "text":
            text = block.get("text")
            if isinstance(text, str):
                parts.append(text)
        elif block_type == "toolCall":
            name = block.get("name")
            call_id = block.get("id")
            arguments = block.get("arguments")
            rendered = (
                json.dumps(arguments, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
                if arguments is not None
                else "{}"
            )
            parts.append(f"[tool_call {name} {call_id}] {rendered}")
    return "\n".join(parts) if parts else None


def _decode_records(content: object, /) -> list[dict[str, Any]]:
    if not isinstance(content, (bytes, bytearray, memoryview)):
        raise ValueError("Pi session content must be bytes")
    raw = bytes(content)
    if not raw:
        raise ValueError("Pi session archive is empty")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as error:
        raise ValueError("Pi session archive must be UTF-8") from error
    if text.startswith("\ufeff"):
        text = text[1:]
    records: list[dict[str, Any]] = []
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            parsed = json.loads(line)
        except ValueError as error:
            raise ValueError("Pi session archive contains invalid JSON") from error
        if not isinstance(parsed, dict):
            raise ValueError("every Pi session record must be a JSON object")
        records.append(parsed)
    if not records:
        raise ValueError("Pi session archive is empty")
    return records


class PiEventTextEvidence:
    """Expose one session event as standard text evidence with event metadata."""

    name = TEXT_EVIDENCE_PROJECTION_KEY.name
    version = TEXT_EVIDENCE_PROJECTION_KEY.version
    source_class = PiEventSource
    output_class = TextEvidence

    def project(self, source: PiEventSource, /) -> object:
        return TextEvidence(
            source_type=PI_EVENT_SOURCE_TYPE,
            source_id=source.source_id,
            content=source.content,
            metadata={
                "session_id": source.session_id,
                "event_id": source.event_id,
                "parent_id": source.parent_id,
                "event_type": source.event_type,
                "role": source.role,
            },
        ).model_dump(mode="json")


class PiEventSourceDefinition:
    """Bind the Pi event adapter to its durable version and standard projection."""

    name = PI_EVENT_SOURCE_NAME
    version = PI_EVENT_DEFINITION_VERSION
    input_class = PiEventInput
    source_class = PiEventSource
    projections = (PiEventTextEvidence(),)

    async def resolve(self, value: PiEventInput, /) -> PiEventSource:
        identity = pi_event_source_id(value.session_id, value.event_id)
        return PiEventSource(
            name=identity,
            source_id=identity,
            definition_version=self.version,
            materialization=SourceMaterialization.CAPTURED,
            session_id=value.session_id,
            event_id=value.event_id,
            parent_id=value.parent_id,
            event_type=value.event_type,
            role=value.role,
            content=value.content,
            record=value.record,
        )

    async def read(self, source: PiEventSource, /) -> PiEventInput:
        return PiEventInput(
            session_id=source.session_id,
            event_id=source.event_id,
            parent_id=source.parent_id,
            event_type=source.event_type,
            role=source.role,
            content=source.content,
            record=source.record,
        )


PI_EVENT_SOURCE_DEFINITION = PiEventSourceDefinition()


class PiSessionConnector:
    """Import one Pi session branch incrementally, one visible event per Source."""

    name = PI_SESSION_CONNECTOR_NAME
    version = PI_SESSION_CONNECTOR_VERSION
    source_definitions = frozenset({PI_EVENT_SOURCE_NAME})

    def __init__(self, path: str | Path, leaf_id: str | None = None) -> None:
        self._path = Path(path)
        self._leaf_id = leaf_id

    async def run(self, session: ConnectorRunSession, /) -> ConnectorRunCompletion:
        raw = self._path.read_bytes()
        events = parse_pi_session(PiSessionInput(content=raw, leaf_id=self._leaf_id))
        imported = _read_checkpoint(session.checkpoint)
        next_imported = dict(imported)
        for event in events:
            fingerprint = _fingerprint(event.record)
            known = imported.get(event.event_id)
            if known is not None:
                if known != fingerprint:
                    raise ValueError("an already imported Pi event was rewritten")
                continue
            await session.submit(event.event_id, PI_EVENT_SOURCE_NAME, event)
            next_imported[event.event_id] = fingerprint
        return ConnectorRunCompletion(
            status=ConnectorRunStatus.COMPLETE,
            checkpoint={_CHECKPOINT_KEY: next_imported},
        )


_CHECKPOINT_KEY = "events"


def _read_checkpoint(checkpoint: object, /) -> dict[str, str]:
    if not isinstance(checkpoint, dict):
        return {}
    events = checkpoint.get(_CHECKPOINT_KEY)
    if not isinstance(events, dict):
        return {}
    return {
        key: value for key, value in events.items() if isinstance(key, str) and isinstance(value, str)
    }


def _fingerprint(record: dict[str, Any], /) -> str:
    canonical = json.dumps(record, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


__all__ = [
    "PI_EVENT_DEFINITION_VERSION",
    "PI_EVENT_SOURCE_DEFINITION",
    "PI_EVENT_SOURCE_NAME",
    "PI_SESSION_CONNECTOR_NAME",
    "PI_SESSION_CONNECTOR_VERSION",
    "PI_EVENT_SOURCE_TYPE",
    "PiEventInput",
    "PiEventSource",
    "PiEventSourceDefinition",
    "PiEventTextEvidence",
    "PiSessionConnector",
    "PiSessionInput",
    "parse_pi_session",
    "pi_event_source_id",
]
