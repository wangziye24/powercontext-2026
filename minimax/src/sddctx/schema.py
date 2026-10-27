"""Relational schema of the SDD context store.

Every table lives in one embedded seekdb instance. Text primary keys are used on
purpose: they stay stable across re-imports, so a specification that changes keeps
its identity while its body and hash are refreshed.
"""

from __future__ import annotations

from sqlalchemy import Column, Integer, MetaData, String, Table, Text

metadata = MetaData()

documents = Table(
    "sdd_documents",
    metadata,
    Column("id", String(320), primary_key=True),
    Column("path", String(512), nullable=False),
    Column("kind", String(32), nullable=False),
    Column("sha256", String(64), nullable=False),
    Column("size_bytes", Integer, nullable=False),
    Column("content", Text, nullable=False),
    Column("updated_at", String(32), nullable=False),
)

specs = Table(
    "sdd_specs",
    metadata,
    Column("spec_id", String(64), primary_key=True),
    Column("title", String(512), nullable=False),
    Column("body", Text, nullable=False),
    Column("tags", String(512), nullable=False),
    Column("source_path", String(512), nullable=False),
    Column("source_hash", String(64), nullable=False),
    Column("updated_at", String(32), nullable=False),
)

code_symbols = Table(
    "sdd_code_symbols",
    metadata,
    Column("id", String(320), primary_key=True),
    Column("path", String(512), nullable=False),
    Column("symbol", String(256), nullable=False),
    Column("kind", String(32), nullable=False),
    Column("line_start", Integer, nullable=False),
    Column("line_end", Integer, nullable=False),
    Column("signature", String(1024), nullable=False),
    Column("doc", Text, nullable=False),
    Column("body", Text, nullable=False),
    Column("source_hash", String(64), nullable=False),
    Column("updated_at", String(32), nullable=False),
)

tests = Table(
    "sdd_tests",
    metadata,
    Column("id", String(320), primary_key=True),
    Column("path", String(512), nullable=False),
    Column("name", String(256), nullable=False),
    Column("purpose", Text, nullable=False),
    Column("covers", String(512), nullable=False),
    Column("target_feature", String(256), nullable=False),
    Column("body", Text, nullable=False),
    Column("source_hash", String(64), nullable=False),
    Column("updated_at", String(32), nullable=False),
)

ingest_runs = Table(
    "sdd_ingest_runs",
    metadata,
    Column("id", String(64), primary_key=True),
    Column("started_at", String(32), nullable=False),
    Column("finished_at", String(32), nullable=False),
    Column("root", String(512), nullable=False),
    Column("status", String(32), nullable=False),
    Column("counts", Text, nullable=False),
)

TABLES = (documents, specs, code_symbols, tests, ingest_runs)
