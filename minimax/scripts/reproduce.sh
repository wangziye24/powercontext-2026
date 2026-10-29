#!/usr/bin/env bash
# 一键复现「规有所依」作品的 6 项必做验收（评审用）。
#
#   * 不需要 MINIMAX_API_KEY：改码步骤用 `sddctx apply --replay` 回放开发过程中
#     记录下来的 MiniMax 产出（artifacts/minimax/ 里的原始请求与响应）。
#   * 不依赖 git 历史：修改前快照随仓库交付在 snapshots/before/，
#     修改后快照就是 sample-project/ 本身。脚本在临时目录里从「修改前」跑到
#     「修改后」，因此不会弄脏仓库。
#   * 数据目录放在 $HOME 下：内嵌 seekdb 需要 fallocate，WSL 的 /mnt/c、/mnt/d
#     这类 Windows 挂载盘不支持，必须避开。
#
# 用法：bash scripts/reproduce.sh
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$HERE"
# shellcheck source=scripts/lib.sh
source "$HERE/scripts/lib.sh"
ensure_sddctx_on_path

export SDDCTX_HOME="${SDDCTX_HOME:-$HOME/.sddctx-reproduce}"
case "$SDDCTX_HOME" in
  /mnt/*)
    echo "[fatal] SDDCTX_HOME=$SDDCTX_HOME 在 Windows 挂载盘上（/mnt/*）。"
    echo "        seekdb 需要 fallocate，这类文件系统不支持，会把 open 打挂（errcode=-4009）。"
    echo "        请改用 \$HOME 下的目录，例如：export SDDCTX_HOME=\"\$HOME/.sddctx-reproduce\""
    exit 2
    ;;
esac

WORK="$(mktemp -d "${TMPDIR:-/tmp}/sddctx-reproduce.XXXXXX")"
PASS=0
FAIL=0
RESULT=()

cleanup() {
  bash scripts/stop_server.sh > /dev/null 2>&1 || true
  rm -rf "$WORK"
}
trap cleanup EXIT

check() {
  local name="$1" ok="$2" detail="${3:-}"
  if [ "$ok" = "1" ]; then
    echo "  [PASS] $name"
    PASS=$((PASS + 1))
    RESULT+=("PASS  $name")
  else
    echo "  [FAIL] $name ${detail}"
    FAIL=$((FAIL + 1))
    RESULT+=("FAIL  $name ${detail}")
  fi
}

echo "== 0. 准备干净数据目录 =="
echo "   SDDCTX_HOME=$SDDCTX_HOME"
rm -rf "$SDDCTX_HOME"
mkdir -p "$SDDCTX_HOME" || exit 1

echo
echo "== 1. 启动 PowerContext Server（任务状态后端：内嵌 seekdb）=="
if bash scripts/start_server.sh; then
  check "能够启动状态服务（PowerContext Server 健康检查 200）" 1
else
  check "能够启动状态服务（PowerContext Server 健康检查 200）" 0 "start_server.sh 失败"
fi

echo
echo "== 2. 准备「修改前」工作工程（snapshots/before → 临时目录）=="
cp -r snapshots/before "$WORK/project"
check "修改前快照存在且可复制" "$([ -f "$WORK/project/src/orders/filter.py" ] && echo 1 || echo 0)"

echo
echo "== 3. 导入工程资料到 seekdb =="
if sddctx ingest --root "$WORK/project" > "$WORK/ingest.json" 2>&1; then
  check "资料导入成功（规格/代码/测试写入 seekdb）" 1
else
  check "资料导入成功（规格/代码/测试写入 seekdb）" 0 "见 $WORK/ingest.json"
fi
grep -q '"specs": 7' "$WORK/ingest.json" && check "导入条数：7 条规格" 1 || check "导入条数：7 条规格" 0

echo
echo "== 4. 输出开发上下文（规格 / 代码符号 / 目标测试 / 回归测试）=="
if sddctx context --root "$WORK/project" --target-spec SPEC-004 --out "$WORK/context.json" --quiet > /dev/null 2>&1; then
  check "上下文输出成功" 1
  ctx="$WORK/context.json"
  grep -q 'SPEC-004' "$ctx" && grep -q 'SPEC-005' "$ctx" && check "上下文包含 SPEC-004 及必须保持的既有规格" 1 || check "上下文包含 SPEC-004 及必须保持的既有规格" 0
  grep -q 'filter_orders' "$ctx" && check "上下文定位到待改代码符号 filter_orders" 1 || check "上下文定位到待改代码符号 filter_orders" 0
  grep -q 'test_filter_tier.py' "$ctx" && grep -q 'test_filter_regression.py' "$ctx" && check "上下文包含目标测试与回归测试" 1 || check "上下文包含目标测试与回归测试" 0
else
  check "上下文输出成功" 0
fi

echo
echo "== 5. 修改前基线：目标测试必须因功能未实现而失败 =="
sddctx verify --root "$WORK/project" --phase before --target-spec SPEC-004 > "$WORK/before.json" 2>&1
BEFORE_CODE=$?
if [ "$BEFORE_CODE" -eq 0 ]; then
  check "修改前目标测试失败（功能未实现）" 0 "目标测试竟然通过，验收样例失效"
elif [ "$BEFORE_CODE" -ge 126 ]; then
  check "修改前目标测试失败（功能未实现）" 0 "sddctx 无法执行（exit=$BEFORE_CODE）"
else
  grep -q 'TypeError' "$WORK/before.json" && check "修改前失败原因是功能未实现（TypeError: unexpected keyword argument）" 1 || check "修改前失败原因是功能未实现" 0 "见 $WORK/before.json"
fi

echo
echo "== 6. 代码修改：MiniMax 真实调用（有密钥）；无密钥时回放已记录的产出 =="
if [ -n "${MINIMAX_API_KEY:-}${SDDCTX_LLM_API_KEY:-}" ]; then
  APPLY_ARGS=()
  echo "   检测到 API 密钥 → 真实调用 MiniMax"
else
  APPLY_ARGS=(--replay)
  echo "   未检测到 MINIMAX_API_KEY → 回放开发过程中记录的 MiniMax 产出（artifacts/minimax/）"
fi
if sddctx apply --root "$WORK/project" "${APPLY_ARGS[@]}" > "$WORK/apply.json" 2>&1; then
  check "MiniMax 补丁应用成功（调用或回放记录写入 artifacts/minimax/）" 1
else
  check "MiniMax 补丁应用成功（调用或回放记录写入 artifacts/minimax/）" 0 "见 $WORK/apply.json"
fi

echo
echo "== 7. 修改后验证：目标测试与回归测试必须全部通过 =="
if sddctx verify --root "$WORK/project" --phase after --target-spec SPEC-004 > "$WORK/after.json" 2>&1; then
  check "修改后全部测试通过（21 passed）" 1
else
  check "修改后全部测试通过（21 passed）" 0 "见 $WORK/after.json"
fi
grep -q '21 passed' "$WORK/after.json" && check "修改后用例数一致：21 passed" 1 || check "修改后用例数一致：21 passed" 0

echo
echo "== 8. 修改后的工程必须与仓库里的 sample-project 逐字节一致 =="
DIFF_EXCLUDES=(-x '__pycache__' -x '*.pyc' -x '.pytest_cache')
if diff -r "${DIFF_EXCLUDES[@]}" "$WORK/project" sample-project > /dev/null 2>&1; then
  check "模型补丁产出的代码与提交的 sample-project 一致" 1
else
  check "模型补丁产出的代码与提交的 sample-project 一致" 0 "$(diff -r "${DIFF_EXCLUDES[@]}" "$WORK/project" sample-project | head -5)"
fi

echo
echo "== 9. 状态复用：重启模块后仍能读回任务状态 =="
bash scripts/stop_server.sh > /dev/null 2>&1
if bash scripts/start_server.sh > /dev/null 2>&1; then
  if sddctx state show > "$WORK/state.json" 2>&1 && grep -q '"task_key"' "$WORK/state.json"; then
    check "重启后读回任务状态（阶段/已完成/待办/验证结果）" 1
  else
    check "重启后读回任务状态（阶段/已完成/待办/验证结果）" 0 "见 $WORK/state.json"
  fi
else
  check "重启后读回任务状态（阶段/已完成/待办/验证结果）" 0 "重启 Server 失败"
fi

echo
echo "================ 汇总 ================"
for line in "${RESULT[@]}"; do echo "  $line"; done
echo "  ------------------------------------"
echo "  PASS=$PASS  FAIL=$FAIL"
echo

if [ "$FAIL" -gt 0 ]; then
  echo "结论：未全部通过。请按上面 [FAIL] 项排查。"
  exit 1
fi

echo "结论：6 项必做验收全部可复现通过。"
echo "证据：artifacts/（SQL 记录、MiniMax 请求与响应、测试日志、代码差异、状态快照）"
