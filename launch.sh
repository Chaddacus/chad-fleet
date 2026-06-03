#!/usr/bin/env bash
# Bring up all 4 chad-fleet services. Logs to /tmp/chad-fleet-<service>.log,
# PIDs to /tmp/chad-fleet-pids. Run `./launch.sh stop` to kill them all.
#
# Usage:
#   ./launch.sh           start all 4 services
#   ./launch.sh stop      kill all running fleet services
#   ./launch.sh status    show port + PID for each service

set -u

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PID_FILE=/tmp/chad-fleet-pids
LOG_DIR=/tmp

# Service table is shared with scripts/run-service.sh (the per-service LaunchAgent runner).
# The hub is fully self-contained: admiral (agent backend) + aggregator (projection) +
# genui (rendering) + dashboard (the hub UI). No third-party front door.
source "$ROOT/scripts/services.sh"

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
