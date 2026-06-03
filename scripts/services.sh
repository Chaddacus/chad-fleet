#!/usr/bin/env bash
# Canonical chad-fleet service table — the ONE place service commands live.
# Sourced by launch.sh (manual all-at-once dev) and scripts/run-service.sh (per-service agents).
# Format: "name|port|command"  (command is run relative to repo ROOT via `bash -c`).
#
# Python services that depend on sibling workspace packages must use `uv run`, not system
# python — the system interpreter lacks newer workspace deps (e.g. email_mcp) and the source
# silently degrades. See agent-memory chad-admiral-not-in-uv-workspace.
PYTHON="${PYTHON:-/opt/homebrew/bin/python3.11}"

SERVICES=(
  "chad-admiral|8901|cd apps/chad-admiral && PYTHONPATH=src uv run --with fastapi --with uvicorn --with pydantic --python 3.11 python -m uvicorn chad_admiral.server:app --host 127.0.0.1 --port 8901"
  "state-aggregator|8106|cd packages/state-aggregator && uv run python -m uvicorn state_aggregator.server:app --host 127.0.0.1 --port 8106"
  "view-registry|8108|cd packages/view-registry && uv run python -m uvicorn view_registry.api:app --host 127.0.0.1 --port 8108"
  "genui-renderer|8107|cd packages/genui-renderer && PORT=8107 npx tsx src/server.ts"
  "chad-dashboard|3000|cd apps/chad-dashboard && npm run dev"
)
