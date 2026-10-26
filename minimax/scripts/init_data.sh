#!/usr/bin/env bash
# 初始化数据：把规格、代码与测试导入内嵌 seekdb。
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$HERE"

# 允许直接用 bash scripts/xxx.sh 运行：把虚拟环境放到 PATH 前面，
# 否则会报 "sddctx: command not found"。
# shellcheck source=scripts/lib.sh
source "$HERE/scripts/lib.sh"
ensure_sddctx_on_path

echo "[init] 导入工程资料到 seekdb"
sddctx ingest --root sample-project

echo "[init] 当前检索到的上下文概览"
sddctx context --target-spec SPEC-004 --quiet > /dev/null
python3 - <<'PY'
import json, pathlib
context = json.loads(pathlib.Path("artifacts/context/context.json").read_text(encoding="utf-8"))
print("  规格条目:", [item["spec_id"] for item in context["specifications"]])
print("  代码符号:", [f'{item["path"]}::{item["symbol"]}' for item in context["code"]])
print("  目标测试:", [item["name"] for item in context["tests"] if item["role"] == "target"])
print("  回归测试:", len([item for item in context["tests"] if item["role"] == "regression"]), "个")
PY
echo "[init] 完成"
