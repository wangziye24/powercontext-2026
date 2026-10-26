"""seekdb access layer for the SDD context store.

The embedded seekdb runtime is started through PowerContext's own seekdb profile,
so the module speaks the same dialect and connection handling as the server that
keeps the task state. Every statement is recorded so the submission can show the
real database traffic that produced a development context.
"""

from __future__ import annotations

import time
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, AsyncIterator, Mapping

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from .schema import TABLES


@dataclass(frozen=True)
class SqlRecord:
    """One executed statement with its result size and latency."""

    sql: str
    params: str
    rows: int
    duration_ms: float


@dataclass
class SqlRecorder:
    """Collects the statements executed against seekdb."""

    records: list[SqlRecord] = field(default_factory=list)

    def add(self, sql: str, params: Mapping[str, Any], rows: int, duration_ms: float) -> None:
        self.records.append(
            SqlRecord(
                sql=" ".join(sql.split()),
                params=", ".join(f"{key}={value!r}" for key, value in sorted(params.items())),
                rows=rows,
                duration_ms=round(duration_ms, 3),
            )
        )

    def render(self) -> str:
        """Render the recorded traffic as a reviewable transcript."""

        lines: list[str] = []
        for index, record in enumerate(self.records, start=1):
            lines.append(f"[{index:03d}] {record.sql}")
            if record.params:
                lines.append(f"      params: {record.params}")
            lines.append(f"      rows={record.rows} duration_ms={record.duration_ms}")
        return "\n".join(lines)


@asynccontextmanager
async def open_store(path: Path) -> AsyncIterator[tuple[AsyncConnection, SqlRecorder]]:
    """Open the embedded seekdb store at ``path`` and yield a live connection."""

    from powercontext.builtin.persistence.seekdb.profile import SeekDBConfig, SeekDBProfile

    config = SeekDBConfig(path=Path(path), database="test")
    recorder = SqlRecorder()
    async with SeekDBProfile.open(config, tables=TABLES) as profile:
        async with profile.database.transaction() as connection:
            yield connection, recorder


async def execute(
    connection: AsyncConnection,
    recorder: SqlRecorder,
    sql: str,
    params: Mapping[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Execute one statement and record it in the transcript."""

    bound = dict(params or {})
    started = time.perf_counter()
    result = await connection.execute(text(sql), bound)
    if result.returns_rows:
        rows = [dict(row) for row in result.mappings().all()]
    else:
        rows = []
        result.close()
    recorder.add(sql, bound, len(rows), (time.perf_counter() - started) * 1000)
    return rows


async def execute_many(
    connection: AsyncConnection,
    recorder: SqlRecorder,
    sql: str,
    rows: list[Mapping[str, Any]],
) -> int:
    """Execute one statement per row and record every call."""

    for row in rows:
        await execute(connection, recorder, sql, row)
    return len(rows)
