"""Import engineering material into the seekdb store.

The importer understands three kinds of material:

* **specifications** - Markdown files whose level two headings carry a stable
  identifier such as ``SPEC-001``.
* **code** - Python modules whose functions, classes and module level constants
  are indexed with their line range and docstring.
* **tests** - Python test functions indexed with the specification entries they
  claim to cover.

Every imported record keeps the path and the content hash of its origin, so a
later re-import can prove that a specification changed.
"""

from __future__ import annotations

import ast
import hashlib
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .db import SqlRecorder, execute, execute_many
from .schema import code_symbols, documents, ingest_runs, specs, tests

SPEC_HEADING = re.compile(r"^##\s+(?P<spec_id>SPEC-[0-9A-Za-z._-]+)\s*(?P<title>.*?)\s*$")
SPEC_REFERENCE = re.compile(r"SPEC-[0-9A-Za-z._-]+")
SPEC_IN_NAME = re.compile(r"\bspec[_-](?P<number>[0-9]+)\b", re.IGNORECASE)
MODULE_CONSTANTS = ("VALID_", "ALLOWED_", "SUPPORTED_", "DEFAULT_", "MAX_", "MIN_")


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


@dataclass
class ImportSummary:
    """Result of one import run."""

    run_id: str
    root: str
    documents: int = 0
    specs: int = 0
    code_symbols: int = 0
    tests: int = 0
    changed_files: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "root": self.root,
            "documents": self.documents,
            "specs": self.specs,
            "code_symbols": self.code_symbols,
            "tests": self.tests,
            "changed_files": sorted(self.changed_files),
        }


def _parse_specs(path: Path, relative: str, content: str) -> list[dict[str, Any]]:
    """Split a specification document into its numbered entries."""

    entries: list[dict[str, Any]] = []
    current_id: str | None = None
    current_title = ""
    current_lines: list[str] = []

    def flush() -> None:
        if current_id is None:
            return
        body = "\n".join(current_lines).strip()
        tags = sorted({match for match in SPEC_REFERENCE.findall(body)})
        entries.append(
            {
                "spec_id": current_id,
                "title": current_title or current_id,
                "body": body,
                "tags": ",".join(tags),
                "source_path": relative,
                "source_hash": _hash(body),
                "updated_at": _now(),
            }
        )

    for line in content.splitlines():
        heading = SPEC_HEADING.match(line)
        if heading:
            flush()
            current_id = heading.group("spec_id")
            current_title = heading.group("title")
            current_lines = []
            continue
        if current_id is not None:
            current_lines.append(line)
    flush()
    return entries


def _parse_code(path: Path, relative: str, content: str) -> list[dict[str, Any]]:
    """Index the public symbols of a Python module."""

    tree = ast.parse(content)
    lines = content.splitlines()
    symbols: list[dict[str, Any]] = []

    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            kind = "async_function" if isinstance(node, ast.AsyncFunctionDef) else "function"
            body = "\n".join(lines[node.lineno - 1 : node.end_lineno])
            signature = lines[node.lineno - 1].strip()
            symbols.append(
                {
                    "id": f"{relative}::{node.name}",
                    "path": relative,
                    "symbol": node.name,
                    "kind": kind,
                    "line_start": node.lineno,
                    "line_end": node.end_lineno or node.lineno,
                    "signature": signature[:1024],
                    "doc": ast.get_docstring(node) or "",
                    "body": body,
                    "source_hash": _hash(body),
                    "updated_at": _now(),
                }
            )
        elif isinstance(node, ast.ClassDef):
            body = "\n".join(lines[node.lineno - 1 : node.end_lineno or node.lineno])
            symbols.append(
                {
                    "id": f"{relative}::{node.name}",
                    "path": relative,
                    "symbol": node.name,
                    "kind": "class",
                    "line_start": node.lineno,
                    "line_end": node.end_lineno or node.lineno,
                    "signature": lines[node.lineno - 1].strip()[:1024],
                    "doc": ast.get_docstring(node) or "",
                    "body": body,
                    "source_hash": _hash(body),
                    "updated_at": _now(),
                }
            )
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                name = getattr(target, "id", None)
                if not isinstance(name, str) or not name.startswith(MODULE_CONSTANTS):
                    continue
                body = "\n".join(lines[node.lineno - 1 : (node.end_lineno or node.lineno)])
                symbols.append(
                    {
                        "id": f"{relative}::{name}",
                        "path": relative,
                        "symbol": name,
                        "kind": "constant",
                        "line_start": node.lineno,
                        "line_end": node.end_lineno or node.lineno,
                        "signature": body[:1024],
                        "doc": "",
                        "body": body,
                        "source_hash": _hash(body),
                        "updated_at": _now(),
                    }
                )
    return symbols


def _parse_tests(path: Path, relative: str, content: str) -> list[dict[str, Any]]:
    """Index the test functions of a test module."""

    tree = ast.parse(content)
    lines = content.splitlines()
    found: list[dict[str, Any]] = []
    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if not node.name.startswith("test_"):
            continue
        body = "\n".join(lines[node.lineno - 1 : node.end_lineno or node.lineno])
        doc = ast.get_docstring(node) or ""
        covered = set(SPEC_REFERENCE.findall(f"{doc}\n{body}\n{node.name}"))
        for match in SPEC_IN_NAME.finditer(node.name):
            covered.add(f"SPEC-{match.group('number')}")
        covered = sorted(covered)
        found.append(
            {
                "id": f"{relative}::{node.name}",
                "path": relative,
                "name": node.name,
                "purpose": doc.splitlines()[0] if doc else node.name.replace("_", " "),
                "covers": ",".join(covered),
                "target_feature": covered[0] if covered else "",
                "body": body,
                "source_hash": _hash(body),
                "updated_at": _now(),
            }
        )
    return found


def _collect_files(root: Path) -> tuple[list[Path], list[Path], list[Path]]:
    spec_files = sorted(p for p in root.glob("specs/**/*.md") if p.is_file())
    code_files = sorted(p for p in root.glob("src/**/*.py") if p.is_file())
    test_files = sorted(p for p in root.glob("tests/**/*.py") if p.is_file())
    return spec_files, code_files, test_files


async def import_material(
    connection: Any,
    recorder: SqlRecorder,
    root: Path,
    *,
    run_id: str | None = None,
) -> ImportSummary:
    """Import the specifications, code and tests found under ``root``."""

    root = Path(root).resolve()
    started = _now()
    run_id = run_id or f"run-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}"
    summary = ImportSummary(run_id=run_id, root=str(root))

    spec_files, code_files, test_files = _collect_files(root)

    document_rows: list[dict[str, Any]] = []
    spec_rows: list[dict[str, Any]] = []
    code_rows: list[dict[str, Any]] = []
    test_rows: list[dict[str, Any]] = []

    for path in spec_files:
        relative = path.relative_to(root).as_posix()
        content = path.read_text(encoding="utf-8")
        document_rows.append(
            {
                "id": f"doc::{relative}",
                "path": relative,
                "kind": "spec",
                "sha256": _hash(content),
                "size_bytes": len(content.encode("utf-8")),
                "content": content,
                "updated_at": _now(),
            }
        )
        entries = _parse_specs(path, relative, content)
        previous = await execute(
            connection,
            recorder,
            f"SELECT spec_id, source_hash FROM {specs.name} WHERE source_path = :source_path",
            {"source_path": relative},
        )
        for entry in entries:
            known = {row["spec_id"]: row["source_hash"] for row in previous}
            if known.get(entry["spec_id"]) != entry["source_hash"]:
                summary.changed_files.append(f"{relative}#{entry['spec_id']}")
        spec_rows.extend(entries)

    for path in code_files:
        relative = path.relative_to(root).as_posix()
        content = path.read_text(encoding="utf-8")
        document_rows.append(
            {
                "id": f"doc::{relative}",
                "path": relative,
                "kind": "code",
                "sha256": _hash(content),
                "size_bytes": len(content.encode("utf-8")),
                "content": content,
                "updated_at": _now(),
            }
        )
        code_rows.extend(_parse_code(path, relative, content))

    for path in test_files:
        relative = path.relative_to(root).as_posix()
        content = path.read_text(encoding="utf-8")
        document_rows.append(
            {
                "id": f"doc::{relative}",
                "path": relative,
                "kind": "test",
                "sha256": _hash(content),
                "size_bytes": len(content.encode("utf-8")),
                "content": content,
                "updated_at": _now(),
            }
        )
        test_rows.extend(_parse_tests(path, relative, content))

    for row in document_rows:
        await execute(
            connection,
            recorder,
            f"DELETE FROM {documents.name} WHERE id = :id",
            {"id": row["id"]},
        )
    await execute_many(
        connection,
        recorder,
        f"INSERT INTO {documents.name}"
        " (id, path, kind, sha256, size_bytes, content, updated_at)"
        " VALUES (:id, :path, :kind, :sha256, :size_bytes, :content, :updated_at)",
        document_rows,
    )
    summary.documents = len(document_rows)

    for row in spec_rows:
        await execute(connection, recorder, f"DELETE FROM {specs.name} WHERE spec_id = :spec_id", {"spec_id": row["spec_id"]})
    await execute_many(
        connection,
        recorder,
        f"INSERT INTO {specs.name}"
        " (spec_id, title, body, tags, source_path, source_hash, updated_at)"
        " VALUES (:spec_id, :title, :body, :tags, :source_path, :source_hash, :updated_at)",
        spec_rows,
    )
    summary.specs = len(spec_rows)

    for row in code_rows:
        await execute(connection, recorder, f"DELETE FROM {code_symbols.name} WHERE id = :id", {"id": row["id"]})
    await execute_many(
        connection,
        recorder,
        f"INSERT INTO {code_symbols.name}"
        " (id, path, symbol, kind, line_start, line_end, signature, doc, body, source_hash, updated_at)"
        " VALUES (:id, :path, :symbol, :kind, :line_start, :line_end, :signature, :doc, :body,"
        " :source_hash, :updated_at)",
        code_rows,
    )
    summary.code_symbols = len(code_rows)

    for row in test_rows:
        await execute(connection, recorder, f"DELETE FROM {tests.name} WHERE id = :id", {"id": row["id"]})
    await execute_many(
        connection,
        recorder,
        f"INSERT INTO {tests.name}"
        " (id, path, name, purpose, covers, target_feature, body, source_hash, updated_at)"
        " VALUES (:id, :path, :name, :purpose, :covers, :target_feature, :body, :source_hash,"
        " :updated_at)",
        test_rows,
    )
    summary.tests = len(test_rows)

    await execute(
        connection,
        recorder,
        f"INSERT INTO {ingest_runs.name}"
        " (id, started_at, finished_at, root, status, counts)"
        " VALUES (:id, :started_at, :finished_at, :root, :status, :counts)",
        {
            "id": run_id,
            "started_at": started,
            "finished_at": _now(),
            "root": str(root),
            "status": "ok",
            "counts": str(summary.as_dict()).replace("'", '"'),
        },
    )
    return summary
