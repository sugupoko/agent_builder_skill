#!/bin/bash
# <agent_name> — ヘルスチェック (5 分毎 CronJob で実行)
# 検出された異常は Slack に通知

set -e
ALERTS=()

alert() {
  echo "[ALERT] $1" >&2
  ALERTS+=("$1")
}

# 1. webhook-receiver liveness
if ! curl -fs --max-time 5 http://<agent_name>-webhook-receiver/health >/dev/null 2>&1; then
  alert "webhook-receiver liveness failed"
fi

# 2. worker count
WORKERS="${WORKER_COUNT:-3}"
if [ "$WORKERS" -lt 2 ]; then
  alert "agent worker count: $WORKERS (expected 2+)"
fi

# 3. Redis queue depth
DEPTH=$(redis-cli -h "${REDIS_HOST:-redis}" LLEN <agent_name>:queue 2>/dev/null || echo 0)
if [ "$DEPTH" -gt 200 ]; then
  alert "queue depth $DEPTH (expected <200)"
fi

# 4. Deadletter check
DLQ=$(redis-cli -h "${REDIS_HOST:-redis}" LLEN <agent_name>:deadletter 2>/dev/null || echo 0)
if [ "$DLQ" -gt 0 ]; then
  alert "deadletter has $DLQ items (manual recovery needed)"
fi

# 5. LLM API ping
python3 -c "
from anthropic import Anthropic
import sys
try:
    Anthropic().messages.create(
        model='claude-haiku-4-5-20251001',
        max_tokens=10,
        messages=[{'role':'user','content':'ok'}]
    )
except Exception as e:
    print(f'LLM API ping failed: {e}', file=sys.stderr)
    sys.exit(1)
" 2>/dev/null || alert "LLM API ping failed"

# 6. 結果通知
if [ ${#ALERTS[@]} -gt 0 ]; then
  MSG="🚨 <agent_name> health check ALERTS:\n"
  for a in "${ALERTS[@]}"; do
    MSG+="• $a\n"
  done

  if [ -n "$SLACK_WEBHOOK_URL" ]; then
    curl -X POST -H 'Content-type: application/json' \
      --data "{\"text\":\"$MSG\"}" \
      "$SLACK_WEBHOOK_URL" >/dev/null 2>&1
  fi
  exit 1
fi

echo "[OK] all health checks passed"
