"""Embedding 封装。

两种 provider：
- ``local``  ：确定性哈希向量（字符 + 双字组合），**完全离线**，用于"评委手里没有 embedding Key"的情况。
- ``minimax``：MiniMax ``embo-01``（1536 维），需要 ``MINIMAX_API_KEY``。

注意：向量维度必须与 seekdb 列定义 ``VECTOR(1536)`` 一致；
**更换 provider 后必须重新灌种子数据或调用 ``/admin/reindex``**，否则查询向量与库内向量不同空间。
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import urllib.error
import urllib.request

from .config import settings

_TOKEN_RE = re.compile(r"[a-z0-9_]+|[一-鿿]")


def embed_local(text: str, dim: int | None = None) -> list[float]:
    """确定性哈希向量：英文按词、中文按字 + 双字组合，L2 归一化。"""
    dim = dim or settings.embed_dim
    text = (text or "").lower().strip()
    tokens = _TOKEN_RE.findall(text)
    bigrams = [a + b for a, b in zip(tokens, tokens[1:]) if len(a) == len(b)]
    vec = [0.0] * dim
    for tok in tokens + bigrams:
        h = hashlib.md5(tok.encode("utf-8")).digest()
        idx = int.from_bytes(h[:4], "little") % dim
        sign = 1.0 if h[4] % 2 == 0 else -1.0
        vec[idx] += sign
    norm = math.sqrt(sum(v * v for v in vec)) or 1.0
    return [v / norm for v in vec]


def embed_minimax(texts: list[str]) -> list[list[float]]:
    """MiniMax embeddings（注意：不是 OpenAI 兼容格式，用 ``texts`` 字段）。"""
    if not settings.minimax_api_key:
        raise RuntimeError("EMBED_PROVIDER=minimax 但未配置 MINIMAX_API_KEY")
    body = json.dumps(
        {"model": settings.minimax_embed_model, "texts": texts, "type": "db"}
    ).encode("utf-8")
    req = urllib.request.Request(
        settings.minimax_base_url.rstrip("/") + "/embeddings",
        data=body,
        headers={
            "Content-Type": "application/json",
            "Authorization": "Bearer " + settings.minimax_api_key,
        },
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    vectors = payload.get("vectors") or payload.get("data")
    if vectors is None:
        raise RuntimeError(f"MiniMax embeddings 返回异常: {list(payload)}")
    return vectors


def embed_texts(texts: list[str]) -> list[list[float]]:
    if settings.embed_provider == "minimax":
        return embed_minimax(texts)
    return [embed_local(t) for t in texts]


def embed_query(text: str) -> list[float]:
    if settings.embed_provider == "minimax":
        return embed_minimax([text])[0]
    return embed_local(text)


def vec_literal(vec: list[float]) -> str:
    """seekdb 向量字面量：必须带方括号，且长度等于列维度。"""
    return "[" + ",".join("%.6f" % v for v in vec) + "]"


def provider_name() -> str:
    return settings.embed_provider
