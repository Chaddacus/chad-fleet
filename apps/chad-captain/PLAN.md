# Captain's Bridge — Implementation Plan

**Goal**: Captain (Claude/Codex LLM) directs an always-on fleet. Per-app goose daemons execute slices. Captain validates + replans + handles 95% inline. Admiral (Chad) watches the dashboard and only intervenes on human-only decisions.

## Architecture

```
ADMIRAL (Chad) — leaves notes, decides escalations
   ↕
DASHBOARD (auto-refresh, L1 bridge / L2 app drill / L3 slice evidence)
   ↕  HTTP / polling
CAPTAIN (chad-captain async daemon — Python)
  ├─ validator: reads slice_complete, runs rubric suite, accept/reject/escalate
  ├─ replanner: research + scorecard + notes → roadmap.json
  ├─ scheduler: 1-min tick across all apps
  └─ HTTP API on :8109 for dashboard
   ↕  filesystem protocol (~/.chad/fleet/apps/<app_id>/)
GOOSE-RUNNER (Python supervisor, one per app)
   └─ shells out to `goose run --no-session ...` per slice
   ↕  goose runtime sandboxed via XDG_CONFIG_HOME
GOOSE (block-goose 1.30, codex-acp provider)
   └─ does the work in <repo_path>
```

## Configuration boundary

Captain owns its goose runtime entirely. **No reuse of `~/.config/goose`.**

```
chad-fleet/apps/chad-captain/goose-runtime/
├─ config/goose/config.yaml      ← captain's opinionated config
├─ state/goose/                   ← session DB, logs (gitignored)
├─ data/goose/                    ← data home (gitignored)
├─ recipes/                       ← captain's curated recipes
└─ skills/                        ← captain's curated skill set
```

Per-slice invocation:
```bash
XDG_CONFIG_HOME=$CAPTAIN/goose-runtime/config \
XDG_STATE_HOME=$CAPTAIN/goose-runtime/state \
XDG_DATA_HOME=$CAPTAIN/goose-runtime/data \
goose run --no-session --max-turns 80 --max-tool-repetitions 5 \
  --system "$slice_system_prompt" --text "$slice_user_prompt"
```

## Per-app workspace

```
~/.chad/fleet/apps/<app_id>/
├─ current_slice.json         ← captain writes, goose-runner reads
├─ progress.jsonl             ← goose-runner appends, captain tails
├─ slice_complete.json        ← goose-runner writes once, captain reads+deletes
├─ roadmap.json               ← captain writes (replanned), dashboard reads
├─ admiral_notes/<ts>.json    ← dashboard writes, captain reads on tick
├─ captain_log.jsonl          ← captain appends, dashboard tails
├─ research/app-profile.json  ← research pipeline writes (weekly cache)
└─ scorecard-history.jsonl    ← rubric suite appends, captain reads
```

All atomic writes. One writer per file (no locking needed).

## Slice plan

| # | Slice | Est LOC | Depends on |
|---|---|---|---|
| **S0** | Captain's goose runtime + per-app workspace scaffold | ~80 | — |
| **S1** | Protocol schemas (Pydantic models) + atomic writers | ~150 | S0 |
| **S2** | Per-app goose-runner daemon | ~250 | S1 |
| **S3** | Captain validator + decision rubric | ~300 | S2 |
| **S4** | End-to-end tracer smoke test (fake test-app, 3 trivial slices) | ~100 | S3 |
| **S5** | Research pipeline (local scan + WebFetch) | ~400 | S1 |
| **S6** | Compliance rubric scorer | ~250 | — (parallel) |
| **S7** | App-specific dimensions overlay | ~200 | S5, S6 |
| **S8** | Replanner (research + scorecard + notes → roadmap) | ~400 | S5, S7 |
| **S9** | Captain HTTP API (FastAPI on :8109) | ~300 | S3, S8 |
| **S10** | Dashboard L1 bridge (auto-render, polling) | ~300 | S9 |
| **S11** | Dashboard L2 app drill + admiral note input + response thread | ~350 | S10 |
| **S12** | Dashboard L3 slice evidence | ~150 | S11 |
| **S13** | Admiral note → captain incorporation → response loop end-to-end | ~150 | S11, S8 |
| **S14** | Spark + author-tk registered as real apps + launchctl plists | ~150 | S4, S13 |
| **S15** | 4-hour soak test + dogfood | ~50 | S14 |
| **S16** | README + ops guide | ~150 docs | S15 |

**Total**: ~3,500 LOC. ~75% new code, ~25% reuse (`obsessive_loop.py`, `run_rubric_suite.py`, `enterprise_rubric_scorer.py`, `auto_runtime.py`, `chad_agent.atomic`, marketing's `llm.py`).

## Decision rubric (captain validator)

| Signal | Action |
|---|---|
| Δ ≥ +0.5pp + no regression + tests pass | **accept** → next slice |
| Δ < +0.1pp + no regression | **soft accept** + flag (replan if 2 in a row) |
| Regression on previously-passing gate | **reject + auto-retry** with hint |
| Regression after retry | **hard reject + revert** + replan slice |
| Stalled >20min no tool calls | **kill + replan** |
| Tests broken, captain can't diagnose | **escalate** to admiral |
| Replan ladder exhausted (3 retries, no movement) | **escalate** |

## Gating

Each slice ships with: typecheck pass + unit tests green + manual smoke (where applicable). No pre-existing regressions introduced. Captain's auto-runtime track records evidence.

## Initial app set (per Chad)

V1: **Spark of Defiance + author-toolkit only**. OpenShield + chad-agent in v2 once protocol proven.

## Open items deferred to v2

- ACP server mode (`goose serve`) — deferred until subprocess pattern proves insufficient
- SSE push from captain → dashboard — deferred (polling is fine for one user)
- Direct roadmap manipulation in dashboard — view-only + steer-via-notes for v1
- Real-time goose stdout streaming to dashboard — progress.jsonl tail is enough
