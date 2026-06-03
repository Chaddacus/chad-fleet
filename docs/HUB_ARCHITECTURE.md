# Hub Architecture — Admiral / Captain / Fleet

**Status:** design frozen for the execution loop; build surfaces open.
**Last updated:** 2026-05-26
**Owner:** Chad Simon (solo dev; this system is the force multiplier)

This document is the canonical design record for the orchestration hub. It captures
the vision, the tier model, the end-to-end pipeline, every decision made during
frontloading (with rationale), what already exists vs what must be built, and the
data contracts between tiers. It is referenceable by future sessions.

---

## 1. The goal

A single hub Chad goes to and says *"here's everything I need done."* He hands over a
list of tasks, each bound to a repo / local code location and a project. The system
front-loads understanding, asks him the questions it genuinely cannot answer itself,
then executes autonomously across all tasks in parallel. Chad monitors one surface
(the admiral) for updates and provides guidance only on the irreducible decisions.

The design intent is **leverage for a solo dev**: answer questions once up front,
then walk away; the system absorbs all the noise and surfaces only signal.

---

## 2. Tier model

Three LLM tiers sitting on top of a non-LLM truth layer.

| Tier | Role | Scope | Lifecycle |
| --- | --- | --- | --- |
| **Admiral** | Global brain / hub. Runs discovery, freezes dossiers, spawns captains, triages escalations, routes to user. | Global (all tasks) | **Reactive**: active during discovery/clarify/spawn and on each escalation; idle during steady-state execution. |
| **Captain** | Per-task coordinator. Born from a frozen dossier. Executes its task like one of these Claude sessions — acts, figures things out, asks when genuinely blocked. | One task = one repo | Lives for the duration of its task; runs an `auto_runtime` slice DAG. |
| **Fleet** | Workers/subagents under a captain. No cross-talk; everything routed through the captain. | Slices within the captain's repo | Ephemeral per slice. |

**Truth layer (non-LLM):** the AgentOps `ContractKernel` (`~/automation_architecture`).
It owns legal states, captaincy leases, scopes, budgets, security allows, and the
authority tiers (`T0_ALLOW`, `T1_ADMIRAL_OVERRIDE`, human). The LLM tiers **propose**;
the kernel **validates**. This is what prevents the admiral-brain and captain-brains
from becoming competing sources of truth. Integrations (omni-mem, DevRelay, Sentinel,
Kickstarter, MCP Gateway) are adapters/projections around the contract, never
competing truth.

---

## 3. The pipeline

```
1. INTAKE        Chad → hub: "here's everything I need done"
                 each item = { task, repo, local path, project, omni-mem thread id }
                      │
2. DISCOVERY     Admiral runs research subagents; RLM = cheap touchpoint,
                 refresh only if stale (HEAD drift); build full picture of
                 task + current repo/code state
                      │
   ── GATE A ──  CLARIFY: admiral collects questions it cannot answer itself
                 + coding-principle checks → BATCHED to Chad (whole list) →
                 Chad answers once → frontload complete
                      │
3. DISPATCH      Admiral freezes one CaptainDossier per task and spawns one
                 captain per task, pre-loaded with that context
                      │
4. EXECUTE       Captain runs its auto_runtime slice DAG; issues fleet workers;
                 fleet → captain (structured evidence); no fleet cross-talk
                      │
   ── GATE B ──  ESCALATE: captain asks when genuinely blocked →
                 EscalationPacket up to admiral → admiral answers or routes to
                 Chad → reply flows back DOWN to the same captain (correlation id)
                      │
5. MONITOR       Chad watches the admiral for updates / provides guidance
6. MEMORY        omni-mem captures every layer raw + generates checkpoints
                 throughout (cross-cutting, not a final stage)
```

The whole loop is **frontload-heavy, escalation-filtered**. Chad's leverage lives in
two gates: **A** (answer unknowns once, up front) and **B** (admiral absorbs noise,
only irreducible decisions reach Chad). Everything between the gates runs without him.

---

## 4. Decisions made (with rationale)

**D1 — Goose is the entry point / conductor, replacing openclaw's dev-supervisor.**
Goose generalizes the exact pattern openclaw proved (ACP → worker on isolated
worktree): provider-agnostic workers, recipes as portable flows, a maintained
orchestrator extension instead of a cold custom dispatcher. openclaw dev-supervisor
retires; omni-mem stays.

**D2 — Admiral runs discovery, then creates the captain with that context.**
Discovery is an admiral-tier capability, not a captain lifecycle phase. This makes
the admiral a *brain* (resolves the earlier admiral-as-brain vs admiral-as-router
fork in favor of brain) and means captains never re-discover.

**D3 — The CaptainDossier is the admiral→captain contract boundary, and it is FROZEN.**
Once handed over, the dossier never changes. Consequences:
- The admiral can forget a task's discovery after spawning (keeps only a thin index +
  escalation queue) — this is what makes the admiral's context survivable.
- Cross-task learning moves to *escalation-time*, not document-time. No concurrent
  writers, no leaking boundary.
- Perfect provenance: "what did this captain know at birth?" always has a clean,
  immutable answer tied to an omni-mem id.

**D4 — Captains behave like these Claude sessions: act, figure things out, ask when
genuinely blocked.** The captain's runtime discoveries are NOT pushed back into the
frozen dossier; they live in the captain's omni-mem thread and surface via escalation
or the closure packet. The ask-threshold is the tuning knob: act on what's
discoverable, ask only on genuine direction/authority ambiguity. Every question is a
frontload miss.

**D5 — Steering is PULL-based via escalation, not push.** This resolved the steering
fork. The admiral has no independent information source about a running captain's
repo-locked world, so it never pushes amendments. Instead the captain pulls: hits
ambiguity → asks → answer flows back down. The dossier stays frozen; Q&A is a separate
request/reply stream the captain initiates.

**D6 — The escalation channel is the ONLY inter-tier comms path, bidirectional
request/reply with a correlation id.** Captain→admiral→(user)→back down to the exact
captain/slice that asked.

**D7 — Park-and-continue across independent slices.** When a slice blocks on a
question, that slice + its transitive dependents are parked; the captain keeps working
the rest of the frontier. Answer returns (correlation id) → slice unparks → dependents
re-enter the frontier. Degrades gracefully to hard-block when no independent work
remains. This requires a slice DAG — which `auto_runtime` already provides, so the
**captain IS an auto_runtime track** (reuse, not rebuild). Runtime parked/active/done
state lives in the captain's omni-mem thread, not the frozen dossier.

**D8 — Whole-list frontloading.** Admiral discovers across the entire list → batches
every clarification → Chad answers once → admiral spawns all captains. Matches the
"walk away / force multiplier" posture. (Falls out naturally from D2.)

**D9 — Admiral triage is the noise filter.** On escalation the admiral wakes, pulls
the task's frozen dossier + raw thread from omni-mem by id, and answers from
dossier / coding principles / cross-task knowledge / omni-mem if it can; routes to
Chad only when it can't. Maps to the AgentOps authority tiers. Captains never reach
Chad directly.

---

## 5. Data contracts between tiers

### CaptainDossier (admiral → captain, frozen)
```
CaptainDossier {
  omni_mem_thread_id      // drill-down handle to all raw discovery
  task_brief              // what + acceptance criteria
  repo_path + rlm_ref     // current code picture (fresh or refreshed)
  resolved_clarifications // Chad's Gate-A answers, baked in
  coding_principles_ref   // guidelines this captain must honor
  allowed_tools + mcps    // per-project allow-list (tool tier)
  fleet_plan (optional)   // initial slice decomposition, or captain derives it
}
```
Maps onto the AgentOps `captaincy_lease` (ownedScope, allowedMutationRoots,
allowedTools already exist). The dossier = the lease + the discovered context.

### EscalationPacket (captain → admiral → user; reply travels back down)
```
EscalationPacket {
  correlation_id          // routes the reply back to the exact captain/slice
  omni_mem_id             // drill-down to full context
  summary                 // self-contained; admiral triages WITHOUT re-reading thread
  problem                 // the specific blocker / question
  context                 // what's needed to decide
  parked_slice_set        // what's waiting on this answer
}
```
Sibling to the existing Evidence and Closure packet schemas in
`fleet-orchestration-doctrine.md`.

---

## 6. Existing vs to-build inventory

| Component | State | Location |
| --- | --- | --- |
| Captain tier | **built** | `~/code/chad-fleet/apps/chad-captain` (launchd-scheduled, apps_registry, merge_facilitator, validator, protocol) |
| Fleet runner | **built** | `chad_captain/goose_runner.py` — `goose run --no-session` in sandboxed XDG runtime |
| Slice DAG / frontier | **built** | `~/.claude/bin/auto_runtime.py` + `~/.claude/state/autonomy/<track>/` |
| Truth layer / contract | **built** | `~/automation_architecture` (AgentOps ContractKernel: leases, scopes, authority tiers, integration_manifest) |
| Intake artifact | **mostly built** | AgentOps `examples/objective-brief.json`; needs multi-task list shape + project/omni-mem binding |
| Tracer-bullet recipes | **built (scratch)** | `~/.config/goose/recipes/{worker-claude,worker-codex,fleet-orchestrator}.yaml` |
| RLM scan cache | **partial** | in-tree `.artifacts/rlm-scan/`; staleness-gated discovery is new |
| **omni-mem two-layer rework** | **TO BUILD** | raw capture + generated checkpoints — the foundation |
| **Tool + MCP registries** | **schema only** | global list + per-project pulls + per-agent allow-lists + collision-safe naming |
| **Admiral tier** | **TO BUILD** | reactive brain: discovery + dossier freeze + spawn + escalation triage |
| **Hub / monitoring surface** | **TO BUILD** | projection of omni-mem checkpoints + escalation queue; `chad-dashboard` is the seed |

---

## 7. Open build surfaces (priority order)

1. **omni-mem two-layer rework** — raw append-only capture (prompt, every back-and-forth,
   each layer's I/O; never summarized/lost) + an LLM-generated **checkpoint layer**
   (human-facing rollups at chosen boundaries: slice complete, escalation, N turns).
   Chad reads checkpoints; drills to raw by id. This is the only place an LLM belongs
   in the memory path — summarization is judgment; capture/staleness/routing/leases are
   deterministic. **Foundation: nothing above it (checkpoints, escalation packets,
   dossier-by-id, monitoring) is trustworthy until raw-capture and checkpoint-generation
   are cleanly separated.** Directly expresses Chad's "noise before signal" principle.
2. **Tool + MCP registries** — the frontloading that makes captains effective. Global
   registry with collision-safe unique names (no "web fetch / document fetch"
   ambiguity), per-project pull lists, per-agent allow-lists wired to the contract's
   `allowedTools`. Integrations: omni-mem, DevRelay, Kickstarter, Sentinel, etc.
3. **Admiral tier** — the reactive brain (discovery orchestration, dossier freeze,
   captain spawn, escalation triage + routing).
4. **Hub / monitoring surface** — what Chad actually goes to.

---

## 8. Principles this design must honor (from Chad's standing rules)

- **Noise before signal** — clean noise sources before adding panels/alerts; the
  checkpoint layer exists to be the filter.
- **Canonical-state observability** — every state the system claims to observe must be
  grounded in real data, never assumed. Applies hard to the checkpoint layer.
- **Anti-overengineering** — reuse existing primitives (auto_runtime DAG, AgentOps
  leases, chad-captain) before building new surfaces. The captain is an auto_runtime
  track; the dossier is a lease + context; do not reinvent.
- **Deterministic where possible** — staleness checks, routing, lease grants, registry
  uniqueness are code, not LLM judgment.
