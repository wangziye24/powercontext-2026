#!/usr/bin/env bash
# 一键自检：验证 seekdb / 侧车 / PowerContext 三条链路是否都通。
# 用法：bash dify/scripts/smoke_test.sh
set -uo pipefail

SIDECAR="${SIDECAR:-http://127.0.0.1:8090}"
PC="${POWERCONTEXT:-http://127.0.0.1:8848}"
PLAYER="${PLAYER:-smoke_player}"

pass=0
fail=0

ok()   { echo "  ✅ $1"; pass=$((pass+1)); }
bad()  { echo "  ❌ $1"; fail=$((fail+1)); }

echo "== 1. 侧车 / seekdb =="
HEALTH=$(curl -s --max-time 10 "$SIDECAR/health")
if echo "$HEALTH" | tr -d ' ' | grep -q '"seekdb":"ok"'; then
  ok "侧车连通，seekdb 正常：$(echo "$HEALTH" | python3 -c 'import sys,json;d=json.load(sys.stdin);print("lore_rows=%s"%d.get("lore_rows"))' 2>/dev/null)"
else
  bad "侧车或 seekdb 异常：$HEALTH"
fi

echo "== 2. 混合检索（向量 + 全文 + 结构化过滤） =="
HITS=$(curl -s --max-time 30 -X POST "$SIDECAR/lore/search" -H 'Content-Type: application/json' \
  -d '{"query":"关东煮还有吗","kinds":"item","top_k":3}')
if echo "$HITS" | grep -q '关东煮'; then
  ok "检索命中「关东煮拼盘」（mode=$(echo "$HITS" | python3 -c 'import sys,json;print(json.load(sys.stdin).get("mode"))' 2>/dev/null)）"
else
  bad "检索未命中：$HITS"
fi

echo "== 3. 玩家状态写入 / 读取（seekdb） =="
curl -s --max-time 20 -X POST "$SIDECAR/state" -H 'Content-Type: application/json' \
  -d "{\"player_id\":\"$PLAYER\",\"player_name\":\"冒烟测试\",\"affection\":7,\"balance\":1.5,\"pranks_done\":1,\"chapter\":\"第二章·常客\",\"event\":{\"intent\":\"smoke\",\"action\":\"自检\"}}" > /dev/null
STATE=$(curl -s --max-time 20 "$SIDECAR/state?player_id=$PLAYER")
if echo "$STATE" | grep -q '"affection": 7\|"affection":7'; then
  ok "状态写入并读回一致：$STATE"
else
  bad "状态读写异常：$STATE"
fi

echo "== 4. 事件流水落库 =="
LOG=$(curl -s --max-time 20 "$SIDECAR/event_log?player_id=$PLAYER&limit=3")
if echo "$LOG" | grep -q 'smoke'; then
  ok "event_log 有记录：$(echo "$LOG" | head -c 200)"
else
  bad "event_log 无记录：$LOG"
fi

echo "== 5. PowerContext 记忆写入 / 召回 =="
PC_HEALTH=$(curl -s --max-time 10 "$PC/api/v1/system/health")
if echo "$PC_HEALTH" | grep -q '"status":"healthy"'; then
  ok "PowerContext 健康"
else
  bad "PowerContext 异常：$PC_HEALTH"
fi
CONTENT="[偏好] 冒烟测试玩家 $(date +%s) 喜欢美式少冰"
curl -s --max-time 60 -X POST "$PC/api/v1/memories" -H 'Content-Type: application/json' \
  -d "{\"content\":\"$CONTENT\",\"user_id\":\"$PLAYER\",\"agent_id\":\"aye-nightstore\",\"scope\":\"user\",\"memory_type\":\"working\",\"infer\":false}" > /dev/null
FOUND=$(curl -s --max-time 60 -X POST "$PC/api/v1/memories/search" -H 'Content-Type: application/json' \
  -d "{\"query\":\"冒烟测试玩家喜欢喝什么\",\"user_id\":\"$PLAYER\",\"agent_id\":\"aye-nightstore\",\"limit\":3}")
if echo "$FOUND" | grep -q '美式少冰'; then
  ok "记忆写入并被语义召回"
else
  bad "记忆召回失败：$(echo "$FOUND" | head -c 200)"
fi

echo
echo "== 结果：成功 $pass 项，失败 $fail 项 =="
[ "$fail" -eq 0 ]
