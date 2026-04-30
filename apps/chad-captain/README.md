# chad-captain

Captain app: an LLM (Claude/Codex via Pro/Max + ChatGPT Plus subscriptions)
that supervises a fleet of always-on goose daemons, one per tracked
application. Chad is the **admiral** — he reads a dashboard, leaves notes,
and only steps in when the captain escalates. The captain handles
dispatch, validation, retry, and replanning autonomously.

**License:** proprietary (closed source).

## Roles

```
Admiral (Chad)              Captain (this app)         Goose-runner (per app)
   │                              │                              │
   └─ writes admiral_notes ──────►│                              │
                                  │                              │
                                  ├─ replan / dispatch ─────────►│
                                  │   (writes current_slice.json)│
                                  │                              ├─ runs goose
                                  │                              │   (writes
                                  │                              │   slice_complete.json)
                                  │◄────── reads completion ─────┤
                                  ├─ validates (rubric + scorecard)
                                  ├─ accept / reject / replan
                                  └─ writes captain_log.jsonl
```

## Per-app workspace layout

Each registered app has a workspace under
`~/.chad/fleet/apps/<app_id>/`:

| File | Writer | Reader |
|------|--------|--------|
| `current_slice.json`   | captain | goose-runner |
| `progress.jsonl`       | goose-runner | captain, dashboard |
| `slice_complete.json`  | goose-runner | captain |
| `roadmap.json`         | captain | dashboard |
| `captain_log.jsonl`    | captain | dashboard |
| `admiral_notes/*.json` | dashboard / admiral | captain |
| `research/app-profile.json` | research pipeline | dashboard |
| `slice_baseline.json`  | captain (at dispatch) | captain (at validate) |

All writes are atomic (`tempfile + rename`). One writer per file, no
locks. The dashboard is read-only against this layout (writes happen
through the API).

## Decision rubric

The captain's validator (`chad_captain.validator.validate_slice`) maps
every slice completion to a verdict:

| Signal | Verdict |
|--------|---------|
| cheat patterns detected (e.g. `assert True`) | `escalate` |
| goose timeout (`exit -9`) | `kill_replan` |
| goose `exit != 0`, never retried | `reject_retry` |
| goose `exit != 0`, already a retry | `reject_hard` |
| `exit 0` + `files_changed = []` | `reject_retry` (or `reject_hard` on retry) |
| `exit 0` + delta ≥ +0.5pp | `accept` |
| `exit 0` + delta in `[0, 0.5pp)` | `soft_accept` |
| delta `< 0`, never retried | `reject_retry` |
| delta `< 0`, already a retry | `revert` |

The "delta" is the percentage-point change in the compliance scorecard
(`chad_captain.scorecard.score_repo`) between dispatch and slice complete.

## Compliance scorecard

Seven baseline dimensions every app gets graded on, plus per-app extras:

- `tests_present`, `tests_recent`, `todo_pressure`, `skip_pressure`,
  `secret_hygiene`, `file_size_health`, `docs_present`

App-specific extras live in `chad_captain/extras/` and are registered in
`EXTRAS_FACTORIES`. Spark adds `voice_guide_intact` +
`chapters_word_count_target`; author-toolkit adds `sentinel_present` +
`typescript_typecheck_clean`.

## Replanner

When the captain needs work, `chad_captain.replanner.replan_if_needed`
detects the trigger (initial / exhausted / soft-accept-streak /
admiral-note) and calls `replan` to produce 3-7 surgical, sequenced
slices. The primary path uses Claude Opus via the Pro/Max CLI; on any
LLM failure we fall back to a deterministic skeleton derived from the
weakest scorecard dimensions.

## Modes

Each registered app picks one:

- **autonomous** — captain dispatches goose-runner; full slice loop.
- **observe_only** — captain runs scorecard + replanner only; the
  admiral drives changes manually. Spark of Defiance is observe_only
  because the work is human writing.

## Quickstart

### 1. Install + sync

```bash
cd apps/chad-captain
uv sync --extra dev
```

### 2. Seed the registry

```bash
uv run chad-captain register --seed-defaults
```

This writes `~/.chad/captain/apps_registry.json` with two apps:
- `spark-of-defiance` (observe_only, 9am ET)
- `author-toolkit` (autonomous, 10am ET)

### 3. Init each workspace

```bash
uv run chad-captain init-workspace --replan
```

This creates `~/.chad/fleet/apps/<id>/` and runs the LLM replanner to
seed an initial roadmap. Use `--no-llm` for the deterministic fallback
if you don't want to burn an Opus call.

### 4. Run a manual tick

```bash
uv run chad-captain tick --app author-toolkit
```

For observe_only apps the tick refreshes the roadmap (if needed) and
exits. For autonomous apps it dispatches the next queued slice into
`current_slice.json` and the goose-runner picks it up.

### 5. Schedule daily ticks (macOS launchd)

```bash
uv run chad-captain install-plists                          # writes ~/Library/LaunchAgents/com.chadcaptain.<id>.plist
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.chadcaptain.author-toolkit.plist
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.chadcaptain.spark-of-defiance.plist
```

Use `--dry-run` to preview the plists without writing.

### 6. Run the API (dashboard backend)

```bash
uv run chad-captain-api --host 127.0.0.1 --port 8109
```

Endpoints:
- `GET  /apps`
- `GET  /apps/{id}`
- `GET  /apps/{id}/roadmap`
- `GET  /apps/{id}/log?limit=50`
- `GET  /apps/{id}/scorecard?repo_path=...`
- `GET  /apps/{id}/research`
- `POST /apps/{id}/note`
- `POST /apps/{id}/replan`
- `POST /apps/{id}/tick`

## CLI reference

| Command | What it does |
|---------|--------------|
| `chad-captain tick --app <id>` | Single captain tick (what launchd invokes) |
| `chad-captain register --seed-defaults` | Bootstrap the apps registry |
| `chad-captain init-workspace [--replan]` | Scaffold per-app workspaces |
| `chad-captain replan --app <id> --repo <path>` | Force a replan |
| `chad-captain research --app <id> --repo <path>` | Build/show research profile |
| `chad-captain install-plists [--dry-run]` | Generate launchd plists |
| `chad-captain status` | Print scheduler + fleet summary as JSON |

Legacy commands (still wired): `run`, `daemon`, `brief`, `alerts`,
`actions`. These predate the captain-fleet architecture; they remain
for compatibility with the daily-brief surface.

## Soak test

A small loop that drives `captain_tick` against a fake-goose stub and
asserts steady-state behavior over ≥4 slices:

```bash
uv run python scripts/soak_test.py --cycles 8
```

Run before any non-trivial captain change.

## Module map

| Module | Responsibility |
|--------|----------------|
| `protocol.py` | Pydantic models + per-app workspace paths + atomic I/O |
| `goose_runner.py` | Subprocess wrapper that runs `goose run` against a slice |
| `validator.py` | `validate_slice` decision rubric + `captain_tick` |
| `scorecard.py` | Seven-dimension baseline compliance scorer |
| `extras/` | Per-app dimension overlays |
| `research/` | Local repo scan + web competitive landscape |
| `replanner.py` | LLM-driven roadmap generator (Opus) + deterministic fallback |
| `apps_registry.py` | JSON-backed registry of tracked apps + modes |
| `launchd.py` | macOS plist generator for daily ticks |
| `api.py` | FastAPI HTTP surface (port 8109) |
| `cli.py` | Argparse entrypoint dispatching all subcommands |
| `llm.py` | Pro/Max + Plus subscription CLI adapter (no API keys) |

## Fleet context

See [../../README.md](../../README.md).
