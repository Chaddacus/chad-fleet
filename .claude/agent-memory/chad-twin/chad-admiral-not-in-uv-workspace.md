---
name: chad-admiral-not-in-uv-workspace
description: chad-admiral isn't a uv workspace member; how it runs + launch.sh service set
metadata:
  type: project
---

`apps/chad-admiral` has **no pyproject.toml and is NOT a uv workspace member** (root
`pyproject.toml` `[tool.uv.workspace]` members list excludes it). So `chad_admiral` is not
importable from the repo `.venv` — codegen/tests cannot `import chad_admiral`.

It runs standalone via its own ephemeral env:
`cd apps/chad-admiral && PYTHONPATH=src uv run --with fastapi --with uvicorn --with pydantic --python 3.11 python -m uvicorn chad_admiral.server:app --host 127.0.0.1 --port 8901`
It exposes OpenAI shape: `GET /v1/models`, `POST /v1/chat/completions` (SSE stream via `reply_stream`).
A `"ok"` user message is a probe → replies "OK" with NO captain dispatch; single-turn intake →
Gate-A discovery (read-only git touch) → still no dispatch. Dispatch only on 2nd+ user turn.

**launch.sh revalidation (2026-06-02):** launch.sh did NOT start the admiral (only aggregator,
view-registry, genui, dashboard) and never referenced Odysseus. Added chad-admiral (:8901) as the
first service so the hub is self-contained. The plan's "drop Odysseus from launch.sh" was stale —
Odysseus was never in it.

**launch.sh / system-python gotcha (2026-06-02):** launch.sh historically ran Python services via
`$PYTHON` = `/opt/homebrew/bin/python3.11` (system). That interpreter has `state_aggregator` (old
editable install) but NOT newer workspace deps like `email_mcp` → `EmailSource` silently caught the
ImportError and returned []. Fix: services with workspace deps must launch via `uv run` (changed the
state-aggregator line to `cd packages/state-aggregator && uv run python -m uvicorn ...`). Any new
Python service that depends on a sibling workspace package must use `uv run`, not `$PYTHON`.

**Auto-start + auto-heal (no launch command on the user) — 2026-06-02:** the hub runs as **one
LaunchAgent per service** (`com.chadsimon.chad-fleet.<service>.plist`, 5 of them; matches Chad's
`com.chadsimon.*` naming). Chosen over a single login-start agent because Chad lives in this console
and wants crash-recovery, not just start-at-login. `scripts/install-launchagent.sh {install,
uninstall,status}` generates/loads them. Each plist: `RunAtLoad` (login start) + `KeepAlive=true`
(restart on any exit) + `ThrottleInterval=10` + explicit `PATH`
(`~/.cargo/bin:/opt/homebrew/bin:...`, because launchd runs with a bare PATH and otherwise can't
find `bws`/`uv`/`node`/`npm`). NO `AbandonProcessGroup` — each agent runs ONE service in the
**foreground** via `scripts/run-service.sh <name>`, so launchd watches the real process and KeepAlive
sees crashes. (Auto-heal proven: `kill -9` the dashboard → respawned in ~3s.)

Shared pieces (dedup): `scripts/services.sh` = the canonical `name|port|cmd` table, sourced by both
`launch.sh` (manual all-at-once dev) and `run-service.sh` (per-service agents). `scripts/bws-resolve.sh`
= sourced token+project-id resolver, shared by `run-service.sh` and `launch-with-secrets.sh`.

**`bws run` flattens its COMMAND args (cost real debugging):** `bws run -- bash -c "cd X && cmd"`
does NOT exec argv — bws JOINS all COMMAND tokens into one string and runs it via `--shell`, which
destroys nested `bash -c` quoting (it becomes `bash -c cd X && cmd`, so `&& cmd` runs in bws's shell
back at the original cwd; the `cd` is lost). Fix: pass the command as a SINGLE token and let bws's
shell run it: `bws run --project-id <id> --shell bash -- "$CMD"`. Symptom when wrong: services that
need their `cd subdir` (genui→`ROOT/src/server.ts` not found, dashboard→`ROOT/package.json` ENOENT,
admiral→`No module named chad_admiral`) while cwd-independent ones (aggregator/view-registry import
from root `.venv`) appear to work.

**view-registry must use `uv run`, not system `$PYTHON`, under launchd:** with `$PYTHON -m uvicorn`
the agent's process hung at 0% CPU, never bound :8108, never logged startup (worked fine in a manual
terminal — launchd/stdout-specific). It has its own `pyproject.toml` (uv workspace member), so
`uv run python -m uvicorn` binds in ~6s. Reinforces the rule: all workspace Python services launch via
`uv run`.

**BWS token under launchd:** `BWS_ACCESS_TOKEN` lives in Chad's `~/.zshrc`, which launchd does NOT
source. `bws-resolve.sh` resolves the token from env first, then macOS Keychain
(`security find-generic-password -w -s chad-fleet-bws-token -a "$USER"`). The one-time token store is
Chad's action (his secret — I never handle the value):
`security add-generic-password -s chad-fleet-bws-token -a "$USER" -w "$BWS_ACCESS_TOKEN"` (Chad ran
this 2026-06-02; entry is PRESENT). Verified the resolver works in a fully empty env (`env -i`) → pulls
from Keychain → launchd-compatible; bws injection reaches the agent-run aggregator (25 live emails in
the snapshot). The installer is token-aware: writes plists always but only bootstraps+kickstarts once
the token is resolvable, so it never KeepAlive-crash-loops before setup.

**How to apply:** to verify admiral-touching work, start it via the `uv run --with` line above, not
via the workspace. Don't add it as a hub-contracts codegen import source. See [[hub-contracts-pattern]]
and [[hub-capability-pattern]].
