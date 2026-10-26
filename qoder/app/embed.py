"""Embedding 封装：支持 minimax（云端）与 local（离线 fastembed）两种 provider。

MiniMax embedding 接口不是 OpenAI 兼容格式，必须使用 ``texts`` 字段（而非 ``input``）。
请求体示例::

    POST {MINIMAX_BASE_URL}/embeddings
    {"model": "embo-01", "texts": ["文本1"], "type": "db"}

响应体示例::

    {"vectors": [[...1536维...]], "total_tokens": N, "base_resp": {...}}
"""

from __future__ import annotations

import logging
import time
from abc import ABC, abstractmethod
from typing import Any

import httpx

from app.config import (
    EMBED_DIM,
    EMBED_PROVIDER,
    MINIMAX_API_KEY,
    MINIMAX_BASE_URL,
    MINIMAX_EMBED_MODEL,
)

logger = logging.getLogger(__name__)

# ── 常量 ──────────────────────────────────────────────────────────
_MAX_RETRIES = 2
_BACKOFF_BASE = 1.0  # 秒
_TIMEOUT = 20.0  # 秒


class EmbeddingError(Exception):
    """Embedding 调用异常基类。"""


class EmbedderBase(ABC):
    """Embedder 抽象基类。"""

    @property
    @abstractmethod
    def dim(self) -> int:
        """返回向量维度。"""

    @abstractmethod
    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        """对文档列表进行 embedding，返回向量列表。"""

    @abstractmethod
    def embed_query(self, text: str) -> list[float]:
        """对单条查询进行 embedding，返回向量。"""


class MiniMaxEmbedder(EmbedderBase):
    """MiniMax 云端 Embedding（embo-01，1536 维）。"""

    def __init__(
        self,
        api_key: str = MINIMAX_API_KEY,
        base_url: str = MINIMAX_BASE_URL,
        model: str = MINIMAX_EMBED_MODEL,
        expected_dim: int = EMBED_DIM,
    ) -> None:
        if not api_key:
            raise EmbeddingError("MINIMAX_API_KEY 未配置")
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._expected_dim = expected_dim
        self._url = f"{self._base_url}/embeddings"

    @property
    def dim(self) -> int:
        return self._expected_dim

    def _call_api(self, texts: list[str], req_type: str) -> list[list[float]]:
        """调用 MiniMax embedding API，带重试与指数退避。"""
        payload: dict[str, Any] = {
            "model": self._model,
            "texts": texts,
            "type": req_type,
        }
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }

        last_exc: Exception | None = None
        for attempt in range(_MAX_RETRIES + 1):
            try:
                with httpx.Client(timeout=_TIMEOUT) as client:
                    resp = client.post(self._url, json=payload, headers=headers)
                    resp.raise_for_status()
                    data = resp.json()
                return self._parse_response(data)
            except (httpx.HTTPStatusError, httpx.RequestError, EmbeddingError) as exc:
                last_exc = exc
                if attempt < _MAX_RETRIES:
                    wait = _BACKOFF_BASE * (2 ** attempt)
                    logger.warning(
                        "MiniMax embedding 第 %d 次重试（%s），等待 %.1fs",
                        attempt + 1, exc, wait,
                    )
                    time.sleep(wait)

        raise EmbeddingError(f"MiniMax embedding 调用失败（已重试 {_MAX_RETRIES} 次）: {last_exc}")

    def _parse_response(self, data: dict[str, Any]) -> list[list[float]]:
        """解析响应，校验向量维度。"""
        vectors = data.get("vectors")
        if not vectors:
            raise EmbeddingError(f"MiniMax 返回 vectors 为空: {data.get('base_resp')}")
        for i, vec in enumerate(vectors):
            if len(vec) != self._expected_dim:
                raise EmbeddingError(
                    f"向量维度不匹配：期望 {self._expected_dim}，"
                    f"第 {i} 条实际 {len(vec)}"
                )
        return vectors

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        """对文档进行 embedding（type=db）。"""
        if not texts:
            return []
        return self._call_api(texts, "db")

    def embed_query(self, text: str) -> list[float]:
        """对查询进行 embedding（type=query）。"""
        vecs = self._call_api([text], "query")
        return vecs[0]


class LocalEmbedder(EmbedderBase):
    """离线 Embedding（fastembed bge-small-zh-v1.5，512 维）。"""

    _MODEL_NAME = "BAAI/bge-small-zh-v1.5"
    _DIM = 512

    def __init__(self) -> None:
        try:
            from fastembed import TextEmbedding  # type: ignore[import-untyped]
        except ImportError as exc:
            raise EmbeddingError(
                "使用 local provider 需要安装 fastembed：pip install fastembed"
            ) from exc
        self._model = TextEmbedding(model_name=self._MODEL_NAME)

    @property
    def dim(self) -> int:
        return self._DIM

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        """对文档进行 embedding。"""
        if not texts:
            return []
        embeddings = list(self._model.embed(texts))
        return [emb.tolist() for emb in embeddings]

    def embed_query(self, text: str) -> list[float]:
        """对查询进行 embedding。"""
        embeddings = list(self._model.embed([text]))
        return embeddings[0].tolist()


# ── 工厂函数 ──────────────────────────────────────────────────────

def create_embedder(provider: str = EMBED_PROVIDER) -> EmbedderBase:
    """根据配置创建 Embedder 实例。"""
    if provider == "local":
        logger.info("使用 local embedding provider（bge-small-zh-v1.5, %d 维）", LocalEmbedder._DIM)
        return LocalEmbedder()
    logger.info("使用 MiniMax embedding provider（%s, %d 维）", MINIMAX_EMBED_MODEL, EMBED_DIM)
    return MiniMaxEmbedder()
