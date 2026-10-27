#!/usr/bin/env bash
# 一键起全栈：seekdb + 侧车 + PowerContext + Dify
#
# 用法：
#   bash dify/scripts/start_all.sh
#
# 说明：
#   * PowerContext 需要用仓库源码启动（不在本目录），请先按 docs/01-运行说明.md §4.2 配好 .env 与虚拟环境；
#     本脚本会用 POWERCONTEXT_PYTHON 指定的解释器拉起它（默认用当前 venv 的 python）。
#   * 本脚本只做"启动 + 健康检查"，不做数据初始化（初始化见 scripts/seed.py）。
set -uo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
DIFY="$ROOT/dify"
SIDECAR_PORT="${SIDECAR_PORT:-8090}"
POWERCONTEXT_PORT="${POWERCONTEXT_PORT:-8848}"
POWERCONTEXT_PYTHON="${POWERCONTEXT_PYTHON:-python3}"

echo "==> 1/5 启动 seekdb"
cd "$DIFY/deploy" && docker compose -f seekdb.yml up -d seekdb

echo "==> 2/5 准备 PowerContext 存储（建库 + 清表）"
# 只在 PowerContext **没在跑**的时候清表：它自己启动时会建表；
# 运行中删表会让它写进不存在的表（这正是我们踩过的坑）。
if curl -s --max-time 3 "http://127.0.0.1:$POWERCONTEXT_PORT/api/v1/system/health" >/dev/null 2>&1; then
  echo "    PowerContext 已在运行，跳过清表"
else
  cd "$DIFY" && python3 scripts/init_powercontext.py >/dev/null && echo "    ok"
fi

echo "==> 3/5 启动侧车 sidecar (:$SIDECAR_PORT)"
cd "$DIFY/backend"
if ! curl -s --max-time 3 "http://127.0.0.1:$SIDECAR_PORT/health" >/dev/null 2>&1; then
  (cd "$DIFY/backend" && nohup python3 -m app.main >/tmp/aye-sidecar.log 2>&1 &)
  sleep 3
fi
echo "    sidecar: $(curl -s --max-time 5 "http://127.0.0.1:$SIDECAR_PORT/health" | head -c 120)"

echo "==> 4/5 启动 PowerContext (:$POWERCONTEXT_PORT)"
if ! curl -s --max-time 3 "http://127.0.0.1:$POWERCONTEXT_PORT/api/v1/system/health" >/dev/null 2>&1; then
  PC_SRC="${POWERCONTEXT_SRC:-$ROOT/competition2026/powercontext-vldb}"
  if [ -d "$PC_SRC/src" ]; then
    (cd "$PC_SRC" && PYTHONPATH="$PC_SRC/src" nohup "$POWERCONTEXT_PYTHON" -m server.cli.server \
       --host 0.0.0.0 --port "$POWERCONTEXT_PORT" --no-open-browser >/tmp/aye-powercontext.log 2>&1 &)
    sleep 25
  else
    echo "    [warn] 没找到 PowerContext 源码（$PC_SRC），请手动启动后重试"
  fi
fi
echo "    powercontext: $(curl -s --max-time 8 "http://127.0.0.1:$POWERCONTEXT_PORT/api/v1/system/health" | head -c 140)"

echo "==> 5/5 启动 Dify"
cd "$DIFY/deploy/dify"
[ -f .env ] || cp .env.template .env
docker compose up -d
sleep 10
docker compose ps --format '{{.Service}}\t{{.State}}' 2>/dev/null | head -12

echo
echo "==> 自检"
bash "$DIFY/scripts/smoke_test.sh"
