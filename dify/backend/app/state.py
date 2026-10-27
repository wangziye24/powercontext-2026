"""玩家状态与事件流水（存 seekdb）。"""
from __future__ import annotations

from typing import Any

from .db import fetch_all, fetch_one

DEFAULT_STATE = {
    "player_name": "",
    "affection": 0,       # 好感度 -100..100
    "balance": 0.0,       # 欠账（元）
    "blacklist": 0,
    "pranks_done": 0,
    "chapter": "第一章·熟客",
}


def get_state(cur, player_id: str) -> dict:
    row = fetch_one(
        cur,
        "SELECT player_id, player_name, affection, balance, blacklist, pranks_done, chapter "
        "FROM player_state WHERE player_id = %s",
        (player_id,),
    )
    if row is None:
        state = {"player_id": player_id, **DEFAULT_STATE}
        cur.execute(
            "INSERT INTO player_state (player_id, player_name, affection, balance, blacklist,"
            " pranks_done, chapter) VALUES (%s, %s, %s, %s, %s, %s, %s)",
            (
                player_id,
                state["player_name"],
                state["affection"],
                state["balance"],
                state["blacklist"],
                state["pranks_done"],
                state["chapter"],
            ),
        )
        return state
    row["balance"] = float(row["balance"])
    return row


def save_state(cur, player_id: str, payload: dict[str, Any]) -> dict:
    """写入状态（upsert）+ 追加一条事件流水。"""
    current = get_state(cur, player_id)
    merged = {
        "player_name": payload.get("player_name") or current["player_name"],
        "affection": int(payload.get("affection", current["affection"])),
        "balance": float(payload.get("balance", current["balance"])),
        "blacklist": int(payload.get("blacklist", current["blacklist"])),
        "pranks_done": int(payload.get("pranks_done", current["pranks_done"])),
        "chapter": payload.get("chapter") or current["chapter"],
    }
    cur.execute(
        "UPDATE player_state SET player_name=%s, affection=%s, balance=%s, blacklist=%s,"
        " pranks_done=%s, chapter=%s WHERE player_id=%s",
        (
            merged["player_name"],
            merged["affection"],
            merged["balance"],
            merged["blacklist"],
            merged["pranks_done"],
            merged["chapter"],
            player_id,
        ),
    )

    event = payload.get("event") or {}
    if event:
        cur.execute(
            "INSERT INTO event_log (player_id, turn_id, intent, action, affection_delta,"
            " balance_delta, hit_event) VALUES (%s, %s, %s, %s, %s, %s, %s)",
            (
                player_id,
                str(event.get("turn_id", ""))[:64],
                str(event.get("intent", ""))[:32],
                str(event.get("action", ""))[:255],
                int(event.get("affection_delta", 0)),
                float(event.get("balance_delta", 0)),
                str(event.get("hit_event", ""))[:64],
            ),
        )
    return {"player_id": player_id, **merged}


def list_events(cur, player_id: str, limit: int = 20) -> list[dict]:
    return fetch_all(
        cur,
        "SELECT id, turn_id, intent, action, affection_delta, balance_delta, hit_event, created_at"
        " FROM event_log WHERE player_id = %s ORDER BY id DESC LIMIT %s",
        (player_id, int(limit)),
    )
