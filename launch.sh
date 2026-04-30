#!/usr/bin/env bash
# Bring up all 4 chad-fleet services. Logs to /tmp/chad-fleet-<service>.log,
# PIDs to /tmp/chad-fleet-pids. Run `./launch.sh stop` to kill them all.
#
# Usage:
#   ./launch.sh           start all 4 services
#   ./launch.sh stop      kill all running fleet services
#   ./launch.sh status    show port + PID for each service

set -u

PYTHON="${PYTHON:-/opt/homebrew/bin/python3.11}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PID_FILE=/tmp/chad-fleet-pids
LOG_DIR=/tmp

# service-name port start-cmd-relative-to-ROOT
SERVICES=(
  "state-aggregator|8106|cd packages/state-aggregator && $PYTHON -m uvicorn state_aggregator.api:app --host 127.0.0.1 --port 8106"
  "view-registry|8108|cd packages/view-registry && $PYTHON -m uvicorn view_registry.api:app --host 127.0.0.1 --port 8108"
  "genui-renderer|8107|cd packages/genui-renderer && PORT=8107 npx tsx src/server.ts"
  "chad-dashboard|3000|cd apps/chad-dashboard && npm run dev"
)

cmd="${1:-start}"

case "$cmd" in
  start)
    : > "$PID_FILE"
    cd "$ROOT"
    for entry in "${SERVICES[@]}"; do
      name="${entry%%|*}"
      rest="${entry#*|}"
      port="${rest%%|*}"
      run="${rest#*|}"
      log="$LOG_DIR/chad-fleet-${name}.log"

      if lsof -nP -iTCP:"$port" -sTCP:LISTEN >/dev/null 2>&1; then
        echo "  $name :$port — already listening, skipping"
        continue
      fi

      bash -c "$run" > "$log" 2>&1 &
      pid=$!
      echo "$name $pid $port" >> "$PID_FILE"
      echo "  $name :$port — started (pid=$pid, log=$log)"
    done
    sleep 4
    echo ""
    echo "Verify with: ./launch.sh status"
    ;;

  stop)
    if [[ ! -f "$PID_FILE" ]]; then
      echo "no pid file at $PID_FILE — nothing to stop"
      exit 0
    fi
    while read -r name pid port; do
      if kill -0 "$pid" 2>/dev/null; then
        kill "$pid" 2>/dev/null && echo "  $name (pid=$pid) — killed"
      else
        echo "  $name (pid=$pid) — already gone"
      fi
    done < "$PID_FILE"
    rm -f "$PID_FILE"
    ;;

  status)
    for entry in "${SERVICES[@]}"; do
      name="${entry%%|*}"
      rest="${entry#*|}"
      port="${rest%%|*}"
      info=$(lsof -nP -iTCP:"$port" -sTCP:LISTEN 2>/dev/null | tail -1)
      if [[ -n "$info" ]]; then
        pid=$(echo "$info" | awk '{print $2}')
        echo "  $name :$port — UP (pid=$pid)"
      else
        echo "  $name :$port — DOWN"
      fi
    done
    ;;

  *)
    echo "usage: $0 {start|stop|status}" >&2
    exit 2
    ;;
esac
