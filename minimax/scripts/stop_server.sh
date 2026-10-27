#!/usr/bin/env bash
# 停止 PowerContext Server。
set -euo pipefail

export SDDCTX_HOME="${SDDCTX_HOME:-$HOME/.sddctx}"
PID_FILE="$SDDCTX_HOME/powercontext-server.pid"

if [ ! -f "$PID_FILE" ]; then
  echo "[server] 没有运行中的实例"
  exit 0
fi

PID="$(cat "$PID_FILE")"
if kill -0 "$PID" 2>/dev/null; then
  kill "$PID"
  for _ in $(seq 1 20); do
    kill -0 "$PID" 2>/dev/null || break
    sleep 0.5
  done
  echo "[server] 已停止 pid=$PID"
else
  echo "[server] 进程不存在 pid=$PID"
fi
rm -f "$PID_FILE"
