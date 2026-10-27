#!/usr/bin/env bash
# 启动 PowerContext Server（任务状态的持久化后端是内嵌 seekdb）。
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export SDDCTX_HOME="${SDDCTX_HOME:-$HOME/.sddctx}"
PORT="${POWERCONTEXT_PORT:-8000}"
ENV_FILE="$SDDCTX_HOME/powercontext.env"
LOG_FILE="$SDDCTX_HOME/powercontext-server.log"
PID_FILE="$SDDCTX_HOME/powercontext-server.pid"

mkdir -p "$SDDCTX_HOME/powercontext"
sed -e "s|__SDDCTX_HOME__|$SDDCTX_HOME|g" -e "s|__PORT__|$PORT|g" \
  "$HERE/config/powercontext.env.template" > "$ENV_FILE"

if [ -f "$PID_FILE" ] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
  echo "[server] 已在运行 (pid $(cat "$PID_FILE"))"
  exit 0
fi

nohup powercontext server run --env-file "$ENV_FILE" > "$LOG_FILE" 2>&1 &
echo $! > "$PID_FILE"
echo "[server] 启动中 pid=$(cat "$PID_FILE") 日志=$LOG_FILE"

for _ in $(seq 1 90); do
  if curl -sf "http://127.0.0.1:$PORT/health/ready" > /dev/null 2>&1; then
    echo "[server] 就绪 http://127.0.0.1:$PORT"
    exit 0
  fi
  if ! kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
    echo "[server] 进程退出，日志尾部："
    tail -20 "$LOG_FILE"
    exit 1
  fi
  sleep 1
done

echo "[server] 等待就绪超时，日志尾部："
tail -20 "$LOG_FILE"
exit 1
