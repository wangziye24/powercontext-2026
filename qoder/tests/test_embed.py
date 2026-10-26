"""embed 单元测试：使用 mock httpx 验证 MiniMax 请求格式。"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import httpx
import pytest

from app.embed import EmbeddingError, MiniMaxEmbedder


def _mock_response(vectors: list[list[float]], status_code: int = 200) -> httpx.Response:
    """构造 mock httpx 响应。"""
    body = json.dumps({
        "vectors": vectors,
        "total_tokens": 10,
        "base_resp": {"status_code": 0, "status_msg": "success"},
    }).encode()
    request = httpx.Request("POST", "https://api.minimax.chat/v1/embeddings")
    resp = httpx.Response(status_code=status_code, content=body, request=request)
    return resp


class TestMiniMaxEmbedder:
    """MiniMaxEmbedder 测试。"""

    def test_embed_documents_uses_texts_field(self) -> None:
        """验证请求体使用 texts 字段（非 OpenAI 的 input）。"""
        embedder = MiniMaxEmbedder(api_key="test-key", expected_dim=4)
        captured: dict = {}

        def mock_post(url: str, json: dict, headers: dict) -> httpx.Response:
            captured["url"] = url
            captured["json"] = json
            captured["headers"] = headers
            return _mock_response([[0.1, 0.2, 0.3, 0.4], [0.5, 0.6, 0.7, 0.8]])

        with patch.object(httpx.Client, "post", side_effect=mock_post):
            result = embedder.embed_documents(["文本1", "文本2"])

        assert "texts" in captured["json"], "请求体必须包含 texts 字段"
        assert "input" not in captured["json"], "请求体不应包含 input 字段"
        assert captured["json"]["texts"] == ["文本1", "文本2"]
        assert captured["json"]["type"] == "db"
        assert captured["json"]["model"] == "embo-01"
        assert captured["headers"]["Authorization"] == "Bearer test-key"
        assert len(result) == 2
        assert result[0] == [0.1, 0.2, 0.3, 0.4]

    def test_embed_query_uses_query_type(self) -> None:
        """验证查询使用 type=query。"""
        embedder = MiniMaxEmbedder(api_key="test-key", expected_dim=4)
        captured: dict = {}

        def mock_post(url: str, json: dict, headers: dict) -> httpx.Response:
            captured["json"] = json
            return _mock_response([[0.1, 0.2, 0.3, 0.4]])

        with patch.object(httpx.Client, "post", side_effect=mock_post):
            result = embedder.embed_query("查询文本")

        assert captured["json"]["type"] == "query"
        assert captured["json"]["texts"] == ["查询文本"]
        assert result == [0.1, 0.2, 0.3, 0.4]

    def test_raises_on_empty_vectors(self) -> None:
        """vectors 为空时抛出异常。"""
        embedder = MiniMaxEmbedder(api_key="test-key", expected_dim=4)

        def mock_post(url: str, json: dict, headers: dict) -> httpx.Response:
            return _mock_response([])

        with patch.object(httpx.Client, "post", side_effect=mock_post):
            with pytest.raises(EmbeddingError, match="vectors 为空"):
                embedder.embed_documents(["文本"])

    def test_raises_on_dimension_mismatch(self) -> None:
        """向量维度不匹配时抛出异常。"""
        embedder = MiniMaxEmbedder(api_key="test-key", expected_dim=4)

        def mock_post(url: str, json: dict, headers: dict) -> httpx.Response:
            return _mock_response([[0.1, 0.2]])  # 只有 2 维

        with patch.object(httpx.Client, "post", side_effect=mock_post):
            with pytest.raises(EmbeddingError, match="维度不匹配"):
                embedder.embed_documents(["文本"])

    def test_retry_on_failure(self) -> None:
        """失败时重试，指数退避。"""
        embedder = MiniMaxEmbedder(api_key="test-key", expected_dim=4)
        call_count = 0

        def mock_post(url: str, json: dict, headers: dict) -> httpx.Response:
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise httpx.ConnectError("连接失败")
            return _mock_response([[0.1, 0.2, 0.3, 0.4]])

        with patch.object(httpx.Client, "post", side_effect=mock_post):
            with patch("app.embed.time.sleep"):  # 跳过实际等待
                result = embedder.embed_documents(["文本"])

        assert call_count == 3  # 初始 1 次 + 重试 2 次
        assert result == [[0.1, 0.2, 0.3, 0.4]]

    def test_raises_after_max_retries(self) -> None:
        """超过最大重试次数后抛出异常。"""
        embedder = MiniMaxEmbedder(api_key="test-key", expected_dim=4)

        def mock_post(url: str, json: dict, headers: dict) -> httpx.Response:
            raise httpx.ConnectError("连接失败")

        with patch.object(httpx.Client, "post", side_effect=mock_post):
            with patch("app.embed.time.sleep"):
                with pytest.raises(EmbeddingError, match="调用失败"):
                    embedder.embed_documents(["文本"])

    def test_embed_documents_empty_input(self) -> None:
        """空输入返回空列表。"""
        embedder = MiniMaxEmbedder(api_key="test-key", expected_dim=4)
        assert embedder.embed_documents([]) == []

    def test_dim_property(self) -> None:
        """dim 属性返回期望维度。"""
        embedder = MiniMaxEmbedder(api_key="test-key", expected_dim=1536)
        assert embedder.dim == 1536
