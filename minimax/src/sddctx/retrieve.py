"""Select the material a development task needs out of the seekdb store.

The selection runs in two steps. The database does the matching: every
candidate row is filtered with parameterised ``LIKE`` predicates, so the
transcript shows the real statements that produced the context. The remaining
ranking happens in the module, where the specification entries that the task
mentions are linked to the code symbols and the tests that reference them.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .db import SqlRecorder, execute
from .schema import code_symbols, documents, specs, tests

SPEC_REFERENCE = re.compile(r"SPEC-[0-9A-Za-z._-]+")
SPEC_RANGE = re.compile(r"(SPEC-[0-9A-Za-z._-]+)\s*[~～]\s*(SPEC-[0-9A-Za-z._-]+)")
ASCII_WORD = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
CJK_RUN = re.compile(r"[一-鿿]{2,}")

STOPWORDS = {
    "the", "and", "for", "with", "that", "this", "from", "into", "keep", "must",
    "add", "adds", "while", "also", "not", "same", "existing", "behavior",
    "behaviour", "保持", "原有", "行为", "不变", "同时", "本次", "任务", "以及",
    "需要", "必须", "可以", "通过", "进行", "实现", "修改", "代码", "保证",
    "增加", "添加", "支持", "使用", "完成", "功能", "全部", "所有",
}


def extract_keywords(task: str) -> list[str]:
    """Return the searchable terms of a task description.

    Latin words are kept as they are. Chinese runs are expanded into character
    bigrams, which keeps the matching dependency free while still reaching the
    words used in the specifications.
    """

    terms: list[str] = []

    def add(term: str) -> None:
        cleaned = term.strip().lower()
        if len(cleaned) < 2 or cleaned in STOPWORDS:
            return
        if cleaned not in terms:
            terms.append(cleaned)

    for match in SPEC_REFERENCE.findall(task):
        add(match)
    for match in ASCII_WORD.findall(task):
        add(match)
    for run in CJK_RUN.findall(task):
        if len(run) <= 4:
            add(run)
        for index in range(len(run) - 1):
            add(run[index : index + 2])
    return terms


def mentioned_specifications(task: str) -> list[str]:
    """Return the specification identifiers a task names, ranges expanded.

    A task may write ``SPEC-001 ~ SPEC-003``; every entry of that range is
    applicable to the task and must show up in the selected context.
    """

    found: set[str] = set(SPEC_REFERENCE.findall(task))
    for start, end in SPEC_RANGE.findall(task):
        start_match = re.fullmatch(r"SPEC-(\d+)", start)
        end_match = re.fullmatch(r"SPEC-(\d+)", end)
        if not start_match or not end_match:
            continue
        first = int(start_match.group(1))
        last = int(end_match.group(1))
        if last < first:
            first, last = last, first
        for number in range(first, last + 1):
            found.add(f"SPEC-{number:0{len(start_match.group(1))}d}")
    return sorted(found)


def _like_clause(column: str, terms: list[str], recorder: SqlRecorder) -> tuple[str, dict[str, Any]]:
    """Build a parameterised ``LIKE`` clause for one column."""

    parts: list[str] = []
    params: dict[str, Any] = {}
    for index, term in enumerate(terms):
        key = f"kw_{column}_{index}"
        parts.append(f"{column} LIKE :{key}")
        params[key] = f"%{term}%"
    return " OR ".join(parts), params


async def select_material(
    connection: Any,
    recorder: SqlRecorder,
    task: str,
    *,
    target_specs: list[str] | None = None,
    max_specs: int = 10,
    max_code: int = 12,
    max_tests: int = 30,
) -> dict[str, Any]:
    """Return the specifications, code symbols and tests that fit ``task``."""

    terms = extract_keywords(task)
    mentioned_specs = mentioned_specifications(task)

    spec_terms = terms + mentioned_specs
    spec_where, spec_params = _like_clause("spec_id", spec_terms, recorder)
    title_where, title_params = _like_clause("title", spec_terms, recorder)
    body_where, body_params = _like_clause("body", spec_terms, recorder)
    spec_rows = await execute(
        connection,
        recorder,
        f"SELECT spec_id, title, body, tags, source_path, source_hash, updated_at"
        f" FROM {specs.name} WHERE {spec_where} OR {title_where} OR {body_where}",
        {**spec_params, **title_params, **body_params},
    )

    code_terms = terms + mentioned_specs
    code_clauses: list[str] = []
    code_params: dict[str, Any] = {}
    for column in ("symbol", "path", "doc", "signature"):
        clause, params = _like_clause(column, code_terms, recorder)
        code_clauses.append(clause)
        code_params.update(params)
    spec_reference_clauses: list[str] = []
    for index, spec_id in enumerate(mentioned_specs):
        key = f"spec_ref_{index}"
        spec_reference_clauses.append(f"doc LIKE :{key}")
        code_params[key] = f"%{spec_id}%"
    where = " OR ".join(code_clauses + spec_reference_clauses) or "1 = 0"
    code_rows = await execute(
        connection,
        recorder,
        f"SELECT id, path, symbol, kind, line_start, line_end, signature, doc, body, source_hash"
        f" FROM {code_symbols.name} WHERE {where}",
        code_params,
    )

    test_clauses: list[str] = []
    test_params: dict[str, Any] = {}
    for column in ("name", "path", "purpose", "covers"):
        clause, params = _like_clause(column, terms, recorder)
        test_clauses.append(clause)
        test_params.update(params)
    for index, spec_id in enumerate(mentioned_specs):
        key = f"test_spec_{index}"
        test_clauses.append(f"covers LIKE :{key}")
        test_params[key] = f"%{spec_id}%"
    test_where = " OR ".join(test_clauses) or "1 = 0"
    test_rows = await execute(
        connection,
        recorder,
        f"SELECT id, path, name, purpose, covers, target_feature, body, source_hash"
        f" FROM {tests.name} WHERE {test_where}",
        test_params,
    )

    selected_specs = _rank_specs(spec_rows, terms, mentioned_specs)[:max_specs]
    selected_spec_ids = {row["spec_id"] for row in selected_specs}
    effective_targets = sorted(set(target_specs or []) & selected_spec_ids) or sorted(selected_spec_ids)

    selected_code = _rank_code(code_rows, terms, selected_spec_ids)[:max_code]
    selected_tests = _rank_tests(test_rows, terms, selected_spec_ids, effective_targets)[:max_tests]
    conventions = await _load_conventions(connection, recorder, selected_code)
    related_files = await _load_related_files(connection, recorder, selected_code)
    missing_errors = _find_missing_error_types(selected_specs, conventions, selected_code)

    return {
        "task": task,
        "keywords": terms,
        "mentioned_specs": mentioned_specs,
        "target_specs": effective_targets,
        "specifications": selected_specs,
        "code": selected_code,
        "tests": selected_tests,
        "conventions": conventions,
        "related_files": related_files,
        "missing_error_types": missing_errors,
    }


ERROR_TYPE = re.compile(r"\b([A-Z][A-Za-z0-9]*(?:Error|Exception))\b")


def _find_missing_error_types(
    specs_rows: list[dict[str, Any]],
    conventions: list[dict[str, Any]],
    code_rows: list[dict[str, Any]],
) -> list[dict[str, str]]:
    """Return the error types a specification requires but the project lacks.

    A rule that says "raise InvalidTierError" needs that type to exist. When the
    project does not define it yet, the change has to add it, and the assistant
    has to be told about it explicitly.
    """

    known = {item["symbol"] for item in conventions}
    known |= {row["symbol"] for row in code_rows}
    mentioned: dict[str, str] = {}
    for spec in specs_rows:
        for name in ERROR_TYPE.findall(f"{spec['title']} {spec['body']}"):
            if name not in known and name not in mentioned:
                mentioned[name] = spec["spec_id"]
    return [
        {
            "name": name,
            "required_by": spec_id,
            "hint": "该类型在工程中尚不存在，必须在本次修改中新增，并沿用同包错误类型的基类与命名风格。",
        }
        for name, spec_id in sorted(mentioned.items())
    ]


async def _load_conventions(
    connection: Any,
    recorder: SqlRecorder,
    code_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Return the error types the touched package already defines.

    A new rule often needs a new error type. Showing the existing ones keeps the
    generated change consistent with the conventions of the package.
    """

    directories = sorted({str(Path(row["path"]).parent).replace("\\", "/") for row in code_rows})
    if not directories:
        return []
    clauses: list[str] = []
    params: dict[str, Any] = {}
    for index, directory in enumerate(directories):
        key = f"dir_{index}"
        clauses.append(f"path LIKE :{key}")
        params[key] = f"{directory}/%"
    rows = await execute(
        connection,
        recorder,
        f"SELECT symbol, path, kind, doc, body, signature FROM {code_symbols.name}"
        f" WHERE kind = 'class' AND symbol LIKE '%Error' AND ({' OR '.join(clauses)})"
        " ORDER BY path, symbol",
        params,
    )
    return [
        {
            "symbol": row["symbol"],
            "path": row["path"],
            "kind": row["kind"],
            "doc": row["doc"],
            "body": row["body"],
        }
        for row in rows
    ]


async def _load_related_files(
    connection: Any,
    recorder: SqlRecorder,
    code_rows: list[dict[str, Any]],
    *,
    limit: int = 8,
) -> list[dict[str, Any]]:
    """Return the modules of the touched package with their full text.

    A change often has to follow the layout of the package, for example the
    import block of the edited module or the package exports. The full text is
    read from the imported documents, because those files carry no indexed
    symbol of their own.
    """

    directories = sorted({str(Path(row["path"]).parent).replace("\\", "/") for row in code_rows})
    if not directories:
        return []
    selected = {row["path"] for row in code_rows}
    clauses: list[str] = []
    params: dict[str, Any] = {}
    for index, directory in enumerate(directories):
        key = f"doc_dir_{index}"
        clauses.append(f"path LIKE :{key}")
        params[key] = f"{directory}/%"
    rows = await execute(
        connection,
        recorder,
        f"SELECT path, kind, content FROM {documents.name}"
        f" WHERE kind = 'code' AND ({' OR '.join(clauses)}) ORDER BY path",
        params,
    )
    files = [
        {
            "path": row["path"],
            "kind": row["kind"],
            "content": row["content"],
            "selected": row["path"] in selected,
        }
        for row in rows
    ]
    selected_files = [item for item in files if item["selected"]]
    other_files = [item for item in files if not item["selected"]]
    return (selected_files + other_files)[:limit]


def _hit_count(text: str, terms: list[str]) -> int:
    lowered = text.lower()
    return sum(1 for term in terms if term in lowered)


def _rank_specs(rows: list[dict[str, Any]], terms: list[str], mentioned: list[str]) -> list[dict[str, Any]]:
    ranked: list[dict[str, Any]] = []
    for row in rows:
        score = _hit_count(f"{row['title']} {row['body']}", terms)
        if row["spec_id"] in mentioned:
            score += 5
        if row["spec_id"] in row["tags"].split(","):
            score += 1
        ranked.append(
            {
                "spec_id": row["spec_id"],
                "title": row["title"],
                "body": row["body"],
                "source_path": row["source_path"],
                "source_hash": row["source_hash"],
                "score": score,
            }
        )
    ranked.sort(key=lambda item: (-item["score"], item["spec_id"]))
    return [item for item in ranked if item["score"] > 0]


def _rank_code(rows: list[dict[str, Any]], terms: list[str], spec_ids: set[str]) -> list[dict[str, Any]]:
    ranked: list[dict[str, Any]] = []
    for row in rows:
        score = _hit_count(f"{row['symbol']} {row['path']} {row['doc']} {row['signature']}", terms)
        referenced = sorted({match for match in SPEC_REFERENCE.findall(row["doc"] or "")})
        score += 2 * len(set(referenced) & spec_ids)
        ranked.append(
            {
                "path": row["path"],
                "symbol": row["symbol"],
                "kind": row["kind"],
                "line_start": row["line_start"],
                "line_end": row["line_end"],
                "signature": row["signature"],
                "doc": row["doc"],
                "body": row["body"],
                "covers_specs": referenced,
                "source_hash": row["source_hash"],
                "score": score,
            }
        )
    ranked.sort(key=lambda item: (-item["score"], item["path"], item["symbol"]))
    return [item for item in ranked if item["score"] > 0]


def _rank_tests(
    rows: list[dict[str, Any]],
    terms: list[str],
    spec_ids: set[str],
    target_specs: list[str],
) -> list[dict[str, Any]]:
    ranked: list[dict[str, Any]] = []
    targets = set(target_specs)
    for row in rows:
        score = _hit_count(f"{row['name']} {row['path']} {row['purpose']}", terms)
        covered = {value for value in row["covers"].split(",") if value}
        score += 3 * len(covered & spec_ids)
        role = "target" if covered & targets else "regression"
        if role == "target":
            score += 5
        ranked.append(
            {
                "path": row["path"],
                "name": row["name"],
                "purpose": row["purpose"],
                "covers": sorted(covered),
                "target_feature": row["target_feature"],
                "body": row["body"],
                "source_hash": row["source_hash"],
                "role": role,
                "score": score,
            }
        )
    ranked.sort(key=lambda item: (-item["score"], item["path"], item["name"]))
    return [item for item in ranked if item["score"] > 0]
