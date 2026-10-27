#!/usr/bin/env bash
# 演示扩展能力：规格更新后重新导入，上下文必须使用更新后的内容。
#
# 脚本会临时修改 SPEC-004 的允许等级集合，重新导入并检索，
# 把前后对比保存到 artifacts/spec-change/，随后恢复原规格并重新导入。
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$HERE"

# 允许直接用 bash scripts/xxx.sh 运行：把虚拟环境放到 PATH 前面，
# 否则会报 "sddctx: command not found"。
# shellcheck source=scripts/lib.sh
source "$HERE/scripts/lib.sh"
ensure_sddctx_on_path

SPEC="sample-project/specs/filtering.md"
OUT="artifacts/spec-change"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

mkdir -p "$OUT"
cp "$SPEC" "$WORK/filtering.v1.md"

step() { echo; echo "=== $1 ==="; }

step "1. 变更前：导入并检索，记录 SPEC-004 的正文与哈希"
sddctx ingest --root sample-project > "$OUT/ingest-before.json" 2>&1
sddctx context --target-spec SPEC-004 --quiet > /dev/null 2>&1
cp artifacts/context/context.json "$OUT/context-before.json"

step "2. 更新规格：SPEC-004 的允许等级集合加入 PLATINUM"
python3 - "$SPEC" <<'PY'
import sys
from pathlib import Path

path = Path(sys.argv[1])
text = path.read_text(encoding="utf-8")
old = "- 允许的等级集合为：`VIP`、`GOLD`、`SILVER`。"
new = "- 允许的等级集合为：`VIP`、`GOLD`、`SILVER`、`PLATINUM`。"
if old not in text:
    raise SystemExit("规格文本与预期不符，无法演示更新")
path.write_text(text.replace(old, new), encoding="utf-8")
print("规格已更新")
PY

step "3. 重新导入：changed_files 必须列出发生变化的规格条目"
sddctx ingest --root sample-project > "$OUT/ingest-after.json" 2>&1
cat "$OUT/ingest-after.json"

step "4. 重新检索：上下文必须使用更新后的规格内容"
sddctx context --target-spec SPEC-004 --quiet > /dev/null 2>&1
cp artifacts/context/context.json "$OUT/context-after.json"

step "5. 对比 SPEC-004 在前后两次检索中的正文与哈希"
python3 - <<'PY'
import json
from pathlib import Path


def spec(path):
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    for item in data["specifications"]:
        if item["spec_id"] == "SPEC-004":
            return item
    return None


before = spec("artifacts/spec-change/context-before.json")
after = spec("artifacts/spec-change/context-after.json")
for label, item in (("变更前", before), ("变更后", after)):
    body = item["body"].splitlines()[1] if item and len(item["body"].splitlines()) > 1 else ""
    print(f"{label}: source_hash={item['source_hash'][:16]}…  {body.strip()}")
print("哈希是否变化:", (before or {}).get("source_hash") != (after or {}).get("source_hash"))
PY

step "6. 恢复原规格并重新导入"
cp "$WORK/filtering.v1.md" "$SPEC"
sddctx ingest --root sample-project > "$OUT/ingest-restored.json" 2>&1
sddctx context --target-spec SPEC-004 --quiet > /dev/null 2>&1

echo
echo "证据目录：$OUT"
