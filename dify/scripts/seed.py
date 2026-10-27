#!/usr/bin/env python3
"""一键初始化：建表 + 灌设定 + 预置一条"记忆盲盒"用的初始记忆。

用法：
    python3 dify/scripts/seed.py            # 只灌 seekdb
    python3 dify/scripts/seed.py --memory   # 同时往 PowerContext 写一条初始记忆

说明：
- 幂等：重复执行不会产生重复数据（lore 按 id 先删后插，记忆按 user_id 先查后写）。
- 记忆写入使用 ``infer=false``（存原文），保证行为确定、可复现。
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys
import urllib.error
import urllib.request

BACKEND = pathlib.Path(__file__).resolve().parents[1] / "backend"
sys.path.insert(0, str(BACKEND))

from app import seed as seed_mod  # noqa: E402
from app.config import settings  # noqa: E402

# 预置记忆：让"记忆盲盒"玩法第一次就有东西可校准
INITIAL_MEMORY = (
    "[梗] 玩家自称『夜班钉子户』，喜欢叫收银员『阿姨』，还说过要把薄荷糖机拆了当废品卖"
)


def _post(url: str, payload: dict, headers: dict | None = None) -> dict:
    req = urllib.request.Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json", **(headers or {})},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _get(url: str, headers: dict | None = None) -> dict:
    req = urllib.request.Request(url, headers=headers or {})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def seed_memory(player_id: str = "player_demo") -> dict:
    base = settings.powercontext_base_url.rstrip("/")
    headers = {"X-API-Key": settings.powercontext_api_key} if settings.powercontext_api_key else {}
    existing = _get(f"{base}/api/v1/memories?user_id={player_id}&limit=50", headers)
    items = (existing.get("data") or {}).get("memories") or (existing.get("data") or {}).get("results") or []
    if any(INITIAL_MEMORY[:12] in str(i.get("content", "")) for i in items):
        return {"ok": True, "skipped": True, "reason": "初始记忆已存在", "count": len(items)}
    resp = _post(
        f"{base}/api/v1/memories",
        {
            "content": INITIAL_MEMORY,
            "user_id": player_id,
            "agent_id": "aye-nightstore",
            "scope": "user",
            "memory_type": "working",
            "metadata": {"source": "seed"},
            "infer": False,
        },
        headers,
    )
    return {"ok": True, "skipped": False, "created": resp.get("data"), "count": len(items) + 1}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--memory", action="store_true", help="同时预置 PowerContext 初始记忆")
    parser.add_argument("--player", default="player_demo")
    args = parser.parse_args()

    result = seed_mod.seed_all()
    print(json.dumps(result, ensure_ascii=False, indent=2))

    if args.memory:
        try:
            print(json.dumps(seed_memory(args.player), ensure_ascii=False, indent=2, default=str))
        except urllib.error.URLError as exc:
            print(f"[warn] PowerContext 不可达，跳过初始记忆：{exc}")


if __name__ == "__main__":
    main()
