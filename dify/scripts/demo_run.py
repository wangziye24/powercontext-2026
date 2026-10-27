#!/usr/bin/env python3
"""一键跑完整玩法演示（跨会话记忆 + 混合检索 + 记忆校准），输出可直接贴进文档的对话记录。

用法：
    export DIFY_CONSOLE=http://127.0.0.1:5001
    export DIFY_EMAIL=nightstore@example.com DIFY_PASSWORD='Nightstore#2026'
    python3 dify/scripts/demo_run.py --app-id <app_id> --out dify/docs/evidence/demo-transcript.md

说明：
- 每个"会话"= 独立的 conversation_id（对应 Dify 里的"新建对话"），**同一个 player_id = 同一段记忆**；
- 脚本结束后会打印 PowerContext 记忆与 seekdb 事件流水，方便核对技术落点。
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import sys
import urllib.error
import urllib.request

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from dify_ops import Console  # noqa: E402

SCRIPT = [
    (
        "会话一",
        [
            "我叫小李，以后每周三都来买热咖啡",
            "关东煮还有吗？",
            "把薄荷糖的标签换成「后悔药」怎么样？",
        ],
    ),
    (
        "会话二（新对话，但同一个玩家）",
        [
            "在吗",
            "你还记得我上次干了什么吗？",
        ],
    ),
    (
        "会话三（新对话）",
        [
            "不对，我上次明明是把薄荷糖换成了芥末糖",
            "你还记得我喜欢喝什么吗？",
        ],
    ),
]


def chat(base: str, token: str, query: str, user: str, conversation_id: str = "") -> dict:
    payload = {
        "query": query,
        "response_mode": "blocking",
        "user": user,
        "inputs": {"player_id": user, "player_name": "小李"},
    }
    if conversation_id:
        payload["conversation_id"] = conversation_id
    req = urllib.request.Request(
        base.rstrip("/") + "/v1/chat-messages",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json", "Authorization": "Bearer " + token},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=300) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return {"error": exc.read().decode("utf-8", "ignore")[:600]}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--app-id", required=True)
    parser.add_argument("--player-id", default="player_demo")
    parser.add_argument("--base", default=os.getenv("DIFY_SERVICE_BASE", "http://127.0.0.1"))
    parser.add_argument("--out")
    args = parser.parse_args()

    c = Console()
    c.login()
    keys = c.api_key_list(args.app_id)
    token = (keys[0] or {}).get("token") if keys else c.create_api_key(args.app_id)
    if not token:
        print("拿不到 API Key", file=sys.stderr)
        return 1

    lines: list[str] = []
    lines.append("# 演示对话记录（自动生成）\n")
    lines.append(f"> 应用：午夜便利店·阿夜的账本　玩家 ID：`{args.player_id}`　昵称：小李\n")

    for title, queries in SCRIPT:
        lines.append(f"\n## {title}\n")
        conversation_id = ""
        for q in queries:
            resp = chat(args.base, token, q, args.player_id, conversation_id)
            conversation_id = resp.get("conversation_id", conversation_id)
            answer = resp.get("answer") or resp.get("error", "")
            lines.append(f"**玩家**：{q}\n")
            lines.append(f"**阿夜**：\n\n{answer}\n")
            print(f"[{title}] {q} -> {(answer or '')[:60]!r}")

    text = "\n".join(lines) + "\n"
    if args.out:
        out = pathlib.Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text, encoding="utf-8")
        print(f"\n[demo] 记录已写入 {out}")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
