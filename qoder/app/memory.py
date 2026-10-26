"""PowerContext 记忆集成：记录用户课程偏好、检索历史、薄弱点。

通过 HTTP API 与 PowerContext 服务交互，在 /api/search 和 /api/ask 中
作为附加上下文，提升个性化体验。

当 PowerContext 未配置或不可用时，自动降级为不使用记忆。
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from app.config import ENABLE_MEMORY, POWCONTEXT_API_KEY, POWCONTEXT_BASE_URL

logger = logging.getLogger(__name__)

# ── 常量 ──────────────────────────────────────────────────────────
_TIMEOUT = 10.0


class MemoryError(Exception):
    """记忆服务异常基类。"""


class PowerContextMemory:
    """PowerContext 记忆服务封装。"""

    def __init__(
        self,
        base_url: str = POWCONTEXT_BASE_URL,
        api_key: str = POWCONTEXT_API_KEY,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._enabled = ENABLE_MEMORY and bool(base_url)

    @property
    def enabled(self) -> bool:
        """是否启用记忆功能。"""
        return self._enabled

    def remember(self, user_id: str, kind: str, content: str) -> bool:
        """写入记忆。

        Args:
            user_id: 用户 ID
            kind: 记忆类型（preference / history / weakness）
            content: 记忆内容

        Returns:
            是否成功
        """
        if not self._enabled:
            return False

        try:
            with httpx.Client(timeout=_TIMEOUT) as client:
                headers = {}
                if self._api_key:
                    headers["Authorization"] = f"Bearer {self._api_key}"

                resp = client.post(
                    f"{self._base_url}/api/memory",
                    json={
                        "user_id": user_id,
                        "kind": kind,
                        "content": content,
                    },
                    headers=headers,
                )
                resp.raise_for_status()
                return True
        except Exception as exc:
            logger.warning("写入记忆失败: %s", exc)
            return False

    def recall(self, user_id: str, kind: str | None = None, limit: int = 5) -> list[dict[str, Any]]:
        """取回记忆。

        Args:
            user_id: 用户 ID
            kind: 记忆类型过滤（可选）
            limit: 返回数量

        Returns:
            记忆列表
        """
        if not self._enabled:
            return []

        try:
            with httpx.Client(timeout=_TIMEOUT) as client:
                headers = {}
                if self._api_key:
                    headers["Authorization"] = f"Bearer {self._api_key}"

                params = {"user_id": user_id, "limit": limit}
                if kind:
                    params["kind"] = kind

                resp = client.get(
                    f"{self._base_url}/api/memory",
                    params=params,
                    headers=headers,
                )
                resp.raise_for_status()
                data = resp.json()
                return data.get("memories", [])
        except Exception as exc:
            logger.warning("取回记忆失败: %s", exc)
            return []


# ── 全局实例 ──────────────────────────────────────────────────────

_memory: PowerContextMemory | None = None


def get_memory() -> PowerContextMemory:
    """获取记忆服务实例。"""
    global _memory
    if _memory is None:
        _memory = PowerContextMemory()
        if _memory.enabled:
            logger.info("PowerContext 记忆功能已启用")
        else:
            logger.info("PowerContext 记忆功能未启用（ENABLE_MEMORY=false 或未配置）")
    return _memory


def build_memory_context(memories: list[dict[str, Any]]) -> str:
    """将记忆列表格式化为上下文字符串。

    Args:
        memories: 记忆列表

    Returns:
        格式化后的上下文
    """
    if not memories:
        return ""

    lines = ["【用户记忆】"]
    for m in memories:
        kind = m.get("kind", "unknown")
        content = m.get("content", "")
        lines.append(f"- [{kind}] {content}")

    return "\n".join(lines)
