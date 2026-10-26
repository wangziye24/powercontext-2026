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

"""Wire formats for sending prepared context as a full pack plus deltas.

A pack is a chunked, digest-addressed representation of one prepared context. A delta
keeps a prefix of an existing pack and only carries the chunks that follow it, so a
client that still holds the previous pack can rebuild the current context without the
sender re-shipping unchanged bytes.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
from dataclasses import dataclass
from typing import Any

PACK_SCHEMA = "powercontext.context-pack.v1"
DELTA_SCHEMA = "powercontext.context-pack-delta.v1"
DEFAULT_CHUNK_BYTES = 512

_PACK_FIELDS = frozenset({"schema", "items", "digest", "byte_count"})
_DELTA_FIELDS = frozenset({"schema", "base_digest", "target_digest", "prefix_items", "appended"})


class ContextPackError(Exception):
    """A pack, delta, or chunk cannot satisfy the wire contract."""


def _canonical(payload: Any) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _encode_item(item: bytes) -> str:
    return base64.b64encode(item).decode("ascii")


def _decode_items(raw: Any) -> tuple[bytes, ...]:
    if not isinstance(raw, list):
        raise ContextPackError("items must be a list of base64 strings")
    decoded: list[bytes] = []
    for value in raw:
        if not isinstance(value, str):
            raise ContextPackError("items must be base64 strings")
        try:
            decoded.append(base64.b64decode(value.encode("ascii"), validate=True))
        except (binascii.Error, UnicodeEncodeError, ValueError) as error:
            raise ContextPackError("item is not valid base64") from error
    return tuple(decoded)


def _digest_for(items: tuple[bytes, ...]) -> str:
    unsigned = {"schema": PACK_SCHEMA, "items": [_encode_item(item) for item in items]}
    return "sha256:" + hashlib.sha256(_canonical(unsigned)).hexdigest()


def _decode_object(data: bytes | str) -> dict[str, Any]:
    try:
        payload = json.loads(data)
    except (TypeError, ValueError) as error:
        raise ContextPackError("payload is not valid JSON") from error
    if not isinstance(payload, dict):
        raise ContextPackError("payload must be a JSON object")
    return payload


@dataclass(frozen=True, slots=True)
class ContextPack:
    """One digest-addressed, chunked snapshot of prepared context text."""

    items: tuple[bytes, ...]
    digest: str
    byte_count: int

    @classmethod
    def build(cls, items: tuple[bytes, ...]) -> ContextPack:
        """Assemble a pack from transport-ready byte chunks."""

        normalized = tuple(bytes(item) for item in items)
        digest = _digest_for(normalized)
        base = {"schema": PACK_SCHEMA, "items": [_encode_item(i) for i in normalized], "digest": digest}
        size = len(_canonical(base))
        while True:
            candidate = {**base, "byte_count": size}
            actual = len(_canonical(candidate))
            if actual == size:
                return cls(items=normalized, digest=digest, byte_count=size)
            size = actual

    @classmethod
    def from_content(cls, content: str, *, chunk_bytes: int = DEFAULT_CHUNK_BYTES) -> ContextPack:
        """Split text on character boundaries into chunks of at most ``chunk_bytes``."""

        if isinstance(chunk_bytes, bool) or not isinstance(chunk_bytes, int):
            raise ContextPackError("chunk_bytes must be a positive integer")
        if chunk_bytes < 1:
            raise ContextPackError("chunk_bytes must be a positive integer")
        chunks: list[bytes] = []
        pending: list[str] = []
        size = 0
        for character in content:
            encoded = character.encode("utf-8")
            if len(encoded) > chunk_bytes:
                raise ContextPackError("chunk_bytes cannot form a valid UTF-8 boundary")
            if size + len(encoded) > chunk_bytes:
                chunks.append("".join(pending).encode("utf-8"))
                pending = []
                size = 0
            pending.append(character)
            size += len(encoded)
        if pending:
            chunks.append("".join(pending).encode("utf-8"))
        return cls.build(tuple(chunks))

    def to_bytes(self) -> bytes:
        """Serialize the pack into its canonical wire form."""

        return _canonical(
            {
                "schema": PACK_SCHEMA,
                "items": [_encode_item(item) for item in self.items],
                "digest": self.digest,
                "byte_count": self.byte_count,
            }
        )

    @classmethod
    def from_bytes(cls, data: bytes | str) -> ContextPack:
        """Parse and verify a wire pack, rejecting any tampering."""

        payload = _decode_object(data)
        if set(payload) != _PACK_FIELDS:
            raise ContextPackError("pack must contain exactly schema, items, digest, byte_count")
        if payload["schema"] != PACK_SCHEMA:
            raise ContextPackError("unsupported pack schema")
        digest = payload["digest"]
        if not isinstance(digest, str):
            raise ContextPackError("pack digest must be a string")
        byte_count = payload["byte_count"]
        if type(byte_count) is not int:
            raise ContextPackError("pack byte_count must be an integer")
        rebuilt = cls.build(_decode_items(payload["items"]))
        if rebuilt.digest != digest or rebuilt.byte_count != byte_count:
            raise ContextPackError("pack digest or byte_count does not match its content")
        return rebuilt


class ContextPackDelta:
    """A prefix-preserving patch that turns one pack into a later pack."""

    __slots__ = ("appended", "base_digest", "prefix_items", "target_digest")

    def __init__(
        self,
        base_digest: str,
        target_digest: str,
        prefix_items: int,
        appended: tuple[bytes, ...],
    ) -> None:
        self.base_digest = base_digest
        self.target_digest = target_digest
        self.prefix_items = prefix_items
        self.appended = tuple(bytes(item) for item in appended)

    @classmethod
    def between(cls, base: ContextPack, target: ContextPack) -> ContextPackDelta:
        """Keep the longest shared prefix of the two packs and append the rest."""

        shared = 0
        limit = min(len(base.items), len(target.items))
        while shared < limit and base.items[shared] == target.items[shared]:
            shared += 1
        return cls(base.digest, target.digest, shared, target.items[shared:])

    def to_bytes(self) -> bytes:
        """Serialize the delta into its canonical wire form."""

        return _canonical(
            {
                "schema": DELTA_SCHEMA,
                "base_digest": self.base_digest,
                "target_digest": self.target_digest,
                "prefix_items": self.prefix_items,
                "appended": [_encode_item(item) for item in self.appended],
            }
        )

    @property
    def byte_count(self) -> int:
        """Serialized wire size of this delta, excluding any extra field."""

        return len(self.to_bytes())

    @classmethod
    def from_bytes(cls, data: bytes | str) -> ContextPackDelta:
        """Parse and validate a wire delta."""

        payload = _decode_object(data)
        if set(payload) != _DELTA_FIELDS:
            raise ContextPackError(
                "delta must contain exactly schema, base_digest, target_digest, prefix_items, appended",
            )
        if payload["schema"] != DELTA_SCHEMA:
            raise ContextPackError("unsupported delta schema")
        for field in ("base_digest", "target_digest"):
            if not isinstance(payload[field], str):
                raise ContextPackError(f"delta {field} must be a string")
        prefix_items = payload["prefix_items"]
        if type(prefix_items) is not int:
            raise ContextPackError("delta prefix_items must be an integer")
        if prefix_items < 0:
            raise ContextPackError("delta prefix_items must not be negative")
        return cls(
            payload["base_digest"],
            payload["target_digest"],
            prefix_items,
            _decode_items(payload["appended"]),
        )

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, ContextPackDelta):
            return NotImplemented
        return (
            self.base_digest == other.base_digest
            and self.target_digest == other.target_digest
            and self.prefix_items == other.prefix_items
            and self.appended == other.appended
        )

    def __hash__(self) -> int:
        return hash((self.base_digest, self.target_digest, self.prefix_items, self.appended))

    def __repr__(self) -> str:
        return (
            f"ContextPackDelta(base_digest={self.base_digest!r}, target_digest={self.target_digest!r},"
            f" prefix_items={self.prefix_items!r}, appended=<{len(self.appended)} items>)"
        )


def apply_context_pack_delta(base: ContextPack, delta: ContextPackDelta) -> ContextPack:
    """Rebuild the target pack from a base pack and one delta."""

    if base.digest != delta.base_digest:
        raise ContextPackError("delta base digest does not match the supplied pack")
    if delta.prefix_items > len(base.items):
        raise ContextPackError("delta prefix_items exceeds the base pack length")
    rebuilt = ContextPack.build((*base.items[: delta.prefix_items], *delta.appended))
    if rebuilt.digest != delta.target_digest:
        raise ContextPackError("delta target digest does not match the rebuilt pack")
    return rebuilt


__all__ = [
    "DEFAULT_CHUNK_BYTES",
    "DELTA_SCHEMA",
    "PACK_SCHEMA",
    "ContextPack",
    "ContextPackDelta",
    "ContextPackError",
    "apply_context_pack_delta",
]
