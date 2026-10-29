#!/usr/bin/env bash
# 准备 MiniMax × OceanBase 赛题运行环境（Linux / WSL Ubuntu）。
# 幂等：重复执行不会破坏已有虚拟环境。
#
# 说明：
#   * Python 3.11 ~ 3.14 都可运行（pylibseekdb 是 cp312-abi3 wheel）；默认用 3.12。
#   * 内嵌 seekdb 需要 fallocate，WSL 的 /mnt/c、/mnt/d 这类 Windows 挂载盘不支持，
#     所以数据目录（SDDCTX_HOME，默认 ~/.sddctx）必须放在 Linux 原生文件系统上。
#   * 依赖里已包含 pytest，装完即可直接跑 scripts/reproduce.sh。
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV="${SDDCTX_VENV:-$HOME/minimax-venv}"
PY_VERSION="${SDDCTX_PY_VERSION:-3.12}"
export PATH="$HOME/.local/bin:$PATH"

export SDDCTX_HOME="${SDDCTX_HOME:-$HOME/.sddctx}"
case "$SDDCTX_HOME" in
  /mnt/*)
    echo "[setup][fatal] SDDCTX_HOME=$SDDCTX_HOME 位于 Windows 挂载盘（/mnt/*）。"
    echo "               seekdb 依赖 fallocate，DrvFs/v9fs 不支持，open 会直接失败（errcode=-4009）。"
    echo "               请改用 \$HOME 下的目录：export SDDCTX_HOME=\"\$HOME/.sddctx\""
    exit 2
    ;;
esac

if ! command -v uv >/dev/null 2>&1; then
  echo "[setup] 安装 uv"
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="$HOME/.local/bin:$PATH"
fi

echo "[setup] 创建虚拟环境 $VENV (Python $PY_VERSION)"
uv python install "$PY_VERSION"
uv venv --python "$PY_VERSION" "$VENV" --allow-existing
# shellcheck disable=SC1091
source "$VENV/bin/activate"

echo "[setup] 安装 sddctx 及其依赖（powercontext[server,seekdb]==1.4.0, pytest）"
uv pip install -e "$HERE"

echo "[setup] python: $(python -V)"
python - <<'PY'
import pathlib
import shutil

import pylibseekdb
import powercontext
import pytest
import sqlalchemy

print("[setup] pylibseekdb   ->", pylibseekdb.__file__)
print("[setup] powercontext  ->", powercontext.__file__)
print("[setup] sqlalchemy    ->", sqlalchemy.__version__)
print("[setup] pytest        ->", pytest.__version__)

# seekdb 自检：在数据目录下真的开一次库，把文件系统不兼容的问题提前暴露。
import os

home = pathlib.Path(os.environ.get("SDDCTX_HOME", pathlib.Path.home() / ".sddctx"))
probe = home / "seekdb-probe"
shutil.rmtree(probe, ignore_errors=True)
instance = pylibseekdb.open(str(probe))
instance.close()
shutil.rmtree(probe, ignore_errors=True)
print("[setup] seekdb 自检    -> OK（数据目录", home, "可用）")
PY
echo "[setup] 完成。后续：bash scripts/reproduce.sh（一条命令复现 6 项验收）"
