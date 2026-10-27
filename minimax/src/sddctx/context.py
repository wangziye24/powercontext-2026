"""Turn a selected material set into the context handed to the coding assistant."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

CONTEXT_SCHEMA_VERSION = 1


def build_context(material: dict[str, Any]) -> dict[str, Any]:
    """Return the serialisable context of a development task."""

    return {
        "schema_version": CONTEXT_SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "task": material["task"],
        "keywords": material["keywords"],
        "mentioned_specs": material["mentioned_specs"],
        "target_specs": material.get("target_specs", []),
        "specifications": material["specifications"],
        "code": [
            {
                "path": item["path"],
                "symbol": item["symbol"],
                "kind": item["kind"],
                "line_start": item["line_start"],
                "line_end": item["line_end"],
                "signature": item["signature"],
                "doc": item["doc"],
                "body": item.get("body", ""),
                "covers_specs": item["covers_specs"],
                "source_hash": item["source_hash"],
                "score": item["score"],
            }
            for item in material["code"]
        ],
        "tests": [
            {
                "path": item["path"],
                "name": item["name"],
                "purpose": item["purpose"],
                "covers": item["covers"],
                "target_feature": item["target_feature"],
                "role": item.get("role", "regression"),
                "body": item.get("body", ""),
                "source_hash": item["source_hash"],
                "score": item["score"],
            }
            for item in material["tests"]
        ],
        "conventions": material.get("conventions", []),
        "related_files": material.get("related_files", []),
        "missing_error_types": material.get("missing_error_types", []),
    }


def write_context(context: dict[str, Any], directory: Path) -> Path:
    """Persist the context as JSON so the submission can show what was selected."""

    directory.mkdir(parents=True, exist_ok=True)
    target = directory / "context.json"
    target.write_text(json.dumps(context, ensure_ascii=False, indent=2), encoding="utf-8")
    return target


def render_prompt(context: dict[str, Any]) -> tuple[str, str]:
    """Build the system and user prompt for the MiniMax coding assistant."""

    system = (
        "你是一名资深 Python 工程师，正在按规格驱动开发（SDD）的方式修改一个已有工程。"
        "你只能依据下面提供的规格条目与文件内容工作，并保持未被任务要求的既有行为完全不变。"
        "你没有任何工具，无法读取文件系统，因此你必须只根据下面给出的文件内容来写补丁。"
        "输出必须是一个标准的 unified diff：以 '--- a/路径' 与 '+++ b/路径' 开头，"
        "包含 '@@' 区块；不要输出解释文字，不要输出整个文件，不要修改测试文件。"
    )

    lines: list[str] = []
    lines.append(f"# 开发任务\n\n{context['task']}\n")

    lines.append("# 适用的规格条目\n")
    target_specs = set(context.get("target_specs", []))
    if not context["specifications"]:
        lines.append("（没有检索到规格条目，请谨慎处理）\n")
    for spec in context["specifications"]:
        marker = "【本次要实现】" if spec["spec_id"] in target_specs else "【必须保持的原有行为】"
        lines.append(f"## {spec['spec_id']} {spec['title']} {marker}\n来源：{spec['source_path']}\n")
        lines.append(f"{spec['body']}\n")

    lines.append("# 需要修改的代码符号\n")
    if not context["code"]:
        lines.append("（没有检索到相关代码）\n")
    for item in context["code"]:
        lines.append(
            f"- {item['path']}::{item['symbol']}（{item['kind']}，第 {item['line_start']}-{item['line_end']} 行）"
        )
        if item["covers_specs"]:
            lines.append(f"  关联规格：{', '.join(item['covers_specs'])}")

    lines.append("\n# 相关文件的当前完整内容\n")
    for item in context.get("related_files", []):
        state = "本次要修改" if item.get("selected") else "同包参考文件（仅供遵循工程约定，除非必要请不要修改）"
        lines.append(f"## {item['path']}（{state}）\n")
        lines.append("```python")
        lines.append(item.get("content", "").rstrip("\n"))
        lines.append("```\n")

    if context.get("conventions"):
        lines.append("# 该包已有的错误类型（新增错误请沿用同样的命名与基类）\n")
        for item in context["conventions"]:
            lines.append(f"## {item['path']}::{item['symbol']}\n")
            lines.append("```python")
            lines.append(item.get("body", "").rstrip("\n"))
            lines.append("```\n")

    if context.get("missing_error_types"):
        lines.append("# ⚠ 规格要求但工程中尚不存在的类型（本次必须新增）\n")
        for item in context["missing_error_types"]:
            lines.append(
                f"- {item['name']}（由 {item['required_by']} 要求）：{item['hint']}"
            )
        lines.append("")

    lines.append("# 用于验证的测试（只读，禁止修改）\n")
    if not context["tests"]:
        lines.append("（没有检索到相关测试）\n")
    for item in context["tests"]:
        covers = ", ".join(item["covers"]) or "-"
        role = "目标功能测试" if item.get("role") == "target" else "必须保持的原有行为测试"
        lines.append(f"## {item['path']}::{item['name']}（{role}，覆盖规格：{covers}）\n")
        lines.append("```python")
        lines.append(item.get("body", "").rstrip("\n"))
        lines.append("```\n")

    lines.append(
        "# 输出要求\n"
        "1. 只输出 unified diff，不要任何解释。\n"
        "2. 路径使用相对于工程根目录的路径，并加上 a/ 与 b/ 前缀。\n"
        "3. 新增文件使用 '--- /dev/null' 与 '+++ b/路径'。\n"
        "4. hunk 的上下文行与删除行必须与上面给出的文件内容逐字一致，否则补丁无法应用。\n"
        "5. 禁止修改 tests/ 目录下的任何文件，禁止删除或削弱任何断言。\n"
        "6. 只实现标记为【本次要实现】的规格；标记为【必须保持的原有行为】的规格行为不能改变。\n"
        "7. 如果需要新增错误类型，请沿用该包已有的错误类型命名与继承关系。\n"
        "8. 上文「规格要求但工程中尚不存在的类型」必须在本次修改中新增，否则代码无法运行。\n"
        "9. 补丁的 hunk 行号请按上面给出的文件内容计算（含文件开头的 license 注释）；"
        "即使行号有偏差，上下文行也必须与文件内容逐字一致。\n"
        "\n输出格式示例：\n"
        "```diff\n"
        "--- a/src/orders/filter.py\n"
        "+++ b/src/orders/filter.py\n"
        "@@ -1,7 +1,7 @@\n"
        " unchanged line\n"
        "-removed line\n"
        "+added line\n"
        "```\n"
    )
    return system, "\n".join(lines)
