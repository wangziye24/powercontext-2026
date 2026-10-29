#!/usr/bin/env bash
# 在干净环境中复核「修改前失败 / 修改后通过」。
#
#   bash scripts/verify_before_after.sh
#
# 默认直接使用随仓库交付的两个快照目录，**不需要 git 历史**：
#   snapshots/before  —— 修改前（目标功能未实现）
#   sample-project    —— 修改后（即提交的最终代码）
#
# 也可以显式指定两个工程目录：bash scripts/verify_before_after.sh <改前目录> <改后目录>
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$HERE"
# shellcheck source=scripts/lib.sh
source "$HERE/scripts/lib.sh"
ensure_sddctx_on_path

BEFORE="${1:-snapshots/before}"
AFTER="${2:-sample-project}"
PYTHON="${PYTHON:-python3}"

for dir in "$BEFORE" "$AFTER"; do
  if [ ! -d "$dir" ]; then
    echo "找不到工程目录: $dir"
    exit 2
  fi
done

run_snapshot() {
  local dir="$1" label="$2"
  echo "=== $label ($dir) ==="

  echo "--- 目标测试 tests/test_filter_tier.py ---"
  ( cd "$dir" && "$PYTHON" -m pytest -q -p no:cacheprovider tests/test_filter_tier.py )
  echo "exit_code=$?"

  echo "--- 全量测试 tests/test_filter_regression.py tests/test_filter_tier.py ---"
  ( cd "$dir" && "$PYTHON" -m pytest -q -p no:cacheprovider tests/test_filter_regression.py tests/test_filter_tier.py )
  echo "exit_code=$?"
  echo
}

run_snapshot "$BEFORE" "before"
run_snapshot "$AFTER" "after"

echo "预期：before 的目标测试失败（功能未实现，TypeError: unexpected keyword argument），"
echo "      after 的目标测试与全量测试全部通过（21 passed）。"
