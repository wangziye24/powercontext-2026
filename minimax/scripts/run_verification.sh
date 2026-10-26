#!/usr/bin/env bash
# 端到端验证：初始化数据 → 修改前基线 → MiniMax 修改代码 → 修改后验证 → 状态复用。
#
# 前提：已执行 scripts/setup_wsl.sh，并导出 MINIMAX_API_KEY。
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$HERE"

TASK_ARGS=(--target-spec SPEC-004)
STEP=0

step() {
  STEP=$((STEP + 1))
  echo
  echo "=============== [$STEP] $1 ==============="
}

step "启动 PowerContext Server（任务状态后端：内嵌 seekDB）"
bash scripts/start_server.sh

step "初始化数据：导入规格、代码与测试"
bash scripts/init_data.sh

step "修改前基线：目标测试必须失败（功能未实现），回归测试必须通过"
sddctx verify --phase before "${TASK_ARGS[@]}"
BEFORE_CODE=$?
if [ "$BEFORE_CODE" -eq 0 ]; then
  echo "!! 异常：修改前目标测试竟然通过了，验证样例失效"
  exit 1
fi
echo "[ok] 修改前目标测试按预期失败"

step "用 MiniMax 完成代码修改"
sddctx apply "${TASK_ARGS[@]}"
APPLY_CODE=$?
if [ "$APPLY_CODE" -ne 0 ]; then
  echo "!! MiniMax 修改步骤失败（exit=$APPLY_CODE）"
  exit "$APPLY_CODE"
fi

step "修改后验证：目标测试与回归测试都必须通过"
sddctx verify --phase after "${TASK_ARGS[@]}"
AFTER_CODE=$?
if [ "$AFTER_CODE" -ne 0 ]; then
  echo "!! 修改后仍有测试失败（exit=$AFTER_CODE）"
  exit "$AFTER_CODE"
fi
echo "[ok] 修改后全部测试通过"

step "状态复用：重启 Server 后读回任务状态"
bash scripts/stop_server.sh
bash scripts/start_server.sh
sddctx state show

echo
echo "=============== 验证完成 ==============="
echo "证据目录：$HERE/artifacts"
