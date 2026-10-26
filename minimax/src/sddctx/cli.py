"""Command line entry point of the SDD context module.

    sddctx ingest   import specifications, code and tests into seekdb
    sddctx context  select the material a development task needs
    sddctx apply    let MiniMax perform the code change with that context
    sddctx verify   run the target tests and the regression tests
    sddctx state    read or write the task state kept in PowerContext
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import config
from .context import build_context, render_prompt, write_context
from .db import open_store
from .ingest import import_material
from .minimax_client import MiniMaxClient, MiniMaxError, save_call
from .patching import PatchError, apply_unified_diff, extract_diff
from .retrieve import select_material
from .taskstate import PowerContextState, TaskStateError

DEFAULT_TASK = (
    "在订单筛选中增加客户等级过滤（customer_tier），"
    "同时保持 SPEC-001 ~ SPEC-003、SPEC-005 ~ SPEC-007 描述的原有行为完全不变。"
)


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")


def _default_root() -> Path:
    return config.package_root() / "sample-project"


def _artifacts(sub: str) -> Path:
    target = config.artifacts_dir() / sub
    target.mkdir(parents=True, exist_ok=True)
    return target


def _write_sql(recorder: Any, name: str) -> Path:
    target = _artifacts("db") / f"{name}-{_stamp()}.sql.txt"
    target.write_text(recorder.render() + "\n", encoding="utf-8")
    return target


def _task_key(task: str) -> str:
    return "task-" + str(abs(hash(task)) % (10**10))


async def _select(
    connection: Any,
    recorder: Any,
    task: str,
    target_specs: list[str] | None = None,
) -> dict[str, Any]:
    return await select_material(connection, recorder, task, target_specs=target_specs)


def _target_spec_ids(context: dict[str, Any]) -> set[str]:
    return set(context.get("target_specs") or [])


def _split_tests(context: dict[str, Any]) -> tuple[list[str], list[str]]:
    """Split the selected tests into the target test files and the rest."""

    target_specs = _target_spec_ids(context)
    target: list[str] = []
    others: list[str] = []
    for item in context["tests"]:
        if set(item["covers"]) & target_specs:
            target.append(item["path"])
        else:
            others.append(item["path"])
    return sorted(set(target)), sorted(set(others))


def _run_pytest(root: Path, test_paths: list[str], label: str) -> dict[str, Any]:
    """Run pytest and capture the command, the exit code and the output."""

    command = [sys.executable, "-m", "pytest", "-q", *test_paths]
    started = _now()
    completed = subprocess.run(
        command,
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    output = completed.stdout + completed.stderr
    log_path = _artifacts("logs") / f"pytest-{label}-{_stamp()}.log"
    log_path.write_text(
        f"$ {' '.join(command)}\n"
        f"# cwd: {root}\n"
        f"# started_at: {started}\n"
        f"# finished_at: {_now()}\n"
        f"# exit_code: {completed.returncode}\n"
        f"# git_describe: {_git_revision(root)}\n\n"
        f"{output}",
        encoding="utf-8",
    )
    return {
        "label": label,
        "command": " ".join(command),
        "exit_code": completed.returncode,
        "passed": completed.returncode == 0,
        "log": str(log_path),
        "output_tail": output.strip().splitlines()[-12:],
    }


def _git_revision(root: Path) -> str:
    completed = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "--short", "HEAD"],
        capture_output=True,
        text=True,
        check=False,
    )
    return completed.stdout.strip() or "not-a-git-repository"


def _git_diff(root: Path, name: str) -> Path | None:
    completed = subprocess.run(
        ["git", "-C", str(root), "diff"],
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0 or not completed.stdout.strip():
        return None
    target = _artifacts("diff") / f"{name}.patch"
    target.write_text(completed.stdout, encoding="utf-8")
    return target


# --------------------------------------------------------------------------- commands


def cmd_ingest(args: argparse.Namespace) -> int:
    root = Path(args.root).resolve()
    async def run() -> dict[str, Any]:
        async with open_store(config.seekdb_path()) as (connection, recorder):
            summary = await import_material(connection, recorder, root)
            transcript = _write_sql(recorder, "ingest")
            return {"summary": summary.as_dict(), "sql_transcript": str(transcript)}

    result = asyncio_run(run())
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def cmd_context(args: argparse.Namespace) -> int:
    root = Path(args.root).resolve()
    async def run() -> dict[str, Any]:
        async with open_store(config.seekdb_path()) as (connection, recorder):
            material = await _select(connection, recorder, args.task, args.target_spec)
            transcript = _write_sql(recorder, "context")
        context = build_context(material)
        target = write_context(context, _artifacts("context"))
        return {"context": context, "context_file": str(target), "sql_transcript": str(transcript)}

    result = asyncio_run(run())
    if args.out:
        Path(args.out).write_text(
            json.dumps(result["context"], ensure_ascii=False, indent=2), encoding="utf-8"
        )
    if args.quiet:
        print(json.dumps(result["context"], ensure_ascii=False, indent=2))
    else:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def cmd_apply(args: argparse.Namespace) -> int:
    root = Path(args.root).resolve()
    state_client = PowerContextState(base_url=args.server_url) if args.server_url else PowerContextState()
    scope_id = ""
    previous_state: dict[str, Any] | None = None
    try:
        scope_id = state_client.resolve_scope()
        previous_state = state_client.load_task_state(scope_id, _task_key(args.task))
    except TaskStateError as error:
        print(f"[warn] PowerContext 状态不可用: {error}", file=sys.stderr)

    async def run() -> dict[str, Any]:
        async with open_store(config.seekdb_path()) as (connection, recorder):
            material = await _select(connection, recorder, args.task, args.target_spec)
            transcript = _write_sql(recorder, "context-apply")
        return {"material": material, "sql_transcript": str(transcript)}

    result = asyncio_run(run())
    context = build_context(result["material"])
    write_context(context, _artifacts("context"))
    system, user = render_prompt(context)

    if previous_state:
        user = (
            "以下是上一次运行保存的任务状态，请在此基础上继续：\n"
            f"```json\n{json.dumps(previous_state, ensure_ascii=False, indent=2)}\n```\n\n"
            + user
        )

    client = MiniMaxClient()
    if not client.available():
        print(
            "[error] 未设置 MINIMAX_API_KEY，无法完成 MiniMax 代码修改步骤。",
            file=sys.stderr,
        )
        return 2

    try:
        call = client.chat(system, user, max_tokens=args.max_tokens)
    except MiniMaxError as error:
        print(f"[error] MiniMax 调用失败: {error}", file=sys.stderr)
        return 3

    call_file = save_call(call, _artifacts("minimax"), f"minimax-call-{_stamp()}")
    stamp = _stamp()
    (_artifacts("minimax") / f"minimax-prompt-{stamp}.txt").write_text(
        f"# system\n{system}\n\n# user\n{user}\n", encoding="utf-8"
    )

    if not call.content.strip():
        print(
            "[error] 模型没有给出正文内容（预算可能全部用于思考）。"
            "请提高 --max-tokens 后重试。",
            file=sys.stderr,
        )
        (_artifacts("minimax") / f"minimax-answer-{stamp}.txt").write_text(
            call.reasoning, encoding="utf-8"
        )
        return 4

    try:
        diff = extract_diff(call.content)
    except PatchError as error:
        print(f"[error] 无法从模型回答中解析补丁: {error}", file=sys.stderr)
        (_artifacts("minimax") / f"minimax-answer-{stamp}.txt").write_text(
            call.content, encoding="utf-8"
        )
        return 4

    diff_file = _artifacts("diff") / f"minimax-{stamp}.patch"
    diff_file.write_text(diff + "\n", encoding="utf-8")

    if args.dry_run:
        print(json.dumps({"diff_file": str(diff_file), "applied": False}, ensure_ascii=False, indent=2))
        return 0

    try:
        applied = apply_unified_diff(root, diff)
    except PatchError as error:
        print(f"[error] 补丁应用失败: {error}", file=sys.stderr)
        return 5

    after = _git_diff(root, f"after-{_stamp()}")
    if scope_id:
        state_client.save_task_state(
            scope_id,
            {
                "task_key": _task_key(args.task),
                "task": args.task,
                "stage": "patch-applied",
                "done": [f"MiniMax 补丁已应用到 {item['path']}" for item in applied],
                "todo": ["运行目标测试与回归测试", "记录验证结果"],
                "applied": applied,
                "minimax_call": str(call_file),
                "diff": str(diff_file),
                "context_specs": sorted(_target_spec_ids(context)),
            },
        )

    print(
        json.dumps(
            {
                "applied": applied,
                "diff_file": str(diff_file),
                "minimax_call": str(call_file),
                "after_patch": str(after) if after else None,
                "scope_id": scope_id,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


def cmd_verify(args: argparse.Namespace) -> int:
    root = Path(args.root).resolve()
    state_client = PowerContextState(base_url=args.server_url) if args.server_url else PowerContextState()
    scope_id = ""
    try:
        scope_id = state_client.resolve_scope()
    except TaskStateError as error:
        print(f"[warn] PowerContext 状态不可用: {error}", file=sys.stderr)

    async def run() -> dict[str, Any]:
        async with open_store(config.seekdb_path()) as (connection, recorder):
            material = await _select(connection, recorder, args.task, args.target_spec)
            transcript = _write_sql(recorder, "context-verify")
        return {"material": material, "sql_transcript": str(transcript)}

    result = asyncio_run(run())
    context = build_context(result["material"])
    target_files, other_files = _split_tests(context)
    all_test_files = sorted(set(target_files) | set(other_files))
    if not all_test_files:
        all_test_files = sorted(str(path.relative_to(root).as_posix()) for path in (root / "tests").glob("test_*.py"))

    target_result = _run_pytest(root, target_files or all_test_files, f"{args.phase}-target")
    full_result = _run_pytest(root, all_test_files, f"{args.phase}-full")

    payload = {
        "task_key": _task_key(args.task),
        "task": args.task,
        "stage": f"verify-{args.phase}",
        "target_tests": target_files,
        "regression_tests": other_files,
        "target_result": target_result,
        "full_result": full_result,
    }
    state_file = _artifacts("state") / f"verify-{args.phase}-{_stamp()}.json"
    state_file.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    if scope_id:
        state_client.save_task_state(
            scope_id,
            {
                **payload,
                "done": [f"目标测试退出码 {target_result['exit_code']}", f"全量测试退出码 {full_result['exit_code']}"],
                "todo": [] if full_result["passed"] else ["修复失败测试"],
            },
        )

    print(json.dumps({**payload, "state_file": str(state_file)}, ensure_ascii=False, indent=2))
    return 0 if full_result["passed"] else 1


def cmd_state(args: argparse.Namespace) -> int:
    state_client = PowerContextState(base_url=args.server_url) if args.server_url else PowerContextState()
    scope_id = state_client.resolve_scope()
    if args.state_command == "show":
        states = state_client.list_task_states(scope_id)
        print(json.dumps({"scope_id": scope_id, "states": states}, ensure_ascii=False, indent=2))
        return 0
    if args.state_command == "save":
        record = state_client.save_task_state(
            scope_id,
            {
                "task_key": args.task_key or _task_key(args.task or ""),
                "task": args.task or "",
                "stage": args.stage or "manual",
                "done": args.done or [],
                "todo": args.todo or [],
            },
        )
        print(json.dumps({"scope_id": scope_id, "saved": record}, ensure_ascii=False, indent=2))
        return 0
    return 0


def cmd_reset(args: argparse.Namespace) -> int:
    store = config.seekdb_path()
    if store.exists():
        import shutil

        shutil.rmtree(store)
    print(json.dumps({"removed": str(store)}, ensure_ascii=False, indent=2))
    return 0


def asyncio_run(coroutine: Any) -> Any:
    """Run one coroutine to completion."""

    import asyncio

    return asyncio.run(coroutine)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="sddctx", description="SDD context module")
    sub = parser.add_subparsers(dest="command", required=True)

    ingest = sub.add_parser("ingest", help="import specifications, code and tests into seekdb")
    ingest.add_argument("--root", default=str(_default_root()))
    ingest.set_defaults(func=cmd_ingest)

    context = sub.add_parser("context", help="select the material a development task needs")
    context.add_argument("--task", default=DEFAULT_TASK)
    context.add_argument("--target-spec", action="append", default=None, help="specification entry this task implements")
    context.add_argument("--root", default=str(_default_root()))
    context.add_argument("--out", default=None)
    context.add_argument("--quiet", action="store_true")
    context.set_defaults(func=cmd_context)

    apply_command = sub.add_parser("apply", help="let MiniMax perform the code change")
    apply_command.add_argument("--task", default=DEFAULT_TASK)
    apply_command.add_argument("--target-spec", action="append", default=None, help="specification entry this task implements")
    apply_command.add_argument("--root", default=str(_default_root()))
    apply_command.add_argument("--server-url", default=None)
    apply_command.add_argument("--max-tokens", type=int, default=32768)
    apply_command.add_argument("--dry-run", action="store_true")
    apply_command.set_defaults(func=cmd_apply)

    verify = sub.add_parser("verify", help="run the target tests and the regression tests")
    verify.add_argument("--task", default=DEFAULT_TASK)
    verify.add_argument("--target-spec", action="append", default=None, help="specification entry this task implements")
    verify.add_argument("--root", default=str(_default_root()))
    verify.add_argument("--phase", default="run", choices=["before", "after", "run"])
    verify.add_argument("--server-url", default=None)
    verify.set_defaults(func=cmd_verify)

    state = sub.add_parser("state", help="read or write the task state kept in PowerContext")
    state.add_argument("state_command", choices=["show", "save"])
    state.add_argument("--task", default=None)
    state.add_argument("--task-key", default=None)
    state.add_argument("--stage", default=None)
    state.add_argument("--done", action="append", default=None)
    state.add_argument("--todo", action="append", default=None)
    state.add_argument("--server-url", default=None)
    state.set_defaults(func=cmd_state)

    reset = sub.add_parser("reset", help="remove the local seekdb store")
    reset.set_defaults(func=cmd_reset)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args) or 0)


if __name__ == "__main__":
    raise SystemExit(main())
