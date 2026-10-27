#!/usr/bin/env python3
"""准备 PowerContext 的存储：建库 + 清理旧表（让 PowerContext 每次都能干净启动）。

为什么需要它：
    本分支 PowerContext 通过 SQLAlchemy 的 inspector 判断表是否存在，
    对 seekdb 的 information_schema 兼容不好，重启时会误判"表不存在"，
    于是重复执行 CREATE TABLE memories 报 1050 Table 'memories' already exists，
    导致 memory service 不可用（health 变 degraded）。
    所以：**每次启动 PowerContext 之前先跑一次本脚本**，把 memories / user_profiles 删掉，
    由 PowerContext 自己重建（记忆会在灌数据脚本里重新写入）。

用法：
    python3 dify/scripts/init_powercontext.py            # 只清理
    python3 dify/scripts/init_powercontext.py --memory   # 清理后写一条演示用初始记忆
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys
import urllib.error

BACKEND = pathlib.Path(__file__).resolve().parents[1] / "backend"
sys.path.insert(0, str(BACKEND))

from app.config import settings  # noqa: E402
from app.db import server_cursor  # noqa: E402

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from seed import seed_memory  # noqa: E402

PC_DB = "powermem"
PC_TABLES = ("memories", "user_profiles")


def prepare() -> dict:
    with server_cursor() as cur:
        cur.execute(f"CREATE DATABASE IF NOT EXISTS {PC_DB}")
        dropped = []
        for table in PC_TABLES:
            cur.execute(f"DROP TABLE IF EXISTS {PC_DB}.{table}")
            dropped.append(table)
    return {
        "ok": True,
        "database": PC_DB,
        "dropped_tables": dropped,
        "seekdb": f"{settings.db_host}:{settings.db_port}",
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--memory", action="store_true")
    parser.add_argument("--player", default="player_demo")
    args = parser.parse_args()

    print(json.dumps(prepare(), ensure_ascii=False, indent=2))
    if args.memory:
        try:
            print(json.dumps(seed_memory(args.player), ensure_ascii=False, indent=2, default=str))
        except urllib.error.URLError as exc:
            print(f"[warn] PowerContext 还不可达（先启动它再补记忆）：{exc}")


if __name__ == "__main__":
    main()
