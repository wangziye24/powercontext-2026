#!/usr/bin/env python3
"""侧车启动入口（等价于 ``python3 -m app.main``，但可以在任意目录下直接跑）。

用法：
    python3 dify/backend/run.py

为什么提供这个：``python3 -m app.main`` 必须在 ``dify/backend`` 目录里执行，
新手最容易在这里踩空；本脚本会自动把包目录加进 sys.path。
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.main import main  # noqa: E402

if __name__ == "__main__":
    main()
