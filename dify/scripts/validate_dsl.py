#!/usr/bin/env python3
"""DSL 自检：在导入 Dify 之前先自己把明显的错误挑出来。

检查项：
1. YAML 可解析，app.mode / workflow.graph 结构完整；
2. 每个节点的 id 唯一，边只引用已存在的节点；
3. 模板里的 {{#节点.字段#}} 引用都能解析到真实节点输出（或 Dify 内置系统变量）；
4. 代码节点的 Python 语法可编译；
5. http-request 节点的 body 是合法 JSON（把变量占位成字符串后 parse）。

用法：python3 dify/scripts/validate_dsl.py
"""
from __future__ import annotations

import json
import pathlib
import re
import sys

import yaml

DSL = pathlib.Path(__file__).resolve().parents[1] / "dsl" / "aye-ledger-chatflow.yml"

SYSTEM_VARS = {
    "sys.query", "sys.user_id", "sys.conversation_id", "sys.app_id", "sys.workflow_id",
    "sys.files", "sys.workflow_run_id", "sys.timestamp",
}

REF_RE = re.compile(r"\{\{#([A-Za-z0-9_\-]+)\.([A-Za-z0-9_]+)#\}\}")


def main() -> int:
    problems: list[str] = []
    doc = yaml.safe_load(DSL.read_text(encoding="utf-8"))

    app = doc.get("app") or {}
    if app.get("mode") != "advanced-chat":
        problems.append(f"app.mode 应为 advanced-chat，实际 {app.get('mode')!r}")
    if not doc.get("version"):
        problems.append("缺少 DSL version")
    if doc.get("kind") != "app":
        problems.append(f"kind 应为 app，实际 {doc.get('kind')!r}")

    graph = ((doc.get("workflow") or {}).get("graph")) or {}
    nodes = graph.get("nodes") or []
    edges = graph.get("edges") or []
    if not nodes:
        problems.append("workflow.graph.nodes 为空")
        return report(problems)

    by_id = {str(n.get("id")): n for n in nodes}
    if len(by_id) != len(nodes):
        problems.append("存在重复节点 id")

    for e in edges:
        if str(e.get("source")) not in by_id:
            problems.append(f"边的 source 不存在：{e.get('source')}")
        if str(e.get("target")) not in by_id:
            problems.append(f"边的 target 不存在：{e.get('target')}")

    # 节点可达性：从 start 出发应能走到所有节点
    reachable = set()
    adj: dict[str, list[str]] = {}
    for e in edges:
        adj.setdefault(str(e.get("source")), []).append(str(e.get("target")))
    start_ids = [nid for nid, n in by_id.items() if n.get("data", {}).get("type") == "start"]
    if not start_ids:
        problems.append("没有 start 节点")
    else:
        stack = list(start_ids)
        while stack:
            cur = stack.pop()
            if cur in reachable:
                continue
            reachable.add(cur)
            stack.extend(adj.get(cur, []))
        for nid in by_id:
            if nid not in reachable:
                problems.append(f"节点不可达：{nid} {by_id[nid].get('data', {}).get('title')}")

    # 收集每个节点的输出字段
    outputs: dict[str, set[str]] = {}
    for nid, node in by_id.items():
        data = node.get("data") or {}
        ntype = data.get("type")
        fields: set[str] = set()
        if ntype == "code":
            fields |= set((data.get("outputs") or {}).keys())
        elif ntype in ("llm", "parameter-extractor"):
            fields |= {"text", "usage", "files"}
        elif ntype == "http-request":
            fields |= {"body", "status_code", "headers", "files"}
        elif ntype == "start":
            for v in data.get("variables") or []:
                fields.add(str(v.get("variable")))
        elif ntype == "answer":
            fields |= {"answer"}
        elif ntype == "if-else":
            fields |= {"result"}
        outputs[nid] = fields

    # 校验模板引用
    def scan(text: str, where: str) -> None:
        for node_id, field in REF_RE.findall(text or ""):
            if f"{node_id}.{field}" in SYSTEM_VARS:
                continue
            if node_id not in by_id:
                problems.append(f"{where} 引用了不存在的节点：{node_id}")
                continue
            if field not in outputs.get(node_id, set()):
                problems.append(f"{where} 引用了 {node_id} 不存在的输出：{field}")

    for nid, node in by_id.items():
        data = node.get("data") or {}
        title = data.get("title")
        for pt in data.get("prompt_template") or []:
            scan(pt.get("text"), f"节点[{title}] prompt")
        scan(data.get("answer"), f"节点[{title}] answer")
        scan(data.get("code"), f"节点[{title}] code")
        for v in data.get("variables") or []:
            sel = v.get("value_selector") or []
            if len(sel) == 2 and str(sel[0]) in by_id:
                if sel[1] not in outputs.get(str(sel[0]), set()):
                    problems.append(
                        f"节点[{title}] 的输入 {v.get('variable')} 引用了 "
                        f"{sel[0]} 不存在的输出 {sel[1]}"
                    )
        for item in ((data.get("body") or {}).get("data") or []):
            scan(item.get("value"), f"节点[{title}] body.{item.get('key')}")
        scan(data.get("params"), f"节点[{title}] params")
        scan(data.get("url"), f"节点[{title}] url")

        # 代码节点语法
        if data.get("type") == "code":
            try:
                compile(data.get("code") or "", "<code>", "exec")
            except SyntaxError as exc:
                problems.append(f"节点[{title}] 代码语法错误：{exc}")

        # http body 是否是合法 JSON（把变量替换成占位串）
        if data.get("type") == "http-request":
            body = data.get("body") or {}
            if body.get("type") == "json-body":
                payload = {}
                for item in body.get("data") or []:
                    value = str(item.get("value") or "")
                    value = REF_RE.sub(lambda m: f'"<{m.group(1)}.{m.group(2)}>"', value)
                    payload[item.get("key")] = value
                try:
                    json.loads(json.dumps(payload, ensure_ascii=False))
                except Exception as exc:
                    problems.append(f"节点[{title}] body 不是合法 JSON：{exc}")

    return report(problems)


def report(problems: list[str]) -> int:
    if problems:
        print(f"❌ 发现 {len(problems)} 个问题：")
        for p in problems:
            print("  -", p)
        return 1
    print("✅ DSL 自检通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
