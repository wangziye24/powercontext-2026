"""seekdb（MySQL 协议）连接与执行封装。"""
from __future__ import annotations

import contextlib
from typing import Any, Iterable

import pymysql  # noqa: E402  app/__init__.py 已保证 vendor 目录在 sys.path 中
from pymysql.cursors import DictCursor

from .config import settings


def connect(database: str | None = None):
    kwargs = dict(
        host=settings.db_host,
        port=settings.db_port,
        user=settings.db_user,
        password=settings.db_password,
        charset="utf8mb4",
        cursorclass=DictCursor,
        autocommit=True,
    )
    if database:
        kwargs["database"] = database
    return pymysql.connect(**kwargs)


@contextlib.contextmanager
def cursor(database: str | None = None):
    conn = connect(database or settings.db_name)
    try:
        with conn.cursor() as cur:
            yield cur
    finally:
        conn.close()


@contextlib.contextmanager
def server_cursor():
    """不指定数据库的连接（用于 CREATE DATABASE）。"""
    conn = connect(None)
    try:
        with conn.cursor() as cur:
            yield cur
    finally:
        conn.close()


def execute(cur, sql: str, args: Iterable[Any] | None = None):
    cur.execute(sql, tuple(args) if args is not None else None)
    return cur


def fetch_all(cur, sql: str, args: Iterable[Any] | None = None) -> list[dict]:
    cur.execute(sql, tuple(args) if args is not None else None)
    return list(cur.fetchall())


def fetch_one(cur, sql: str, args: Iterable[Any] | None = None) -> dict | None:
    cur.execute(sql, tuple(args) if args is not None else None)
    return cur.fetchone()


def table_exists(cur, table: str) -> bool:
    row = fetch_one(
        cur,
        "SELECT COUNT(*) AS c FROM information_schema.tables "
        "WHERE table_schema = %s AND table_name = %s",
        (settings.db_name, table),
    )
    return bool(row and row["c"])
