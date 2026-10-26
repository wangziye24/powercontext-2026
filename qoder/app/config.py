"""应用配置：从 .env 读取所有环境变量，集中管理模块级常量。"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

# 加载项目根目录的 .env（qoder/.env）
_ENV_PATH = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(_ENV_PATH)


def _env(key: str, default: str = "") -> str:
    """读取环境变量，缺失时返回默认值。"""
    return os.getenv(key, default)


# ── MiniMax ──────────────────────────────────────────────────────
MINIMAX_API_KEY: str = _env("MINIMAX_API_KEY")
MINIMAX_BASE_URL: str = _env("MINIMAX_BASE_URL", "https://api.minimax.chat/v1")
MINIMAX_EMBED_MODEL: str = _env("MINIMAX_EMBED_MODEL", "embo-01")
MINIMAX_CHAT_MODEL: str = _env("MINIMAX_CHAT_MODEL", "MiniMax-M2.5")

# ── Embedding ────────────────────────────────────────────────────
EMBED_PROVIDER: str = _env("EMBED_PROVIDER", "minimax")  # minimax | local
EMBED_DIM: int = 1536 if EMBED_PROVIDER == "minimax" else 512

# ── seekdb (MySQL 协议) ──────────────────────────────────────────
SEEKDB_HOST: str = _env("SEEKDB_HOST", "127.0.0.1")
SEEKDB_PORT: int = int(_env("SEEKDB_PORT", "2881"))
SEEKDB_USER: str = _env("SEEKDB_USER", "root")
SEEKDB_PASSWORD: str = _env("SEEKDB_PASSWORD")
DB_NAME: str = _env("DB_NAME", "coursenote")

# ── 混合检索权重 ─────────────────────────────────────────────────
SCORE_WEIGHT_VEC: float = float(_env("SCORE_WEIGHT_VEC", "0.7"))
SCORE_WEIGHT_KW: float = float(_env("SCORE_WEIGHT_KW", "0.3"))

# ── PowerContext 记忆（可选） ────────────────────────────────────
ENABLE_MEMORY: bool = _env("ENABLE_MEMORY", "false").lower() == "true"
POWCONTEXT_BASE_URL: str = _env("POWCONTEXT_BASE_URL", "http://127.0.0.1:8000")
POWCONTEXT_API_KEY: str = _env("POWCONTEXT_API_KEY")
