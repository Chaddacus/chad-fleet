---
name: chad-fleet-admiral
description: chad-fleet HUB admiral tier — Odysseus front-door integration, all slices S1–S5 + S4b done; how to run/verify; what's out of scope
metadata:
  type: project
---

The **chad-fleet HUB** (`~/code/chad-fleet`) is Chad's Admiral/Captain/Fleet
orchestrator; goose is the captain's outer loop. The **admiral tier**
(`apps/chad-admiral`) is the reactive brain, exposed as an **OpenAI-compatible
service on :8901** so **Odysseus** (`~/code/odysseus`, PewDiePie's MIT
self-hosted AI workspace, runs on :7050) talks to it as a "model" — Odysseus is
the user's front door.

**Why:** Chad wanted Odysseus specifically as "that front door for the user to
interact with." The admiral replaces the throwaway `odysseus-fleet-prototype/stub_admiral.py`.

**How to apply:** the admiral is stateless — Odysseus replays the full transcript
each turn, so admiral state is reconstructed from the chat (1 user turn =
discovery+GateA, ≥2 = dispatch). Slice status lives in `apps/chad-admiral/README.md`
(authoritative). As of 2026-06-02 **all named slices S1–S5 + S4b are done**:
- **S1** discovery + Gate-A + real auto_runtime tracks + omni-mem dossiers.
- **S2** dispatch executes the captain via chad-captain's `goose_runner` CLI
  one-shot; result streamed in the chat turn.
- **S4** ContractKernel authority gate (`~/automation_architecture/bin/authority.mjs`
  → `src/authority-cli.ts`) + post-exec path secret gate `scan_changed_files`.
- **S4b** secret-*content* gate: `authority.mjs --security-scan` pipes path-allowed
  file content through kernel `evaluateSecurity` (hard-deny secret patterns). A
  path-denied file (`.env`) is NEVER content-read (honors no-read-.env rule).
- **S3** in-band escalation + stateless resume, see [[chad-fleet-escalation-gotchas]].
- **S5** parallel dispatch (all cleared captains run concurrently, one daemon
  thread each), per-task labeled reply-routing, and multi-task escalation as a
  numbered Gate-B list with carry-forward of un-answered captains.

**Explicitly OUT OF SCOPE of the admiral slices** (HUB_ARCHITECTURE §7, separate
epics that cross other repos — don't bundle without Chad's direction): omni-mem
two-layer rework (raw + checkpoints), the tool/MCP registry (`allowed_tools` stays
empty until it lands), the monitoring surface.

**Run the admiral:** `cd ~/code/chad-fleet/apps/chad-admiral && PYTHONPATH=src
uv run --with fastapi --with uvicorn --with pydantic --python 3.11 python -m
chad_admiral.server` (0.0.0.0:8901). goose uses `.goose-runtime/config` symlinked
to `~/.config` (gemini_oauth provider; the captain's shipped runtime had a stale
Codex/gpt-5.5 config that 400s). Verification stays on `~/code/scratch-demo`
(baseline HEAD c5220ac) and `~/code/scratch-demo2` (baseline bc174b3) — never
mutate helm/sentinel with goose unreviewed.
