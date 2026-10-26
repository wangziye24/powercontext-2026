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

"""Propagate Source availability through derived Memory and Topic Memory.

A Scope registers a small evidence graph: captured Sources, Memory entries
derived from them, and Topic Memory revisions derived from either. A lifecycle
event changes one Source's availability and every dependent node is re-evaluated
through its own ``all`` / ``any`` policy, in registration order (which is a
topological order because a parent must be registered before its dependents).

Memory availability is realised through the existing inactive entry state, so
search and Context stop offering the content while exact historical reads keep
working. Topic availability is registration state that the Topic search and
browse paths exclude; the registered ``ArtifactRef`` stays exactly readable.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt, field_validator
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncConnection

from powercontext.artifacts import ArtifactRef
from powercontext.builtin.artifacts.memory import (
    Memory,
    MemoryCitation,
    MemoryEntryVersion,
    MemoryService,
)
from powercontext.builtin.persistence.database import AsyncDatabase
from powercontext.builtin.persistence.tables import SOURCE_LIFECYCLE_NODES_TABLE
from powercontext.builtin.sources import validate_scope_id
from powercontext.sources import SourceRef

SOURCE_FAMILY = "source"
MEMORY_FAMILY = "memory"
TOPIC_FAMILY = "topic"

GENERATION_PURPOSE = "generation"
AUDIT_PURPOSE = "audit"

_POLICIES = ("all", "any")
_REGISTRATION_REASON = "source lifecycle registration"
_EVENT_REASON_PREFIX = "source lifecycle event"


class SourceLifecycleError(ValueError):
    """Reject an invalid lifecycle registration, event, or reference read."""

    def __init__(self, code: str, detail: str | None = None) -> None:
        super().__init__(code if detail is None else f"{code}: {detail}")
        self.code = code
        self.detail = detail


class SourceLifecycleEvent(BaseModel):
    """One explicit availability transition for a registered Source."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    node_id: str
    revision: StrictInt = Field(gt=0)
    sequence: StrictInt = Field(gt=0)
    available: bool
    reason: str

    @field_validator("node_id", "reason")
    @classmethod
    def validate_text(cls, value: str, info: Any) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{info.field_name} must be a non-empty, non-blank string")
        return value


@dataclass(frozen=True)
class _StoredNode:
    """One persisted lifecycle node in a Scope."""

    node_id: str
    family: str
    reference: str
    parents: tuple[str, ...]
    policy: str
    revision: int
    sequence: int
    available: bool
    event_digest: str | None
    ordinal: int

    @property
    def citation(self) -> MemoryCitation:
        return MemoryCitation.model_validate(json.loads(self.reference))

    @property
    def source_ref(self) -> SourceRef:
        return SourceRef.model_validate(json.loads(self.reference))

    @property
    def artifact_ref(self) -> ArtifactRef:
        return ArtifactRef.model_validate(json.loads(self.reference))

    @property
    def reference_value(self) -> SourceRef | MemoryCitation | ArtifactRef:
        if self.family == SOURCE_FAMILY:
            return self.source_ref
        if self.family == MEMORY_FAMILY:
            return self.citation
        return self.artifact_ref


class SourceLifecycleRepository:
    """Persist lifecycle nodes through caller-owned short transactions."""

    async def load(self, connection: AsyncConnection, scope_id: str, /) -> tuple[_StoredNode, ...]:
        table = SOURCE_LIFECYCLE_NODES_TABLE
        rows = (
            await connection.execute(
                select(table).where(table.c.scope_id == scope_id).order_by(table.c.ordinal)
            )
        ).mappings()
        return tuple(_decode_node(dict(row)) for row in rows)

    async def load_node(
        self,
        connection: AsyncConnection,
        scope_id: str,
        node_id: str,
        /,
        *,
        for_update: bool = False,
    ) -> _StoredNode | None:
        table = SOURCE_LIFECYCLE_NODES_TABLE
        statement = select(table).where(table.c.scope_id == scope_id, table.c.node_id == node_id)
        if for_update:
            statement = statement.with_for_update()
        row = (await connection.execute(statement)).mappings().one_or_none()
        return None if row is None else _decode_node(dict(row))

    async def insert(self, connection: AsyncConnection, scope_id: str, node: _StoredNode, /) -> None:
        await connection.execute(
            SOURCE_LIFECYCLE_NODES_TABLE.insert().values(
                scope_id=scope_id,
                node_id=node.node_id,
                family=node.family,
                reference=node.reference,
                parents=_encode_parents(node.parents),
                policy=node.policy,
                revision=node.revision,
                sequence=node.sequence,
                available=node.available,
                event_digest=node.event_digest,
                ordinal=node.ordinal,
            )
        )

    async def record_event(
        self,
        connection: AsyncConnection,
        scope_id: str,
        /,
        *,
        node_id: str,
        available: bool,
        sequence: int,
        event_digest: str,
    ) -> None:
        table = SOURCE_LIFECYCLE_NODES_TABLE
        await connection.execute(
            update(table)
            .where(table.c.scope_id == scope_id, table.c.node_id == node_id)
            .values(available=available, sequence=sequence, event_digest=event_digest)
        )

    async def update_states(
        self,
        connection: AsyncConnection,
        scope_id: str,
        states: Mapping[str, bool],
        /,
    ) -> None:
        table = SOURCE_LIFECYCLE_NODES_TABLE
        for node_id, available in states.items():
            await connection.execute(
                update(table)
                .where(table.c.scope_id == scope_id, table.c.node_id == node_id)
                .values(available=available)
            )

    async def unavailable_topic_references(self, connection: AsyncConnection, scope_id: str, /) -> tuple[str, ...]:
        table = SOURCE_LIFECYCLE_NODES_TABLE
        rows = (
            await connection.execute(
                select(table.c.reference).where(
                    table.c.scope_id == scope_id,
                    table.c.family == TOPIC_FAMILY,
                    table.c.available.is_(False),
                )
            )
        ).scalars()
        return tuple(str(value) for value in rows)


class LifecycleApplication:
    """Select the lifecycle service for one Scope."""

    def __init__(self, runtime: Any, database: AsyncDatabase) -> None:
        self._runtime = runtime
        self._database = database

    def for_scope(self, scope_id: str, /) -> ScopedSourceLifecycleApplication:
        return ScopedSourceLifecycleApplication(self._runtime, self._database, scope_id)


class ScopedSourceLifecycleApplication:
    """Register the evidence graph and propagate Source availability in one Scope."""

    def __init__(self, runtime: Any, database: AsyncDatabase, scope_id: str) -> None:
        self._runtime = runtime
        self._database = database
        self.scope_id = validate_scope_id(scope_id)
        self._repository = SourceLifecycleRepository()

    # -- registration ----------------------------------------------------

    async def register_source(
        self,
        node_id: str,
        source_ref: SourceRef,
        /,
        *,
        revision: int = 1,
    ) -> None:
        """Register one captured Source with the lifecycle version it starts at."""

        node = _validate_node_id(node_id)
        if not isinstance(source_ref, SourceRef):
            raise SourceLifecycleError("invalid-source-ref", node)
        if not _is_positive_int(revision):
            raise SourceLifecycleError("invalid-revision", node)
        reference = _encode_reference(source_ref)
        async with self._runtime._context(self.scope_id):
            async with self._runtime._locked(self.scope_id):
                nodes = await self._load_nodes()
                existing = _find(nodes, node)
                if existing is not None:
                    if (
                        existing.family == SOURCE_FAMILY
                        and existing.reference == reference
                        and existing.revision == revision
                    ):
                        return
                    raise SourceLifecycleError("conflicting-registration", node)
                await self._require_source(source_ref)
                async with self._database.transaction() as connection:
                    await self._repository.insert(
                        connection,
                        self.scope_id,
                        _StoredNode(
                            node_id=node,
                            family=SOURCE_FAMILY,
                            reference=reference,
                            parents=(),
                            policy="all",
                            revision=int(revision),
                            sequence=0,
                            available=True,
                            event_digest=None,
                            ordinal=len(nodes),
                        ),
                    )

    async def register_memory(
        self,
        node_id: str,
        citation: MemoryCitation,
        parents: Sequence[str],
        policy: str = "all",
    ) -> None:
        """Register one Memory entry derived from already registered nodes."""

        if not isinstance(citation, MemoryCitation):
            raise SourceLifecycleError("invalid-citation", str(node_id))
        node = _validate_node_id(node_id)
        async with self._runtime._context(self.scope_id) as context:
            async with self._runtime._locked(self.scope_id):
                service = context.artifacts.memory
                await self._require_citation(service, citation)
                prepared = await self._prepare_derived(
                    node_id=node,
                    family=MEMORY_FAMILY,
                    reference=_encode_reference(citation),
                    parents=parents,
                    policy=policy,
                )
                if prepared is None:
                    return
                stored, available = prepared
                # The entry loses generation eligibility before the graph admits the
                # node, so a failed write leaves no registration behind.
                await self._set_memory_available(service, citation, available, _REGISTRATION_REASON)
                async with self._database.transaction() as connection:
                    await self._repository.insert(connection, self.scope_id, stored)

    async def register_topic(
        self,
        node_id: str,
        artifact_ref: ArtifactRef,
        parents: Sequence[str],
        policy: str = "all",
    ) -> None:
        """Register one Topic Memory revision derived from already registered nodes."""

        if not isinstance(artifact_ref, ArtifactRef):
            raise SourceLifecycleError("invalid-artifact-ref", str(node_id))
        node = _validate_node_id(node_id)
        await self._require_artifact(artifact_ref)
        async with self._runtime._context(self.scope_id):
            async with self._runtime._locked(self.scope_id):
                prepared = await self._prepare_derived(
                    node_id=node,
                    family=TOPIC_FAMILY,
                    reference=_encode_reference(artifact_ref),
                    parents=parents,
                    policy=policy,
                )
                if prepared is None:
                    return
                stored, _available = prepared
                async with self._database.transaction() as connection:
                    await self._repository.insert(connection, self.scope_id, stored)

    async def _prepare_derived(
        self,
        *,
        node_id: str,
        family: str,
        reference: str,
        parents: Sequence[str],
        policy: str,
    ) -> tuple[_StoredNode, bool] | None:
        if policy not in _POLICIES:
            raise SourceLifecycleError("invalid-policy", node_id)
        parent_ids = _validate_parents(node_id, parents)
        nodes = await self._load_nodes()
        existing = _find(nodes, node_id)
        if existing is not None:
            if _same_binding(existing, family, reference, parent_ids, policy):
                return None
            raise SourceLifecycleError("conflicting-registration", node_id)
        states = {item.node_id: item.available for item in nodes}
        for parent in parent_ids:
            if parent not in states:
                raise SourceLifecycleError("missing-parent", parent)
        available = _derived_availability(parent_ids, policy, states)
        return (
            _StoredNode(
                node_id=node_id,
                family=family,
                reference=reference,
                parents=parent_ids,
                policy=policy,
                revision=1,
                sequence=0,
                available=available,
                event_digest=None,
                ordinal=len(nodes),
            ),
            available,
        )

    # -- events and reads ------------------------------------------------

    async def apply(self, event: SourceLifecycleEvent) -> list[str]:
        """Apply one Source transition and return every available node id."""

        if not isinstance(event, SourceLifecycleEvent):
            raise SourceLifecycleError("invalid-event")
        async with self._runtime._context(self.scope_id) as context:
            async with self._runtime._locked(self.scope_id):
                nodes = await self._load_nodes()
                node = _find(nodes, event.node_id)
                if node is None or node.family != SOURCE_FAMILY:
                    raise SourceLifecycleError("unknown-source", event.node_id)
                if event.revision != node.revision:
                    raise SourceLifecycleError("revision-mismatch", event.node_id)
                digest = _event_digest(event)
                if event.sequence < node.sequence:
                    raise SourceLifecycleError("stale-sequence", event.node_id)
                if event.sequence == node.sequence:
                    if node.event_digest == digest:
                        return sorted(item.node_id for item in nodes if item.available)
                    raise SourceLifecycleError("conflicting-sequence", event.node_id)
                targets = _propagate(nodes, event.node_id, event.available)
                changed = tuple(item for item in nodes if item.available != targets[item.node_id])
                reason = f"{_EVENT_REASON_PREFIX}: {event.reason}"
                await self._reconcile_memory(context.artifacts.memory, changed, targets, reason)
                async with self._database.transaction() as connection:
                    current = await self._repository.load_node(
                        connection, self.scope_id, event.node_id, for_update=True
                    )
                    if current is None or current.sequence != node.sequence:
                        raise SourceLifecycleError("concurrent-event", event.node_id)
                    await self._repository.record_event(
                        connection,
                        self.scope_id,
                        node_id=event.node_id,
                        available=event.available,
                        sequence=event.sequence,
                        event_digest=digest,
                    )
                    await self._repository.update_states(
                        connection,
                        self.scope_id,
                        {item.node_id: targets[item.node_id] for item in changed},
                    )
                return sorted(node_id for node_id, available in targets.items() if available)

    async def resolve(self, node_id: str, purpose: str = GENERATION_PURPOSE) -> Any:
        """Return the registered reference for generation or audit use."""

        if purpose not in (GENERATION_PURPOSE, AUDIT_PURPOSE):
            raise SourceLifecycleError("unknown-purpose", str(purpose))
        node = _validate_node_id(node_id)
        nodes = await self._load_nodes()
        stored = _find(nodes, node)
        if stored is None:
            raise SourceLifecycleError("unknown-node", node)
        if purpose == GENERATION_PURPOSE and not stored.available:
            raise SourceLifecycleError("unavailable-node", node)
        return stored.reference_value

    async def unavailable_topic_artifact_ids(self) -> frozenset[str]:
        """Return the Topic revisions that must stay out of search and Context."""

        async with self._database.transaction() as connection:
            references = await self._repository.unavailable_topic_references(connection, self.scope_id)
        return frozenset(
            ArtifactRef.model_validate(json.loads(reference)).artifact_id for reference in references
        )

    # -- internals -------------------------------------------------------

    async def _load_nodes(self) -> tuple[_StoredNode, ...]:
        async with self._database.transaction() as connection:
            return await self._repository.load(connection, self.scope_id)

    async def _require_source(self, source_ref: SourceRef) -> None:
        records = self._runtime._records()
        try:
            await records.get_source(self.scope_id, source_ref.source_type, source_ref.source_id)
        except Exception as error:  # noqa: BLE001 - every read failure is a registration failure
            raise SourceLifecycleError("unreadable-source", f"{source_ref.source_type}/{source_ref.source_id}") from (
                error
            )

    async def _require_artifact(self, reference: ArtifactRef) -> None:
        records = self._runtime._records()
        try:
            await records.get_artifact_revision(
                self.scope_id,
                reference.family,
                reference.artifact_id,
                reference.revision,
            )
        except Exception as error:  # noqa: BLE001 - every read failure is a registration failure
            raise SourceLifecycleError("unreadable-artifact", reference.artifact_id) from error

    async def _require_citation(self, service: MemoryService, citation: MemoryCitation) -> None:
        try:
            memory = await service.revision(citation.memory_ref)
            if not any(
                item.entry_id == citation.entry_id and item.entry_version_id == citation.entry_version_id
                for item in memory.content.manifest.entries
            ):
                raise SourceLifecycleError("unreadable-citation", citation.entry_id)
        except SourceLifecycleError:
            raise
        except Exception as error:  # noqa: BLE001 - every read failure is a registration failure
            raise SourceLifecycleError("unreadable-citation", citation.entry_id) from error

    async def _reconcile_memory(
        self,
        service: MemoryService,
        changed: Sequence[_StoredNode],
        targets: Mapping[str, bool],
        reason: str,
    ) -> None:
        # Memory state is authoritative for read behaviour, so it moves before the
        # graph row does. A retry after an interrupted propagation therefore finds
        # the untouched graph and finishes only the transitions still missing.
        for node in changed:
            if node.family != MEMORY_FAMILY:
                continue
            await self._set_memory_available(service, node.citation, targets[node.node_id], reason)

    async def _set_memory_available(
        self,
        service: MemoryService,
        citation: MemoryCitation,
        available: bool,
        reason: str,
    ) -> bool:
        memory = await service.head(citation.memory_ref.artifact_id)
        state = _entry_state(memory, citation.entry_id)
        if state is None:
            raise SourceLifecycleError("unknown-memory-entry", citation.entry_id)
        if (state == "active") == available:
            return False
        version = await _entry_version(service, memory, citation.entry_id)
        if available:
            await service.reactivate(memory, entries=(version,), reason=reason)
        else:
            await service.forget(memory, entries=(version,), reason=reason)
        return True


def _validate_node_id(node_id: str) -> str:
    if not isinstance(node_id, str) or not node_id.strip() or node_id != node_id.strip():
        raise SourceLifecycleError("invalid-node-id", str(node_id))
    return node_id


def _validate_parents(node_id: str, parents: Sequence[str]) -> tuple[str, ...]:
    if isinstance(parents, str) or not isinstance(parents, Sequence):
        raise SourceLifecycleError("invalid-parents", node_id)
    values = tuple(parents)
    for parent in values:
        if not isinstance(parent, str) or not parent.strip() or parent != parent.strip():
            raise SourceLifecycleError("invalid-parents", node_id)
    if node_id in values:
        raise SourceLifecycleError("cycle", node_id)
    if len(set(values)) != len(values):
        raise SourceLifecycleError("duplicate-parent", node_id)
    if not values:
        raise SourceLifecycleError("missing-parents", node_id)
    return values


def _is_positive_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _find(nodes: Sequence[_StoredNode], node_id: str) -> _StoredNode | None:
    return next((item for item in nodes if item.node_id == node_id), None)


def _same_binding(
    node: _StoredNode,
    family: str,
    reference: str,
    parents: tuple[str, ...],
    policy: str,
) -> bool:
    return (
        node.family == family
        and node.reference == reference
        and frozenset(node.parents) == frozenset(parents)
        and node.policy == policy
    )


def _derived_availability(parents: Sequence[str], policy: str, states: Mapping[str, bool]) -> bool:
    support = tuple(states[parent] for parent in parents)
    if policy == "all":
        return all(support)
    return any(support)


def _propagate(nodes: Sequence[_StoredNode], source_id: str, available: bool) -> dict[str, bool]:
    """Re-evaluate every node after one Source transition, in registration order."""

    resolved: dict[str, bool] = {}
    for node in nodes:
        if node.family == SOURCE_FAMILY:
            resolved[node.node_id] = available if node.node_id == source_id else node.available
            continue
        resolved[node.node_id] = _derived_availability(node.parents, node.policy, resolved)
    return resolved


def _event_digest(event: SourceLifecycleEvent) -> str:
    payload = json.dumps(
        {
            "node_id": event.node_id,
            "revision": int(event.revision),
            "sequence": int(event.sequence),
            "available": bool(event.available),
            "reason": event.reason,
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _encode_reference(reference: SourceRef | MemoryCitation | ArtifactRef) -> str:
    return json.dumps(
        reference.model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )


def _encode_parents(parents: Sequence[str]) -> str:
    return json.dumps(list(parents), separators=(",", ":"), ensure_ascii=False)


def _decode_node(row: Mapping[str, Any]) -> _StoredNode:
    return _StoredNode(
        node_id=str(row["node_id"]),
        family=str(row["family"]),
        reference=str(row["reference"]),
        parents=tuple(json.loads(str(row["parents"]))),
        policy=str(row["policy"]),
        revision=int(row["revision"]),
        sequence=int(row["sequence"]),
        available=bool(row["available"]),
        event_digest=None if row["event_digest"] is None else str(row["event_digest"]),
        ordinal=int(row["ordinal"]),
    )


def _entry_state(memory: Memory, entry_id: str) -> Literal["active", "inactive"] | None:
    return next(
        (item.state for item in memory.content.manifest.entries if item.entry_id == entry_id),
        None,
    )


async def _entry_version(service: MemoryService, memory: Memory, entry_id: str) -> MemoryEntryVersion:
    entries = await service.entries(memory)
    version = next((item for item in entries if item.entry_id == entry_id), None)
    if version is None:
        raise SourceLifecycleError("unknown-memory-entry", entry_id)
    return version


__all__ = [
    "AUDIT_PURPOSE",
    "GENERATION_PURPOSE",
    "LifecycleApplication",
    "ScopedSourceLifecycleApplication",
    "SourceLifecycleError",
    "SourceLifecycleEvent",
    "SourceLifecycleRepository",
]
