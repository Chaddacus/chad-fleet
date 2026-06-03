# chad-admiral

The reactive **admiral** tier of the chad-fleet HUB (HUB_ARCHITECTURE.md § 2),
exposed as an **OpenAI-compatible service** so the Odysseus front door talks to
it as a model. It runs discovery, freezes `CaptainDossier`s into omni-mem, and
spawns captains as `auto_runtime` tracks.

This replaces the throwaway `~/code/odysseus-fleet-prototype/stub_admiral.py`.

## Loop (stateless — reconstructed from the chat transcript)

```
1 user turn  (task list)  -> DISCOVERY -> GATE A (batched clarifications)
>=2 user turns            -> DISPATCH  (freeze dossiers + spawn captain tracks)
```

## Run

```bash
cd ~/code/chad-fleet/apps/chad-admiral
PYTHONPATH=src uv run --with fastapi --with uvicorn --with pydantic --python 3.11 \
  python -m chad_admiral.server          # 0.0.0.0:8901
```

Register `http://host.docker.internal:8901/v1` as a model endpoint in Odysseus
(see `~/code/odysseus-fleet-prototype/README.md` for the front-door setup).

## Modules

| File | Role | Real backend |
|------|------|--------------|
| `intake.py` | deterministic task-list parser (chat text → `TaskItem[]`) | — |
| `discovery.py` | lightweight repo touch: resolve path, read git HEAD, find gaps | git |
| `admiral.py` | the intake→GateA→dispatch state machine | — |
| `dispatch.py` | freeze `CaptainDossier` → omni-mem; spawn captain → `auto_runtime` | omni-mem CLI, `auto_runtime.py` |
| `types.py` | `CaptainDossier`, `EscalationPacket`, `TaskItem`, `DiscoveryResult` | — |
| `server.py` | OpenAI-compatible `/v1/models` + `/v1/chat/completions` | — |

## Slice status

**Slice 1 (DONE, verified live through Odysseus):** real discovery (git HEADs),
gap-driven Gate-A, dispatch creates real `auto_runtime` tracks + real omni-mem
dossier threads.

**Slice 2 + dispatch-execution (DONE, verified live end-to-end):** dispatch now
*executes* the captain via the real fleet executor and the result surfaces in the
chat turn. `captain.py` writes a `current_slice.json` and invokes chad-captain's
`goose_runner` one-shot (`python -m chad_captain.goose_runner --max-iters 1`,
boundary-clean CLI, no cross-app import), reads back `slice_complete.json`. The
admiral streams the dispatch (`reply_stream`) with heartbeats so goose can run
inline without the SSE timing out, then marks `slice-1` **accepted** on the track
with evidence (`auto_runtime update-node`). Verified: a task dispatched in Odysseus
created `PROOF.md` in scratch-demo, `goose_exit=0`, slice accepted.
  - **Safety gate:** captains only auto-execute in repos under `_SAFE_REPO_PREFIXES`
    (scratch-demo). Real repos return "execution gated — awaiting ContractKernel
    authority lease (S4)". Override with `ADMIRAL_EXECUTE_ALL=1`.
  - **goose runtime:** `.goose-runtime/config` symlinks `~/.config` so goose uses
    the working gemini_oauth provider (the captain's shipped runtime had a stale
    Codex/gpt-5.5 config that 400s); state/data stay sandboxed.

**Slice 4 — ContractKernel authority gate (DONE, verified live):** real-repo
execution is now **ungated but governed**. Before any captain runs, the admiral
calls the ContractKernel via a new TS shim (`~/automation_architecture/bin/authority.mjs`
→ `src/authority-cli.ts`): `canGrantCaptaincyLease` (no overlapping lease) +
`evaluatePolicyHook(before_command)` → `{action, overrideTier}`. `authority.py`
(fail-closed) executes only on `ALLOW` + tier in `{T0_ALLOW, T1_ADMIRAL_OVERRIDE}`;
`DENY` / `REQUIRE_HUMAN_APPROVAL` / `T2`/`T3` / lease-conflict → block + escalate.
The hardcoded scratch safelist is gone. Verified: helm → `ALLOW/T0` (eligible);
`.env` write → `DENY/T3` (blocked); e2e through Odysseus showed `authority granted
(T0_ALLOW)` then real execution.
  - **Note:** real repos (helm/sentinel) are now eligible at T0 under the default
    policy pack — a real-repo dispatch WILL execute goose (with auto-commit).
    Verification stayed on scratch-demo to avoid unreviewed real-repo mutation.
  - **Pre-execution gate** is `before_command` (may this captain run at all).
  - **Post-execution secret gate (DONE, verified):** after the captain runs,
    `authority.scan_changed_files` scans everything the slice touched —
    `git diff --name-only <dossier.rlm_ref> HEAD` (the captain auto-commits, so
    changes land in commits + leave a clean tree) UNION `git status --ignored` —
    through the `before_file_write` policy. Any denied file (`.env`/secrets at T3)
    → slice **REJECTED** (state `rework`, NOT accepted), surfaced in the chat.
    Verified e2e: a dispatched `.env`-writing task was executed by goose then
    rejected by the gate.
    - **Why not the executor's `files_changed`:** it proved unreliable — it
      reported `['PROOF2.md']` while goose had actually written `.env`. The gate
      therefore scans the git tree independently, not the executor's accounting.
  - **Secret-*content* gate (Slice 4b — DONE, verified):** the path gate can't see
    a secret VALUE dropped into an otherwise-allowed file (an API key written into
    `config.py`). `scan_changed_files` now runs two layers per changed file,
    fail-closed: (1) PATH via `before_file_write`, then (2) for path-allowed files
    only, CONTENT via the kernel's `evaluateSecurity` (hard-deny secret patterns:
    `sk-…`, AWS keys, `BEGIN … PRIVATE KEY`, GitHub/Slack tokens) through a new
    `authority.mjs --security-scan` mode (content piped on stdin). A path-denied
    file (`.env`) is **never content-read**, honoring the no-read-`.env` rule.
    Verified on a throwaway repo: secret-in-`config.py` → REJECTED (secret-content
    T3); clean file → clean; `.env` → caught by PATH gate, content never scanned.
    - **Still open:** true mid-run `before_file_write` interception (today both
      gates are post-execution — the write happens, then the slice is rejected and
      the commit left for manual review, not auto-reverted).

**Slice 3 — in-band escalation + resume (DONE, verified live through Odysseus):**
a captain that hits a genuine direction/authority ambiguity parks itself instead
of guessing, the question surfaces in the chat, and the operator's next message
resumes the *exact* captain.
  - **Captain side** (`captain.py`): the system prompt instructs goose to emit a
    single line `ESCALATE: <question>` and stop when (and only when) it cannot
    proceed without a human decision. `_detect_escalation` scans the goose summary
    + log tail for the marker and **rejects the system-prompt echo** (goose echoes
    the instruction template `ESCALATE: <one-line question>` into its log — a naive
    search false-positives on it; the detector skips the placeholder/instruction
    text). The escalation question rides back on `slice_complete["escalation"]`.
  - **Stateless resume — no Odysseus patch, no session-id propagation.** The
    earlier plan needed Odysseus's `session_id` to push async; rejected — it would
    mean threading session_id through `chat_stream → chat_handler → agent_loop →
    stream_llm` (4 layers of an external repo). Instead the admiral stays *fully*
    stateless: when a captain parks, `_emit_escalation` streams the question plus
    an **HTML-comment sentinel** (`<!--ADMIRAL-ESCALATION:<b64 json>-->`) carrying
    `{track_id, repo, objective, base_ref}`. The comment is invisible in the
    rendered chat but round-trips verbatim in the message history Odysseus replays
    to the model. On the operator's next turn, `_pending_escalation` parses the
    sentinel off the **most-recent** assistant message (so a resolved escalation
    won't re-trigger and chained escalations resume the newest), and `_route`
    dispatches to `_resume_stream`, which folds the operator's answer into the
    objective, re-runs the captain, and runs the same post-exec gate + accept path.
  - **Verified live end-to-end:** confirmed via source + an empirical drive-through
    that Odysseus stores assistant content verbatim (`_extract_thinking_meta` only
    strips `<think>`, not HTML comments) and replays full assistant history
    (`get_context_messages` + `_sanitize_llm_messages` preserve `content`). Logged
    the admiral's received payload on a real `/api/chat_stream` turn: it got
    `[system, user, assistant, user, assistant, user]` with the sentinel intact,
    routed to resume, ran the captain, and streamed `**done**` back through the SSE.

**Slice 5 — parallel dispatch + reply-routing (DONE, verified live with real goose):**
the intake is a task LIST, so on `go` the admiral freezes + spawns every task,
authority-gates each, then runs all cleared captains **concurrently** (one daemon
thread each) instead of one-at-a-time.
  - **Reply-routing:** completions stream back **labeled by task title** as each
    captain finishes (`_settle` builds the per-task block + records the slice's
    terminal state). The single SSE stays alive with a global heartbeat while any
    captain runs.
  - **Escalation across parallel tasks:** escalations are gathered and presented as
    one **numbered** Gate-B list. The operator targets one with a leading `N:`;
    `_resume_stream` resumes that captain and **re-emits the others' sentinels
    (carry-forward)** so they survive the stateless turn. One pending → the answer
    resumes it directly; ambiguous (multiple, no number) → asks + carries all
    forward. Each sentinel now carries `{track_id, repo, objective, base_ref,
    label, question}` so a carried-forward captain re-shows its question.
  - **Verified:** unit + deterministic e2e (parallel dispatch with 2/4 escalating,
    numbered, targeted resume `1:`, carry-forward, ambiguity path); parallelism
    proven (3×5s captains → 8s wall vs 15s serial); and **live with real goose** —
    a 2-task dispatch ran captains in scratch-demo + scratch-demo2 concurrently
    (128s wall), each created its file on its own `auto_runtime` track, both slices
    accepted.

**Out of scope here (separate build surfaces, HUB_ARCHITECTURE §7 — not admiral
slices):** omni-mem two-layer rework (raw + checkpoints), the tool/MCP registry
(`allowed_tools` stays empty until it lands), and the monitoring surface. These
are their own epics crossing other repos; they need their own direction, not a
bundle here.

## Known shortcuts (Slice 1)

- Gate-A clarifications are **deterministic gap detection** (repo-not-found,
  no-branch-specified), not LLM product-judgment questions. Real judgment
  clarifications are a later layer.
- `resolved_clarifications` stores the raw answer under one key; per-question
  Q↔A mapping is later.
