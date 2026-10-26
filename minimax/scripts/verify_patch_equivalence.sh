#!/usr/bin/env bash
# 核对 MiniMax 产出的补丁与提交的代码差异是否一致。
#
# 用法：bash scripts/verify_patch_equivalence.sh [修改前提交] [修改后提交] [补丁文件]
#
# 做法：把补丁应用到"修改前"提交的 sample-project 快照上，再与"修改后"提交的
# 快照逐字节比较。两者完全一致，说明提交的代码差异正是模型产出的结果。
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REPO_ROOT="$(git -C "$HERE" rev-parse --show-toplevel)"
BEFORE="${1:-54c32599}"
AFTER="${2:-ce4d6fa4}"
PATCH="${3:-$HERE/artifacts/diff/minimax-20261026T071003.patch}"

if [ ! -f "$PATCH" ]; then
  echo "找不到补丁文件: $PATCH"
  exit 2
fi

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
mkdir -p "$WORK/before" "$WORK/after"

git -C "$REPO_ROOT" archive "$BEFORE" minimax/sample-project | tar -x -C "$WORK/before" --strip-components=2
git -C "$REPO_ROOT" archive "$AFTER" minimax/sample-project | tar -x -C "$WORK/after" --strip-components=2

echo "--- 把模型补丁应用到修改前快照（$BEFORE）---"
( cd "$WORK/before" && python3 -c "
import sys
sys.path.insert(0, '$HERE/src')
from pathlib import Path
from sddctx.patching import apply_unified_diff
print(apply_unified_diff(Path('.'), Path('$PATCH').read_text(encoding='utf-8')))
" ) || { echo "补丁应用失败"; exit 1; }

echo "--- 与修改后快照（$AFTER）逐字节比较 ---"
if diff -r "$WORK/before" "$WORK/after"; then
  echo "IDENTICAL: 模型补丁产出的代码与修改后提交完全一致"
else
  echo "DIFFERS"
  exit 1
fi
