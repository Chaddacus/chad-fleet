#!/usr/bin/env bash
# Launch the hub with secrets injected from Bitwarden Secrets Manager (bws).
#
# Secrets (EMAIL_IMAP_HOST/USER/PASSWORD, EMAIL_SMTP_HOST, HUB_AUTH_*, LLM_API_KEY, ...) live
# in a Secrets Manager project; bws injects them as env vars by key name, so launch.sh's
# services pick them up. No plaintext on disk.
#
# Project id resolution (no hardcoded UUID — stays shippable):
#   1. $BWS_PROJECT_ID, else
#   2. a gitignored ./.bws-project file (one line: the project UUID).
#
# Setup once:   echo <your-project-uuid> > .bws-project
# Daily use:    ./scripts/launch-with-secrets.sh   (forwards args to launch.sh: start|stop|status)
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# Token + project-id resolution (env → Keychain → .bws-project). Shared with run-service.sh.
source "$ROOT/scripts/bws-resolve.sh" "$ROOT" || exit 1

exec bws run --project-id "$PROJECT_ID" -- "$ROOT/launch.sh" "$@"
