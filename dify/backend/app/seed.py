"""幂等灌入种子数据：建表 + 设定库 + 预计算向量。

用法：
    python -m app.seed          # 在 dify/backend 目录下执行
    curl -XPOST localhost:8080/admin/seed
"""
from __future__ import annotations

import json
from pathlib import Path

from .config import settings
from .db import cursor, fetch_one, server_cursor
from .embed import embed_texts, provider_name, vec_literal

DATA_DIR = Path(__file__).resolve().parents[2] / "data"
SCHEMA_SQL = DATA_DIR / "schema.sql"
LORE_JSON = DATA_DIR / "lore.json"


def _split_sql(text: str) -> list[str]:
    out = []
    for chunk in text.split(";"):
        lines = [
            ln for ln in chunk.splitlines()
            if ln.strip() and not ln.strip().startswith("--")
        ]
        if lines:
            out.append("\n".join(lines))
    return out


def ensure_schema(cur) -> None:
    # 先确保数据库存在（此时可能还没有 nightstore 库）
    with server_cursor() as raw:
        raw.execute(f"CREATE DATABASE IF NOT EXISTS {settings.db_name}")
    for stmt in _split_sql(SCHEMA_SQL.read_text(encoding="utf-8")):
        cur.execute(stmt)


def seed_lore(cur) -> int:
    rows = json.loads(LORE_JSON.read_text(encoding="utf-8"))
    texts = [f"{r['title']}\n{r['content']}" for r in rows]
    vectors = embed_texts(texts)
    for r, vec in zip(rows, vectors):
        cur.execute("DELETE FROM lore WHERE id = %s", (r["id"],))
        cur.execute(
            "INSERT INTO lore (id, kind, title, content, tag, weight, cond_min, cond_max,"
            " cond_pranks, embedding)"
            " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (
                r["id"],
                r["kind"],
                r["title"],
                r["content"],
                r.get("tag", ""),
                r.get("weight", 0),
                r.get("cond_min", -999),
                r.get("cond_max", 999),
                r.get("cond_pranks", 0),
                vec_literal(vec),
            ),
        )
    return len(rows)


def seed_all() -> dict:
    # 先建库，再连库建表（库不存在时无法直接切库）
    with server_cursor() as raw:
        raw.execute(f"CREATE DATABASE IF NOT EXISTS {settings.db_name}")
    with cursor() as cur:
        ensure_schema(cur)
        n = seed_lore(cur)
        row = fetch_one(cur, "SELECT COUNT(*) AS c FROM lore")
    return {
        "ok": True,
        "lore_rows": n,
        "lore_rows_in_db": int(row["c"]) if row else 0,
        "embed_provider": provider_name(),
        "embed_dim": settings.embed_dim,
        "db": f"{settings.db_host}:{settings.db_port}/{settings.db_name}",
    }


if __name__ == "__main__":
    print(json.dumps(seed_all(), ensure_ascii=False, indent=2))
