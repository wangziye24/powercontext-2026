#!/usr/bin/env bash
# 核对 MiniMax 产出的补丁与提交的代码差异是否一致。
#
#   bash scripts/verify_patch_equivalence.sh [改前目录] [改后目录] [补丁文件]
#
# 默认使用随仓库交付的快照，**不需要 git 历史**：
#   snapshots/before  —— 修改前
#   sample-project    —— 修改后
#   补丁 = artifacts/diff/minimax-*.patch（MiniMax 原始回答里的 unified diff）
#
# 做法：把补丁应用到「修改前」快照的副本上，再与「修改后」快照逐字节比较。
# 两者完全一致，说明提交的代码差异正是模型产出的结果。
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$HERE"
# shellcheck source=scripts/lib.sh
source "$HERE/scripts/lib.sh"

BEFORE="${1:-snapshots/before}"
AFTER="${2:-sample-project}"
PATCH="${3:-$(ls -1 artifacts/diff/minimax-*.patch 2>/dev/null | tail -1)}"
# 补丁路径必须是绝对路径：脚本后续会 cd 到临时目录再读补丁。
if [ -n "$PATCH" ] && [ -f "$PATCH" ]; then
  PATCH="$(cd "$(dirname "$PATCH")" && pwd)/$(basename "$PATCH")"
else
  echo "找不到补丁文件: ${PATCH:-<artifacts/diff/minimax-*.patch>}"
  exit 2
fi

for dir in "$BEFORE" "$AFTER"; do
  if [ ! -d "$dir" ]; then
    echo "找不到工程目录: $dir"
    exit 2
  fi
done

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
cp -r "$BEFORE" "$WORK/before"

echo "--- 把模型补丁应用到修改前快照（$BEFORE）---"
PYTHON="${PYTHON:-python3}"
( cd "$WORK/before" && "$PYTHON" - "$HERE/src" "$PATCH" <<'PY'
import sys
from pathlib import Path

sys.path.insert(0, sys.argv[1])
from sddctx.patching import apply_unified_diff

patch = Path(sys.argv[2]).read_text(encoding="utf-8")
print(apply_unified_diff(Path("."), patch))
PY
) || { echo "补丁应用失败"; exit 1; }

echo "--- 与修改后快照（$AFTER）逐字节比较 ---"
if diff -r -x '__pycache__' -x '*.pyc' -x '.pytest_cache' "$WORK/before" "$AFTER"; then
  echo "IDENTICAL: 模型补丁产出的代码与提交的 sample-project 完全一致"
else
  echo "DIFFERS"
  exit 1
fi
