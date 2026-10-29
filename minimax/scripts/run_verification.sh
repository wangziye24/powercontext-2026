#!/usr/bin/env bash
# 端到端验证入口（推荐）：逐条复现 6 项必做验收并打印 PASS/FAIL 汇总。
#
#   bash scripts/run_verification.sh
#
# 等价于 `bash scripts/reproduce.sh`；保留这个名字是为了兼容 RUNBOOK 里的历史命令。
#
# 行为要点：
#   * 从 snapshots/before/（修改前快照）在临时目录里跑完整流程，不会弄脏仓库；
#   * 有 MINIMAX_API_KEY 时真实调用 MiniMax，没有时自动回放已记录的模型产出；
#   * 数据目录默认落在 $HOME/.sddctx-reproduce（内嵌 seekdb 不能放 /mnt/* 上）。
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
exec bash "$HERE/scripts/reproduce.sh" "$@"
