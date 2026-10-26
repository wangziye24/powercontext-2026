#!/usr/bin/env bash
# 在干净环境中复核"修改前失败 / 修改后通过"。
#
# 用法：bash scripts/verify_before_after.sh <修改前提交> <修改后提交>
#
# 脚本从版本库中取出两个提交里的 sample-project 快照，分别运行
# 目标测试与全量测试，并打印每条命令的退出码。
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REPO_ROOT="$(cd "$HERE/.." && pwd)"
BEFORE="${1:-}"
AFTER="${2:-}"

if [ -z "$BEFORE" ] || [ -z "$AFTER" ]; then
  echo "用法: bash scripts/verify_before_after.sh <修改前提交> <修改后提交>"
  exit 2
fi

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
# shellcheck source=scripts/lib.sh
source "$HERE/scripts/lib.sh"
ensure_sddctx_on_path
PYTHON="${PYTHON:-$HOME/minimax-venv/bin/python}"
[ -x "$PYTHON" ] || PYTHON="python3"

run_snapshot() {
  local commit="$1" label="$2"
  local dir="$WORK/$label"
  mkdir -p "$dir"
  git -C "$REPO_ROOT" archive "$commit" "$(detect_project_dir "$REPO_ROOT" "$commit")/sample-project" | tar -x -C "$dir" --strip-components=2
  echo "=== $label ($commit) ==="

  echo "--- 目标测试 tests/test_filter_tier.py ---"
  ( cd "$dir" && "$PYTHON" -m pytest -q tests/test_filter_tier.py )
  echo "exit_code=$?"

  echo "--- 全量测试 tests/test_filter_regression.py tests/test_filter_tier.py ---"
  ( cd "$dir" && "$PYTHON" -m pytest -q tests/test_filter_regression.py tests/test_filter_tier.py )
  echo "exit_code=$?"
  echo
}

run_snapshot "$BEFORE" "before"
run_snapshot "$AFTER" "after"

echo "预期：before 的目标测试失败（功能未实现），after 的目标测试与全量测试全部通过。"
