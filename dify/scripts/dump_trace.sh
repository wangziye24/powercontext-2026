#!/usr/bin/env bash
# 导出 Dify 的节点执行记录（workflow run trace），作为"编排由 Dify 承担"的证据。
#
# 用法：
#   bash dify/scripts/dump_trace.sh > dify/docs/evidence/node-trace.txt
#
# 说明：
#   * 控制台 UI：应用 → 日志 → 每次对话的"节点详情"；
#   * 本脚本直接查 Dify 自己的 Postgres（workflow_runs / workflow_node_executions），
#     方便离线归档与核对。
set -uo pipefail

COMPOSE_DIR="$(cd "$(dirname "$0")/../deploy/dify" && pwd)"
APP_ID="${1:-}"

echo "# Dify 节点执行记录（导出于 $(date '+%Y-%m-%d %H:%M:%S')）"
echo

if [ -n "$APP_ID" ]; then
  FILTER="WHERE r.app_id = '$APP_ID'"
else
  FILTER=""
fi

cd "$COMPOSE_DIR"
docker compose exec -T db_postgres psql -U postgres -d dify -c "
SELECT r.id AS run_id, r.status, r.created_at
FROM workflow_runs r $FILTER
ORDER BY r.created_at DESC LIMIT 5;"

echo
echo "==== 最近一次运行的逐节点执行明细 ===="
echo
RUN_ID=$(docker compose exec -T db_postgres psql -U postgres -d dify -t -A -c "
SELECT id FROM workflow_runs r $FILTER ORDER BY created_at DESC LIMIT 1;" | tr -d '[:space:]')

if [ -z "$RUN_ID" ]; then
  echo "没有找到 workflow run"
  exit 0
fi

docker compose exec -T db_postgres psql -U postgres -d dify -c "
SELECT n.node_type, n.title, n.status, n.elapsed_time,
       LEFT(COALESCE(n.outputs::text, ''), 300) AS outputs
FROM workflow_node_executions n
WHERE n.workflow_run_id = '$RUN_ID'
ORDER BY n.created_at;"
