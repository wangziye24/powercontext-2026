"""MiniMax Chat 调用封装。

MiniMax chat 接口兼容 OpenAI 格式：
POST {MINIMAX_BASE_URL}/chat/completions
Body: {"model": "MiniMax-M2.5", "messages": [...]}
"""

from __future__ import annotations

import logging
import time
from typing import Any

import httpx

from app.config import MINIMAX_API_KEY, MINIMAX_BASE_URL, MINIMAX_CHAT_MODEL

logger = logging.getLogger(__name__)

# ── 常量 ──────────────────────────────────────────────────────────
_MAX_RETRIES = 2
_BACKOFF_BASE = 1.0
_TIMEOUT = 60.0


class LLMError(Exception):
    """LLM 调用异常基类。"""


def _strip_think(text: str) -> str:
    """去掉模型输出中的 <think>...</think> 思考块，只保留最终答案。"""
    if "</think>" in text:
        text = text.split("</think>", 1)[1]
    if "<think>" in text:
        text = text.split("<think>", 1)[0]
    return text.strip()


class MiniMaxChat:
    """MiniMax Chat API 封装。"""

    def __init__(
        self,
        api_key: str = MINIMAX_API_KEY,
        base_url: str = MINIMAX_BASE_URL,
        model: str = MINIMAX_CHAT_MODEL,
    ) -> None:
        if not api_key:
            raise LLMError("MINIMAX_API_KEY 未配置")
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._url = f"{self._base_url}/chat/completions"

    def chat(
        self,
        messages: list[dict[str, str]],
        temperature: float = 0.7,
        max_tokens: int = 1024,
    ) -> str:
        """调用 chat API 生成回复。

        Args:
            messages: OpenAI 格式的消息列表
            temperature: 温度参数
            max_tokens: 最大生成长度

        Returns:
            生成的文本
        """
        payload: dict[str, Any] = {
            "model": self._model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
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
            except (httpx.HTTPStatusError, httpx.RequestError, LLMError) as exc:
                last_exc = exc
                if attempt < _MAX_RETRIES:
                    wait = _BACKOFF_BASE * (2 ** attempt)
                    logger.warning(
                        "MiniMax chat 第 %d 次重试（%s），等待 %.1fs",
                        attempt + 1, exc, wait,
                    )
                    time.sleep(wait)

        raise LLMError(f"MiniMax chat 调用失败（已重试 {_MAX_RETRIES} 次）: {last_exc}")

    def _parse_response(self, data: dict[str, Any]) -> str:
        """解析响应，提取生成的文本。"""
        choices = data.get("choices", [])
        if not choices:
            raise LLMError(f"MiniMax 返回 choices 为空: {data}")
        message = choices[0].get("message", {})
        content = message.get("content", "")
        if not content:
            raise LLMError(f"MiniMax 返回 content 为空: {data}")
        return _strip_think(content).strip()


def build_qa_messages(context: str, question: str) -> list[dict[str, str]]:
    """构建问答消息。

    Args:
        context: 检索到的上下文
        question: 用户问题

    Returns:
        消息列表
    """
    system_prompt = (
        "你是学习资料助手，只依据给定资料回答，并标注来源。\n\n"
        f"【资料片段】\n{context}\n\n"
        "请根据以上资料回答问题。如果资料中没有相关信息，请明确说明。"
    )
    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": question},
    ]
