#!/usr/bin/env bash
# Resolve Bitwarden Secrets Manager access — sourced, not executed.
# Sets BWS_ACCESS_TOKEN and PROJECT_ID, or prints a hint and `return 1`s.
#
# Token: env first, then macOS Keychain (launchd does NOT inherit ~/.zshrc). Store once:
#   security add-generic-password -s chad-fleet-bws-token -a "$USER" -w "$BWS_ACCESS_TOKEN"
# Project id: $BWS_PROJECT_ID, else a gitignored ./.bws-project (one line, the project UUID).
#
# Usage:  source "$ROOT/scripts/bws-resolve.sh" || exit 1   (with $ROOT set to the repo root)
_bws_resolve() {
  local root="$1"

  if ! command -v bws >/dev/null 2>&1; then
    echo "bws (Bitwarden Secrets Manager CLI) not found on PATH." >&2
    return 1
  fi

  if [ -z "${BWS_ACCESS_TOKEN:-}" ] && command -v security >/dev/null 2>&1; then
    BWS_ACCESS_TOKEN="$(security find-generic-password -w -s chad-fleet-bws-token -a "${USER:-$(id -un)}" 2>/dev/null || true)"
    export BWS_ACCESS_TOKEN
  fi
  if [ -z "${BWS_ACCESS_TOKEN:-}" ]; then
    echo "BWS_ACCESS_TOKEN is not set and not in Keychain (service 'chad-fleet-bws-token')." >&2
    echo "Set it once:  security add-generic-password -s chad-fleet-bws-token -a \"\$USER\" -w \"\$BWS_ACCESS_TOKEN\"" >&2
    return 1
  fi

  PROJECT_ID="${BWS_PROJECT_ID:-}"
  if [ -z "$PROJECT_ID" ] && [ -f "$root/.bws-project" ]; then
    PROJECT_ID="$(tr -d '[:space:]' < "$root/.bws-project")"
  fi
  if [ -z "$PROJECT_ID" ]; then
    echo "No project id. Set BWS_PROJECT_ID, or: echo <project-uuid> > $root/.bws-project" >&2
    return 1
  fi
  export PROJECT_ID
}

_bws_resolve "${1:?bws-resolve.sh needs the repo root as arg}"
