"""混合检索：向量 + 全文 + 结构化过滤。

优先使用 seekdb 1.4 的 ``DBMS_HYBRID_SEARCH.SEARCH()``（ES 风格 DSL + RRF 融合）；
若当前实例不支持该函数，自动回退到"加权排序"的原生 SQL（与之前实测过的写法一致）。
两条路径都会返回：命中内容 + 语义分 + 关键词分 + 结构化过滤条件。
"""
from __future__ import annotations

import json
from typing import Any

from .config import settings
from .db import fetch_all
from .embed import embed_query, vec_literal

_HYBRID_FALLBACK_MARK = "hybrid_search_unavailable"


def _as_list(value) -> list[str]:
    """kinds/tags 既可以是数组，也可以是逗号分隔字符串（DSL 里用字符串更方便）。"""
    if value is None:
        return []
    if isinstance(value, str):
        return [v.strip() for v in value.split(",") if v.strip()]
    return [str(v).strip() for v in value if str(v).strip()]


def _dsl(
    query: str,
    kinds: list[str],
    tags: list[str],
    min_affection: int | None,
    max_affection: int | None,
    min_pranks: int | None,
    top_k: int,
    query_vec: list[float],
) -> dict:
    """构造 DBMS_HYBRID_SEARCH 的 ES 风格 DSL。

    注意：不同 seekdb 小版本对过滤子句的支持有差异，这里只使用最稳的 ``term`` / ``range``，
    并且**在 Python 侧再做一次过滤兜底**（见 search()），避免"过滤没生效"导致串味。
    """
    filters: list[dict] = []
    if kinds:
        filters.append({"term": {"kind": kinds[0]}})
    if tags:
        filters.append({"term": {"tag": tags[0]}})
    if min_affection is not None:
        filters.append({"range": {"cond_min": {"lte": min_affection}}})
    if max_affection is not None:
        filters.append({"range": {"cond_max": {"gte": max_affection}}})
    if min_pranks is not None:
        filters.append({"range": {"cond_pranks": {"lte": min_pranks}}})

    must: list[dict] = []
    if query.strip():
        must.append({"match": {"content": query}})

    bool_query: dict = {}
    if must:
        bool_query["must"] = must
    if filters:
        bool_query["filter"] = filters

    dsl = {
        "knn": {"field": "embedding", "k": max(top_k * 3, 10), "query_vector": query_vec},
        "rank": {"rrf": {"rank_constant": settings.rrf_constant, "rank_window_size": max(top_k * 4, 20)}},
        "_source": [
            "id",
            "kind",
            "title",
            "content",
            "tag",
            "weight",
            "cond_min",
            "cond_max",
            "cond_pranks",
            "_keyword_score",
            "_semantic_score",
        ],
        # 多取一些，Python 侧过滤后再截断
        "size": max(top_k * 3, top_k),
    }
    # 注意：空的 must 数组会让 DBMS_HYBRID_SEARCH 报 1210，所以有内容才写 query
    if bool_query:
        dsl["query"] = {"bool": bool_query}
    return dsl


def _normalize_hybrid(hits: Any) -> list[dict]:
    if isinstance(hits, dict):
        hits = hits.get("hits", hits.get("results", []))
    out: list[dict] = []
    for hit in hits or []:
        src = hit.get("_source", hit) if isinstance(hit, dict) else {}
        row = {
            "id": src.get("id"),
            "kind": src.get("kind"),
            "title": src.get("title"),
            "content": src.get("content"),
            "tag": src.get("tag"),
            "weight": src.get("weight"),
            "cond_min": src.get("cond_min"),
            "cond_max": src.get("cond_max"),
            "semantic_score": hit.get("_semantic_score") if isinstance(hit, dict) else None,
            "keyword_score": hit.get("_keyword_score") if isinstance(hit, dict) else None,
        }
        row["score"] = row["semantic_score"] if row["semantic_score"] is not None else row["keyword_score"]
        out.append(row)
    return out


def _fallback_search(
    cur,
    query: str,
    kinds: list[str],
    tags: list[str],
    min_affection: int | None,
    max_affection: int | None,
    min_pranks: int | None,
    top_k: int,
    query_vec: list[float],
) -> list[dict]:
    """原生 SQL 兜底：向量近邻 + 全文 BM25 + 结构化过滤，0.7/0.3 加权。"""
    where: list[str] = ["1 = 1"]
    args: list[Any] = []
    if kinds:
        where.append("kind IN (" + ",".join(["%s"] * len(kinds)) + ")")
        args.extend(kinds)
    if tags:
        where.append("tag IN (" + ",".join(["%s"] * len(tags)) + ")")
        args.extend(tags)
    if min_affection is not None:
        where.append("cond_min <= %s")
        args.append(min_affection)
    if max_affection is not None:
        where.append("cond_max >= %s")
        args.append(max_affection)
    if min_pranks is not None:
        where.append("cond_pranks <= %s")
        args.append(min_pranks)

    sql = (
        "SELECT id, kind, title, content, tag, weight, cond_min, cond_max, cond_pranks, "
        "VECTOR_DISTANCE(embedding, %s) AS vdist, "
        "MATCH(content) AGAINST (%s IN NATURAL LANGUAGE MODE) AS kw "
        "FROM lore WHERE " + " AND ".join(where) +
        " ORDER BY (0.7 * (1 - VECTOR_DISTANCE(embedding, %s)) "
        "+ 0.3 * MATCH(content) AGAINST (%s IN NATURAL LANGUAGE MODE)) DESC LIMIT %s"
    )
    vec = vec_literal(query_vec)
    rows = fetch_all(cur, sql, [vec, query, *args, vec, query, top_k])
    out = []
    for r in rows:
        out.append(
            {
                "id": r["id"],
                "kind": r["kind"],
                "title": r["title"],
                "content": r["content"],
                "tag": r["tag"],
                "weight": r["weight"],
                "cond_min": r["cond_min"],
                "cond_max": r["cond_max"],
                "cond_pranks": r.get("cond_pranks"),
                "semantic_score": round(1 - float(r["vdist"]), 6) if r["vdist"] is not None else None,
                "keyword_score": float(r["kw"]) if r["kw"] is not None else 0.0,
                "score": round(0.7 * (1 - float(r["vdist"])) + 0.3 * float(r["kw"] or 0), 6),
            }
        )
    return out


def search(
    cur,
    query: str,
    kinds=None,
    tags=None,
    min_affection: int | None = None,
    max_affection: int | None = None,
    min_pranks: int | None = None,
    top_k: int | None = None,
) -> dict:
    """返回 {"hits": [...], "mode": "hybrid_rrf"|"fallback_sql", "query": ...}。"""
    kinds = _as_list(kinds)
    tags = _as_list(tags)
    top_k = top_k or settings.top_k
    query_vec = embed_query(query)
    dsl = _dsl(query, kinds, tags, min_affection, max_affection, min_pranks, top_k, query_vec)

    used_mode = "hybrid_rrf"
    sql_text = "SELECT DBMS_HYBRID_SEARCH.SEARCH('lore', @p);"
    try:
        payload = json.dumps(dsl, ensure_ascii=False).replace("\\", "\\\\").replace("'", "''")
        cur.execute(f"SET @p = '{payload}'")
        cur.execute(sql_text)
        raw = cur.fetchone()
        value = list(raw.values())[0] if raw else None
        hits = _normalize_hybrid(json.loads(value) if isinstance(value, (str, bytes)) else value)
        if not hits and not query.strip() and not kinds and not tags:
            raise RuntimeError(_HYBRID_FALLBACK_MARK)
    except Exception:  # noqa: BLE001 - 任何异常都退回原生 SQL，保证功能可用
        used_mode = "fallback_sql"
        hits = _fallback_search(
            cur, query, kinds, tags, min_affection, max_affection, min_pranks, top_k, query_vec
        )

    # Python 侧兜底过滤（部分 seekdb 版本对 DSL 过滤子句支持不一致）
    if kinds:
        hits = [h for h in hits if h.get("kind") in kinds]
    if tags:
        hits = [h for h in hits if h.get("tag") in tags]
    if min_affection is not None:
        hits = [h for h in hits if int(h.get("cond_min", -999)) <= min_affection]
    if max_affection is not None:
        hits = [h for h in hits if int(h.get("cond_max", 999)) >= max_affection]
    if min_pranks is not None:
        hits = [h for h in hits if int(h.get("cond_pranks", 0) or 0) <= min_pranks]
    hits = hits[:top_k]

    return {
        "hits": hits,
        "mode": used_mode,
        "query": query,
        "kinds": kinds,
        "tags": tags,
        "top_k": top_k,
        "embed_provider": settings.embed_provider,
    }
