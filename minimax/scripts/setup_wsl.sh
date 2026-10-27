#!/usr/bin/env bash
# 准备 MiniMax × OceanBase 赛题运行环境（Linux / WSL Ubuntu）。
# 幂等：重复执行不会破坏已有虚拟环境。
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV="${SDDCTX_VENV:-$HOME/minimax-venv}"
export PATH="$HOME/.local/bin:$PATH"

if ! command -v uv >/dev/null 2>&1; then
  echo "[setup] 安装 uv"
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="$HOME/.local/bin:$PATH"
fi

echo "[setup] 创建虚拟环境 $VENV (Python 3.12)"
uv python install 3.12
uv venv --python 3.12 "$VENV" --allow-existing
# shellcheck disable=SC1091
source "$VENV/bin/activate"

echo "[setup] 安装 sddctx 及其依赖（powercontext[server,seekdb]==1.4.0）"
uv pip install -e "$HERE"

echo "[setup] python: $(python -V)"
python - <<'PY'
import pylibseekdb, powercontext, sqlalchemy
print("[setup] pylibseekdb   ->", pylibseekdb.__file__)
print("[setup] powercontext  ->", powercontext.__file__)
print("[setup] sqlalchemy    ->", sqlalchemy.__version__)
PY
echo "[setup] 完成。后续：scripts/init_data.sh -> scripts/run_verification.sh"
