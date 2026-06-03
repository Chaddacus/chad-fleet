#!/usr/bin/env bash
# resume_after_engine_merge.sh — bring per-task captains back online after
# the engine pre-req PR (#1, cycles C–H) merges to main.
#
# Use case: chad-fleet's PR #1 lands. Each captain that was paused
# (mode=observe_only) during the engine work is ready to resume autonomous
# dispatch against fresh code. This script does the mechanical steps:
#
#   1. Pull main + reinstall chad-captain editable so the daemon code is fresh.
#   2. (Optional) Flip a captain's apps_registry mode back to autonomous.
#   3. (Optional) Drop the stale roadmap.json so next tick replans clean.
#   4. Tick the captain once and report the resulting status line.
#
# Usage:
#   resume_after_engine_merge.sh <app_id> [--dry-run]
#       Resume one captain end-to-end.
#
#   resume_after_engine_merge.sh --refresh-only
#       Just pull + reinstall captain. Does not touch any registry.
#
# Defaults:
#   - Branch = main
#   - chad-fleet repo = /Users/chadsimon/code/chad-fleet
#   - Registry path = ~/.chad/captain/apps_registry.json (or
#     $CHAD_CAPTAIN_APPS_REGISTRY when set)

set -euo pipefail

REPO="${CHAD_FLEET_REPO:-/Users/chadsimon/code/chad-fleet}"
BRANCH="${CHAD_FLEET_BRANCH:-main}"
REGISTRY="${CHAD_CAPTAIN_APPS_REGISTRY:-$HOME/.chad/captain/apps_registry.json}"
FLEET_DIR="${CHAD_FLEET_APPS_DIR:-$HOME/.chad/fleet/apps}"

DRY_RUN=0
REFRESH_ONLY=0
APP_ID=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run)      DRY_RUN=1;       shift ;;
    --refresh-only) REFRESH_ONLY=1;  shift ;;
    -h|--help)
      sed -n '2,28p' "$0"
      exit 0 ;;
    -*)
      echo "unknown flag: $1" >&2
      exit 2 ;;
    *)
      if [[ -z "$APP_ID" ]]; then APP_ID="$1"; shift
      else
        echo "extra positional arg: $1" >&2
        exit 2
      fi ;;
  esac
done

run() {
  if [[ "$DRY_RUN" -eq 1 ]]; then
    printf 'DRY-RUN: %s\n' "$*"
  else
    printf '+ %s\n' "$*"
    eval "$@"
  fi
}

# ---- 1. Refresh chad-fleet + reinstall captain editable ------------------

# SAFETY: never auto-checkout the target branch. Checking out main from a
# feature branch silently abandons the user's working state. Instead, refuse
# to run if the working tree isn't already on $BRANCH, and let the operator
# switch explicitly.
CURRENT_BRANCH=$(git -C "$REPO" rev-parse --abbrev-ref HEAD 2>/dev/null || echo "")
if [[ "$CURRENT_BRANCH" != "$BRANCH" ]]; then
  echo "error: $REPO is on '$CURRENT_BRANCH', expected '$BRANCH'." >&2
  echo "       checkout $BRANCH manually before running this script." >&2
  exit 3
fi

echo "[1/4] Refreshing $REPO ($BRANCH)..."
run "git -C \"$REPO\" fetch --quiet origin $BRANCH"
LOCAL_SHA=$(git -C "$REPO" rev-parse "$BRANCH" 2>/dev/null || echo "none")
REMOTE_SHA=$(git -C "$REPO" rev-parse "origin/$BRANCH" 2>/dev/null || echo "none")
if [[ "$LOCAL_SHA" != "$REMOTE_SHA" ]]; then
  echo "  local $BRANCH at $LOCAL_SHA, remote at $REMOTE_SHA — fast-forwarding."
  run "git -C \"$REPO\" pull --ff-only --quiet origin $BRANCH"
else
  echo "  $BRANCH already at $LOCAL_SHA."
fi

# Workspace-root sync with --all-packages picks up every editable member.
# Per-app `uv --project apps/chad-captain sync` invalidates the workspace
# cache and triggers a root rebuild that fails when the root pyproject
# lacks a hatch wheel config — see chad-fleet pyproject.toml fix.
run "uv --project \"$REPO\" sync --quiet --all-packages --all-extras"

if [[ "$REFRESH_ONLY" -eq 1 ]]; then
  echo "Refresh-only mode — done."
  exit 0
fi

if [[ -z "$APP_ID" ]]; then
  echo "error: app_id required (or pass --refresh-only)" >&2
  exit 2
fi

# ---- 2. Flip mode back to autonomous if currently observe_only -----------

echo "[2/4] Checking apps_registry mode for $APP_ID..."
if [[ ! -f "$REGISTRY" ]]; then
  echo "  registry not found at $REGISTRY — skipping mode flip."
else
  CUR_MODE=$(python3 -c "
import json, sys
reg = json.load(open('$REGISTRY'))
app = next((a for a in reg.get('apps', []) if a.get('app_id') == '$APP_ID'), None)
print(app.get('mode', '') if app else 'NOTFOUND')
")
  if [[ "$CUR_MODE" == "NOTFOUND" ]]; then
    echo "  $APP_ID not in registry — skipping mode flip."
  elif [[ "$CUR_MODE" == "autonomous" ]]; then
    echo "  $APP_ID already autonomous — no flip needed."
  else
    echo "  $APP_ID is $CUR_MODE — flipping to autonomous..."
    if [[ "$DRY_RUN" -eq 0 ]]; then
      cp "$REGISTRY" "$REGISTRY.bak-$(date -u +%Y-%m-%dT%H%M%SZ)"
      python3 -c "
import json
reg = json.load(open('$REGISTRY'))
for a in reg.get('apps', []):
    if a.get('app_id') == '$APP_ID':
        a['mode'] = 'autonomous'
open('$REGISTRY', 'w').write(json.dumps(reg, indent=2))
"
    fi
  fi
fi

# ---- 3. Drop stale roadmap so captain replans fresh ----------------------

ROADMAP="$FLEET_DIR/$APP_ID/roadmap.json"
echo "[3/4] Stale roadmap check at $ROADMAP..."
if [[ -f "$ROADMAP" ]]; then
  ALL_TERMINAL=$(python3 -c "
import json
rm = json.load(open('$ROADMAP'))
states = {s.get('status') for s in rm.get('slices', [])}
terminal = {'done', 'skipped', 'blocked'}
print('YES' if states.issubset(terminal) else 'NO')
")
  if [[ "$ALL_TERMINAL" == "YES" ]]; then
    echo "  roadmap fully terminal — moving aside so next tick replans..."
    run "mv \"$ROADMAP\" \"${ROADMAP}.pre-resume-$(date -u +%Y-%m-%dT%H%M%SZ)\""
  else
    echo "  roadmap has dispatchable slices — leaving in place."
  fi
else
  echo "  no roadmap on file — captain will create one on first tick."
fi

# ---- 4. Tick once + report -----------------------------------------------

echo "[4/4] Ticking $APP_ID..."
if [[ "$DRY_RUN" -eq 1 ]]; then
  echo "DRY-RUN: would run: uv --project \"$REPO/apps/chad-captain\" run chad-captain tick --app $APP_ID"
else
  uv --project "$REPO/apps/chad-captain" run chad-captain tick --app "$APP_ID" || true
fi

echo "Done."
