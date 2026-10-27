"""侧车服务包。

PyMySQL 采用 **vendor 内置**（MIT License，见 vendor/pymysql/LICENSE），
这样评委侧不需要 pip install、也不需要外网，``python3 -m app.main`` 即可启动。
若环境中已有 pymysql，则优先使用已安装的版本。
"""
from __future__ import annotations

import sys
from pathlib import Path

try:  # pragma: no cover - 取决于运行环境
    import pymysql  # noqa: F401
except ImportError:  # pragma: no cover
    _VENDOR = Path(__file__).resolve().parents[1] / "vendor"
    if str(_VENDOR) not in sys.path:
        sys.path.insert(0, str(_VENDOR))
