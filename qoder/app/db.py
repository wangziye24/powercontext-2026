"""seekdb 连接池、建表与混合检索。

使用 PyMySQL 连接 seekdb（MySQL 协议），支持向量检索 + 全文检索 + 结构化过滤。
"""

from __future__ import annotations

import json
import logging
from contextlib import contextmanager
from typing import Any, Generator

import pymysql
from pymysql.cursors import DictCursor

from app.config import (
    DB_NAME,
    EMBED_DIM,
    SCORE_WEIGHT_KW,
    SCORE_WEIGHT_VEC,
    SEEKDB_HOST,
    SEEKDB_PASSWORD,
    SEEKDB_PORT,
    SEEKDB_USER,
)

logger = logging.getLogger(__name__)


class DBError(Exception):
    """数据库操作异常基类。"""


# ── 连接池 ────────────────────────────────────────────────────────

class ConnectionPool:
    """简单的 PyMySQL 连接池。"""

    def __init__(
        self,
        host: str = SEEKDB_HOST,
        port: int = SEEKDB_PORT,
        user: str = SEEKDB_USER,
        password: str = SEEKDB_PASSWORD,
        database: str = DB_NAME,
        pool_size: int = 5,
    ) -> None:
        self._config = {
            "host": host,
            "port": port,
            "user": user,
            "password": password,
            "database": database,
            "charset": "utf8mb4",
            "cursorclass": DictCursor,
            "autocommit": True,
        }
        self._pool: list[pymysql.connections.Connection] = []
        self._pool_size = pool_size

        # 预创建连接
        for _ in range(pool_size):
            try:
                conn = pymysql.connect(**self._config)
                self._pool.append(conn)
            except pymysql.Error as exc:
                raise DBError(
                    f"连接 seekdb 失败（{host}:{port}/{database}）: {exc}"
                ) from exc

    @contextmanager
    def connection(self) -> Generator[pymysql.connections.Connection, None, None]:
        """从连接池获取连接，使用后归还。"""
        if not self._pool:
            # 池空时临时创建
            try:
                conn = pymysql.connect(**self._config)
            except pymysql.Error as exc:
                raise DBError(f"连接 seekdb 失败: {exc}") from exc
            try:
                yield conn
            finally:
                conn.close()
        else:
            conn = self._pool.pop()
            try:
                yield conn
            finally:
                self._pool.append(conn)

    def close_all(self) -> None:
        """关闭所有连接。"""
        for conn in self._pool:
            try:
                conn.close()
            except Exception:
                pass
        self._pool.clear()


# ── 全局连接池实例 ────────────────────────────────────────────────

_pool: ConnectionPool | None = None


def get_pool() -> ConnectionPool:
    """获取全局连接池（懒初始化）。"""
    global _pool
    if _pool is None:
        _pool = ConnectionPool()
    return _pool


# ── 建表 ──────────────────────────────────────────────────────────

def ensure_database(database: str = DB_NAME) -> None:
    """确保目标库存在。

    全新部署的 seekdb 里还没有该库时，连接会直接报
    ``1049 Unknown database``，因此先在实例级连接上执行 CREATE DATABASE。
    """
    conn = pymysql.connect(
        host=SEEKDB_HOST,
        port=SEEKDB_PORT,
        user=SEEKDB_USER,
        password=SEEKDB_PASSWORD,
        charset="utf8mb4",
        autocommit=True,
    )
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"CREATE DATABASE IF NOT EXISTS `{database}` "
                "DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci"
            )
    finally:
        conn.close()


def create_tables(embed_dim: int = EMBED_DIM) -> None:
    """幂等建表：docs + chunks。

    Args:
        embed_dim: 向量维度（minimax=1536, local=512）
    """
    ensure_database()
    pool = get_pool()

    sql_docs = """
    CREATE TABLE IF NOT EXISTS docs (
        doc_id     BIGINT PRIMARY KEY AUTO_INCREMENT,
        title      VARCHAR(512) NOT NULL,
        source     VARCHAR(255) NOT NULL DEFAULT '',
        course     VARCHAR(64)  NOT NULL DEFAULT '未分类',
        tags       VARCHAR(255) NOT NULL DEFAULT '',
        chunk_cnt  INT NOT NULL DEFAULT 0,
        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
    )
    """

    sql_chunks = f"""
    CREATE TABLE IF NOT EXISTS chunks (
        chunk_id  BIGINT PRIMARY KEY AUTO_INCREMENT,
        doc_id    BIGINT NOT NULL,
        seq       INT NOT NULL DEFAULT 0,
        heading   VARCHAR(255) NOT NULL DEFAULT '',
        content   TEXT NOT NULL,
        embedding VECTOR({embed_dim}),
        course    VARCHAR(64) NOT NULL DEFAULT '未分类',
        INDEX idx_chunks_doc_id (doc_id),
        FULLTEXT INDEX ft_chunks_content (content) WITH PARSER ngram,
        VECTOR INDEX idx_chunks_vec (embedding) WITH (distance=cosine, type=hnsw)
    )
    """

    with pool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(sql_docs)
            try:
                cur.execute(sql_chunks)
            except pymysql.err.OperationalError as exc:
                # 如果 ngram 不支持，退化为普通全文索引
                if "ngram" in str(exc).lower() or "parser" in str(exc).lower():
                    logger.warning("ngram 分词器不可用，退化为普通全文索引: %s", exc)
                    sql_chunks_fallback = f"""
                    CREATE TABLE IF NOT EXISTS chunks (
                        chunk_id  BIGINT PRIMARY KEY AUTO_INCREMENT,
                        doc_id    BIGINT NOT NULL,
                        seq       INT NOT NULL DEFAULT 0,
                        heading   VARCHAR(255) NOT NULL DEFAULT '',
                        content   TEXT NOT NULL,
                        embedding VECTOR({embed_dim}),
                        course    VARCHAR(64) NOT NULL DEFAULT '未分类',
                        INDEX idx_chunks_doc_id (doc_id),
                        FULLTEXT INDEX ft_chunks_content (content),
                        VECTOR INDEX idx_chunks_vec (embedding) WITH (distance=cosine, type=hnsw)
                    )
                    """
                    cur.execute(sql_chunks_fallback)
                else:
                    raise

    logger.info("建表完成（embedding 维度=%d）", embed_dim)


# ── 文档操作 ──────────────────────────────────────────────────────

def insert_doc(title: str, source: str, course: str = "未分类", tags: str = "") -> int:
    """插入文档记录，返回 doc_id。"""
    pool = get_pool()
    sql = """
    INSERT INTO docs (title, source, course, tags, chunk_cnt)
    VALUES (%s, %s, %s, %s, 0)
    """
    with pool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (title, source, course, tags))
            return cur.lastrowid


def insert_chunks(
    doc_id: int,
    chunks: list[dict[str, Any]],
    course: str = "未分类",
) -> int:
    """批量插入 chunks，返回插入数量。

    Args:
        doc_id: 文档 ID
        chunks: [{seq, heading, content, embedding}, ...]
        course: 课程分类

    Returns:
        插入的 chunk 数量
    """
    if not chunks:
        return 0

    pool = get_pool()
    sql = """
    INSERT INTO chunks (doc_id, seq, heading, content, embedding, course)
    VALUES (%s, %s, %s, %s, %s, %s)
    """

    # 准备批量数据
    data = [
        (
            doc_id,
            chunk["seq"],
            chunk["heading"],
            chunk["content"],
            _format_vector(chunk["embedding"]),
            course,
        )
        for chunk in chunks
    ]

    with pool.connection() as conn:
        with conn.cursor() as cur:
            cur.executemany(sql, data)

            # 更新 docs.chunk_cnt
            cur.execute(
                "UPDATE docs SET chunk_cnt = %s WHERE doc_id = %s",
                (len(chunks), doc_id),
            )

    return len(chunks)


def _format_vector(vec: list[float]) -> str:
    """将向量格式化为 seekdb 要求的字符串格式：[0.1,0.2,...]"""
    return "[" + ",".join(f"{v:.6f}" for v in vec) + "]"


def delete_doc(doc_id: int) -> bool:
    """删除文档及其所有 chunks。

    Returns:
        是否成功删除
    """
    pool = get_pool()
    with pool.connection() as conn:
        with conn.cursor() as cur:
            # 先删除 chunks
            cur.execute("DELETE FROM chunks WHERE doc_id = %s", (doc_id,))
            # 再删除 doc
            cur.execute("DELETE FROM docs WHERE doc_id = %s", (doc_id,))
            return cur.rowcount > 0


def delete_doc_by_title(title: str) -> int:
    """按标题删除文档及其分块（用于 seed 脚本幂等重灌）。

    Returns:
        删除的文档数量
    """
    pool = get_pool()
    with pool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT doc_id FROM docs WHERE title = %s", (title,))
            doc_ids = [row["doc_id"] for row in cur.fetchall()]
            for doc_id in doc_ids:
                cur.execute("DELETE FROM chunks WHERE doc_id = %s", (doc_id,))
                cur.execute("DELETE FROM docs WHERE doc_id = %s", (doc_id,))
    return len(doc_ids)


def list_docs() -> list[dict[str, Any]]:
    """列出所有文档。"""
    pool = get_pool()
    sql = "SELECT * FROM docs ORDER BY created_at DESC"
    with pool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(sql)
            return cur.fetchall()


def count_by_course() -> dict[str, int]:
    """按课程统计文档数量。"""
    pool = get_pool()
    sql = "SELECT course, COUNT(*) as cnt FROM docs GROUP BY course"
    with pool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(sql)
            rows = cur.fetchall()
            return {row["course"]: row["cnt"] for row in rows}


# ── 混合检索 ──────────────────────────────────────────────────────

def search(
    query_vec: list[float],
    query_text: str,
    course: str | None = None,
    top_k: int = 10,
    weight_vec: float = SCORE_WEIGHT_VEC,
    weight_kw: float = SCORE_WEIGHT_KW,
) -> list[dict[str, Any]]:
    """混合检索：向量近邻 + 全文匹配 + 结构化过滤。

    最终得分 = weight_vec * (1 - dist) + weight_kw * normalized_kw_score

    Args:
        query_vec: 查询向量
        query_text: 查询文本（用于全文检索）
        course: 课程过滤（可选）
        top_k: 返回数量
        weight_vec: 向量得分权重
        weight_kw: 关键词得分权重

    Returns:
        [{chunk_id, doc_id, heading, content, title, source, dist, kw_score, score}, ...]
    """
    pool = get_pool()
    vec_str = _format_vector(query_vec)

    # 构建 SQL
    where_clauses = []
    params: list[Any] = []

    if course:
        where_clauses.append("c.course = %s")
        params.append(course)

    where_sql = " AND ".join(where_clauses) if where_clauses else "1=1"

    sql = f"""
    SELECT
        c.chunk_id,
        c.doc_id,
        c.heading,
        c.content,
        d.title,
        d.source,
        c.course,
        VECTOR_DISTANCE(c.embedding, %s) AS dist,
        MATCH(c.content) AGAINST (%s IN NATURAL LANGUAGE MODE) AS kw_score
    FROM chunks c
    JOIN docs d ON d.doc_id = c.doc_id
    WHERE {where_sql}
    ORDER BY (%s * (1 - VECTOR_DISTANCE(c.embedding, %s))
            + %s * MATCH(c.content) AGAINST (%s IN NATURAL LANGUAGE MODE)) DESC
    LIMIT %s
    """

    # 参数顺序必须与 SQL 中占位符出现的先后顺序一致：
    #   SELECT(VECTOR_DISTANCE, MATCH) → WHERE(course) → ORDER BY(权重, VECTOR_DISTANCE, 权重, MATCH) → LIMIT
    # 否则带课程过滤时所有参数会整体错位，导致检索恒为空。
    # 多取一些再去重：同一份内容被重复灌入时，LIMIT top_k 可能全被重复行占满，
    # 去重后不足 top_k 条，因此这里按 3 倍取，去重后再截断。
    fetch_limit = min(max(top_k * 3, top_k), 100)
    final_params = [
        vec_str,           # 1. SELECT 中的 VECTOR_DISTANCE
        query_text,        # 2. SELECT 中的 MATCH
    ] + params + [        # 3. WHERE 中的课程过滤
        weight_vec,        # 4. ORDER BY 权重（向量）
        vec_str,           # 5. ORDER BY 中的 VECTOR_DISTANCE
        weight_kw,         # 6. ORDER BY 权重（关键词）
        query_text,        # 7. ORDER BY 中的 MATCH
        fetch_limit,       # 8. LIMIT
    ]

    with pool.connection() as conn:
        with conn.cursor() as cur:
            try:
                cur.execute(sql, final_params)
                rows = cur.fetchall()
            except pymysql.err.OperationalError as exc:
                logger.error("检索失败: %s", exc)
                raise DBError(f"混合检索失败: {exc}") from exc

    # 归一化 kw_score 并计算最终得分
    if rows:
        max_kw = max(row["kw_score"] for row in rows) or 1.0
        for row in rows:
            row["kw_score_norm"] = row["kw_score"] / max_kw
            row["score"] = (
                weight_vec * (1 - row["dist"])
                + weight_kw * row["kw_score_norm"]
            )

    # 去重：同一 chunk 可能因向量/全文多次参与排序而重复出现；同一份内容被重复
    # 灌入（例如重复执行 seed 脚本）时也会产生重复行。这里按 (标题+正文) 去重，
    # 保留得分最高的一条，再按得分降序返回。
    deduped: dict[str, dict[str, Any]] = {}
    for row in rows:
        key = f"{row.get('title', '')}\x00{row.get('heading', '')}\x00{row.get('content', '')}"
        if key not in deduped or row["score"] > deduped[key]["score"]:
            deduped[key] = row
    result = sorted(deduped.values(), key=lambda r: r["score"], reverse=True)
    return result[:top_k]
