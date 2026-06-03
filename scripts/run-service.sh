#!/usr/bin/env bash
# Run ONE chad-fleet service in the FOREGROUND with Bitwarden secrets injected.
# This is what each per-service LaunchAgent executes — launchd watches THIS process, so the
# service command must stay attached (uvicorn / tsx / next dev all block in the foreground;
# unlike launch.sh, we do NOT background with `&`). On crash, the process exits and launchd's
# KeepAlive restarts it.
#
#   ./scripts/run-service.sh <service-name>
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "$ROOT/scripts/services.sh"

NAME="${1:?usage: run-service.sh <service-name>}"

CMD=""
for entry in "${SERVICES[@]}"; do
  n="${entry%%|*}"
  rest="${entry#*|}"
  run="${rest#*|}"
  if [ "$n" = "$NAME" ]; then CMD="$run"; break; fi
done
if [ -z "$CMD" ]; then
  echo "unknown service '$NAME'. Known:" >&2
  for entry in "${SERVICES[@]}"; do echo "  ${entry%%|*}" >&2; done
  exit 2
fi

source "$ROOT/scripts/bws-resolve.sh" "$ROOT" || exit 1

cd "$ROOT"
# bws run flattens its COMMAND args into ONE string and runs it via --shell; passing the
# command as a single token (NOT `bash -c <token>`, which bws would re-flatten and break the
# `cd subdir && ...` quoting) lets bash run it intact from $ROOT.
exec bws run --project-id "$PROJECT_ID" --shell bash -- "$CMD"
