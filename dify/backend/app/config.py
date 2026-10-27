"""配置：全部来自环境变量，仓库里只提交 .env.example（不含真实密钥）。"""
from __future__ import annotations

import os
from pathlib import Path


def _load_dotenv() -> None:
    """极简 .env 加载（不依赖 python-dotenv）：已存在的环境变量优先。"""
    here = Path(__file__).resolve().parents[1]  # dify/backend
    for candidate in (here / ".env", here.parent / ".env"):
        if not candidate.exists():
            continue
        for raw in candidate.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))
        break


_load_dotenv()


def _env(key: str, default: str = "") -> str:
    return os.getenv(key, default).strip()


def _env_int(key: str, default: int) -> int:
    raw = _env(key)
    try:
        return int(raw) if raw else default
    except ValueError:
        return default


class Settings:
    # ---- seekdb（MySQL 协议）----
    db_host: str = _env("SEEKDB_HOST", "127.0.0.1")
    db_port: int = _env_int("SEEKDB_PORT", 2881)
    db_user: str = _env("SEEKDB_USER", "root")
    db_password: str = _env("SEEKDB_PASSWORD", "")
    db_name: str = _env("DB_NAME", "nightstore")

    # ---- embedding ----
    embed_provider: str = _env("EMBED_PROVIDER", "local")  # local | minimax
    embed_dim: int = _env_int("EMBED_DIM", 1536)
    minimax_base_url: str = _env("MINIMAX_BASE_URL", "https://api.minimax.chat/v1")
    minimax_api_key: str = _env("MINIMAX_API_KEY", "")
    minimax_embed_model: str = _env("MINIMAX_EMBED_MODEL", "embo-01")

    # ---- 检索 ----
    top_k: int = _env_int("TOP_K", 5)
    rrf_constant: int = _env_int("RRF_CONSTANT", 60)

    # ---- PowerContext（仅用于健康检查与可选代理）----
    powercontext_base_url: str = _env("POWERCONTEXT_BASE_URL", "http://127.0.0.1:8848")
    powercontext_api_key: str = _env("POWERCONTEXT_API_KEY", "")


settings = Settings()
