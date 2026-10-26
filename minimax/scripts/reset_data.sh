#!/usr/bin/env bash
# 重置验证样例的数据与状态。
#  1. 删除本地 seekDB 索引（工程资料与检索索引）
#  2. 清空 PowerContext 保存的任务状态
#  3. 把 sample-project 恢复到版本库中的基线提交
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REPO_ROOT="$(cd "$HERE/.." && pwd)"
export SDDCTX_HOME="${SDDCTX_HOME:-$HOME/.sddctx}"

# shellcheck source=scripts/lib.sh
source "$HERE/scripts/lib.sh"
ensure_sddctx_on_path

cd "$HERE"
echo "[reset] 删除本地 seekDB 索引"
sddctx reset

echo "[reset] 清空 PowerContext 任务状态"
if curl -sf "http://127.0.0.1:${POWERCONTEXT_PORT:-8000}/health/ready" > /dev/null 2>&1; then
  for scope in $(sddctx state show 2>/dev/null | python3 -c '
import json, sys
try:
    print(json.load(sys.stdin).get("scope_id", ""))
except Exception:
    print("")
'); do
    [ -n "$scope" ] && curl -sf -X POST "http://127.0.0.1:${POWERCONTEXT_PORT:-8000}/v1/memory/flush" \
      -H "content-type: application/json" -d "{\"scope_id\": \"$scope\"}" > /dev/null || true
  done
  echo "[reset] 状态已清空"
else
  echo "[reset] PowerContext Server 未运行，跳过状态清空"
fi

echo "[reset] 恢复 sample-project 到基线提交"
PROJECT_DIR="$(detect_project_dir "$REPO_ROOT" HEAD)"
git -C "$REPO_ROOT" checkout -- "$PROJECT_DIR/sample-project"
echo "[reset] 完成。下一步：bash scripts/init_data.sh"
