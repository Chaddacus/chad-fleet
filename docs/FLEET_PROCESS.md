# Fleet Process — End-to-End Spec (v9)

> **Status:** v9 — Chad's 3 follow-up answers locked in. (1) Linode = host `linode` / `23.92.20.39` / user `root` (from Chad's ~/.ssh/config). (2) Twilio creds: deferred — SMS code ships in PR5 but stays disabled until Chad drops creds. (3) Zoom: ONE account (chad-agent's existing company creds — `chad.simon@cloudwarriors.ai`); v8's personal-vs-company isolation section was wrong and is removed. Twin DMs go directly to Chad's user JID via the existing chad-agent Zoom client.

## Engine prep work (R3 found 5 HIGH engine bugs the spec assumed away)

Before any Twin daemon code can ship, the chad-captain engine needs these changes (PR5 in the captain assembly stack):

| Engine fix | File(s) | Why | LOC |
|------------|---------|-----|-----|
| Registry file locking (R3#1) | `apps/chad-captain/src/chad_captain/apps_registry.py` | `load_registry`/`save_registry` use bare `read_text`/`write_text` today. Two writers can lose updates; reader during writer can torn-read. Add `fcntl.flock` + `os.replace` + shared/exclusive helpers. | ~80 |
| `RegisteredApp.enabled` field (R3#2) | `apps_registry.py` + `daemon.py` + `cli.py` | Spec relies on staging captains as `enabled=false` after partial scaffold; field doesn't exist. Pydantic silently drops unknown `enabled=false` so a staged captain ticks anyway. Add `enabled: bool = True`; daemon and `install-plists` filter disabled. | ~60 |
| `task_id` on CurrentSlice + SliceComplete (R3#4) | `protocol.py` + `replanner.py` + `validator.py::build_current_slice` | Spec adds `task_id` to backlog/roadmap/log but skipped CurrentSlice. Without it, `CaptainLogEntry.task_id` can't be propagated from the dispatched slice. | ~60 |
| Dynamic extras discovery with safe import semantics (R3#8 + R2#2) | `extras/__init__.py` | New `get_extras` does `importlib.import_module` + `importlib.invalidate_caches()` after scaffold install. ONLY swallows `ModuleNotFoundError` when `e.name == f"chad_captain.extras.{slug}"`; everything else escalates. | ~60 |
| Synthetic canary captain support (R3#5) | `cli.py` (new `chad-captain canary` subcommand) | Twin's engine-repair canary needs a no-push/no-merge dispatch mode that runs one tick against a deliberately-empty repo, not "the lowest-stakes paused captain." | ~150 |
| JSONL append/tail safety (R3#6) | `protocol.py` (append helpers) + new tail helper in `apps/chad-twin-daemon/jsonl_tail.py` | Use `os.open(O_APPEND)` + single `os.write` per encoded line; tailer buffers until newline so partial-line reads don't checkpoint past unterminated bytes. Diff snippets > 4KB go in referenced files, not log JSON. | ~150 |
| Replan rate limit + sanity helpers (v5 §6) | `replanner.py` + `protocol.py` + `cli.py` | `_slice_shape_signature` for drained-replan sanity; per-captain replan-count tracking (5/hour cap); `chad-captain backlog reprioritize` subcommand; verify_cmd-required validation in `apps_registry.py` | ~120 |
| Twilio SMS sender (v5 §10) | `apps/chad-twin-daemon/sms.py` (NEW) | Twilio REST POST to send SMS; rate limiter (3/hour, 8/day) with collapse-to-digest fallback; FAIL-CLOSED to Zoom DM on Twilio outage | ~80 |
| Cross-task artifact schema validation (v5 §validation) | `apps/chad-captain-scaffold/artifacts.py` | Schema register + validate at put/get; Pydantic JSON Schema emit | ~100 |
| Verify-cmd enforcement OUTSIDE custom validator (v6 §validation L2) | `validator.py::_resolve_validate_fn` | Wrap custom validator so verify_cmd always runs first and short-circuits on failure; custom hook receives verify-passed result only | ~60 |
| `RegisteredApp.verify_host` for SSH/remote verify (v6 §validation close) | `apps_registry.py` + new `run_verify` helper | New VerifyHost Pydantic model; ssh execution with timeout, stdout/stderr capture, exit propagation | ~120 |
| Trigger queue + scope-change mid-flight handling (v6 §6.1, §6.3.1) | `replanner.py` + `protocol.py` + `daemon.py` | `pending_replan_reasons.json` queue with priority order; `send_goose_abort_signal` (SIGTERM via tracked PID); `superseded_by_scope_change` slice status | ~100 |
| Backlog generation lock (v6 §6.3) | `protocol.py` (FeatureBacklog model) | Monotonic generation int + flock; replanner reads-then-checks before write | ~40 |
| `slice_complete.removed_tests_reason` field + diff-deletion check (v6 test gate) | `protocol.py` + `validator.py` | New SliceComplete field; validator inspects diff for test-file/test-function deletions; PR body trailer copy | ~80 |
| `twin_holds/` directory + close handler integration (v6 §validation L4) | `protocol.py` (paths) + close handler | New AppWorkspace.twin_holds_dir; close blocks if any unexpired hold | ~30 |
| Producer-pending state for split_task (v6 §6.4) | `validator.py` (roadmap_complete flow) | Check task manifest produces against artifact bus before opening PR | ~50 |
| Cost projection for new captains (v7 auth-boundary #3) | `apps/chad-twin-daemon/cost_projection.py` (NEW) | Estimate captain LLM spend from research output (token estimates × backlog item count); gate scaffold install at $20/day | ~80 |
| Captain spin-up rate limit + concurrency ceiling (v7) | `apps/chad-twin-daemon/captain_throttle.py` (NEW) + state file | 5 new/24h + 12 concurrent caps; queue overflow; `twin captain unthrottle` admin override | ~120 |
| Twin Zoom DM client (v9 — reuses chad-agent's existing S2S, no separate creds) | `apps/chad-twin-daemon/zoom_dm.py` (NEW) | Wraps chad-agent's existing zoom_client.py; resolves CHAD_DM_TARGET to 1:1 channel ID once at startup; caches channel ID; users/me identity check | ~50 |
| Approved-classes registry deprecated (v7 first-of-class removed) | DELETE: `~/.chad/captain/approved_classes.json` references | Per-captain approval gate gone; profile registry takes its place | -40 |
| Hosting topology: WorkerBinding + log_sink_url + heartbeat (v8 R5#1) | `apps_registry.py` + `goose_runner.py` + new `apps/chad-twin-daemon/event_sink.py` | WorkerBinding model on RegisteredApp; HTTPS POST event sink on Twin; goose-runner streams events to sink instead of local file write; heartbeat poller + 5min watchdog | ~290 |
| Activity cursor + delta DMs (v8 R5#2) | `apps/chad-twin-daemon/aggregate.py` + new `activity_cursor.py` | Persisted cursor; "Changed since last viewed" header; delta DM emission on task transitions; `--since` drill flag | ~150 |
| Resource access manifest + capability registry (v8 R5#3) | `apps/chad-twin-daemon/research.py` (manifest field) + new `apps/chad-captain/src/chad_captain/capabilities.py` + scaffold integration | ResourceAccess model; capability registry with flock; default-deny credential provisioner; scaffold reject when manifest entry not approved | ~260 |
| Zoom startup identity check (v9 — slimmer than v8's isolation since one account) | `apps/chad-twin-daemon/zoom_dm.py` + `main.py` startup self-check | users/me account_id match + DM channel resolution at startup; degraded mode (no outbound Zoom) if mismatch | ~30 |
| Cost projection + reconciliation (v8 R5#5) | `apps/chad-twin-daemon/cost_projection.py` (already counted) + new pricing table + reconciler | p50/p95 estimator with confidence; daily reconciliation against captain_log token usage; 2-day overrun auto-pause | ~120 (delta over earlier ~80) |
| Captain task state machine + queue UX (v8 R5#6) | `apps/chad-twin-daemon/captain_throttle.py` (already counted) + new state.json schema | 12 explicit states; per-task state.json; queue position + earliest_start; `--captain-queue` drill | ~60 (delta over earlier ~120) |

**Total engine prep:** ~2,460 LOC + tests (v9: -80 LOC from simplified Zoom client; one account instead of personal-vs-company isolation). Ships as **PR5 (engine prep) BEFORE PR6+ (twin daemon).**
> **Goal:** Chad pushes a task; the fleet returns a finished task. Chad is the LAST stop, not stops 3, 5, 7, and 9.

---

## Vision (in Chad's words)

> "the fleet is a tool that is able to run any task that i throw at it. its
> like me having 5 development teams. - the captain should be aligned for
> the specific task. we should be doing the research / frontloading,
> building the captain and the fleet we need for that task"

> "end state is you being able to take my tasks and run with them, being
> the admiral for me and overseeing the captains"

## Roles + canonical names (R1#21 fix)

| Role | Canonical name | Identity | Owns |
|------|----------------|----------|------|
| **human_owner** | Chad | Human | Direction, sign-off on authority-boundary actions, vetoes |
| **fleet_operator** | Twin | chad-twin daemon + Claude calls | Intake → research → scaffold → register → oversee captains → repair fleet → escalate to Chad ONLY on authority-boundary or unresolved ambiguity |
| **captain** | per-app chad-captain process | Plan slices → dispatch goose → validate → integrate to main |
| **fleet_workers** | goose-runner workers | Execute slices, edit code, commit |
| **comms_surface** | chad-agent | Zoom DMs, calendar, external messaging — only when Twin or Chad triggers |

**Term reservations:**
- `admiral_notes/` is the existing captain → human protocol artifact. NOT used for Twin↔captain or Twin↔Chad messaging.
- Twin↔captain messaging uses captain's existing `admiral_notes/` (Twin writes notes to itself as the operator). Twin↔Chad uses chad-agent Zoom DMs.

**Hard rules:**
- Twin does NOT mutate task code. Twin scaffolds captains; captains run the work.
- Twin DOES mutate fleet infrastructure (chad-captain engine, scaffold templates, twin daemon itself) under emergency repair when a captain is stuck (R1#9 fix).
- Twin auto-registers captains and approves roadmaps. Chad sign-off only on authority-boundary actions (R1#1 fix).

---

## Authority boundary (definitive list — v7 LOCKED)

Twin escalates to Chad ONLY for:

1. **Production deploys** to user-facing surfaces (Spark publish to KDP, chadacys.com push, customer-running services)
2. **External communications** (any non-Chad recipient: customers, prospects, public posts, federal RFP responses)
3. **Money** (any payment, subscription change, contractor invoice, AWS spend > $50/event, projected captain LLM cost > $20/day)
4. **Destructive ops** that can't be reverted by `git revert` (DB drops, force-push to main, secret rotation)
5. **New scaffold profile needed** — when no existing scaffold profile fits the task's repo_shape (e.g. first encounter with `polyglot` shape, or a brand-new repo type). This is NOT first-of-class captain — it's first-of-INFRASTRUCTURE. (v7 change per Chad: per-captain approval removed; per-profile approval kept because it's actually building new tooling.)
6. **Genuine direction ambiguity** (research + classifier confidence both < 0.7)
7. **Final task completion sign-off** (bundled, not per-PR — R1#17 fix)

Everything else: Twin acts on its own. PR review for non-authority-boundary work is BUNDLED into final task sign-off, not a per-PR ping.

## Hosting topology (v8 R5#1 + v9 concrete Linode info)

The fleet has TWO hosts and ONE log plane:

| Host | SSH | Role | What lives here |
|------|-----|------|-----------------|
| **Linode VPS** | `ssh linode` (`23.92.20.39`, user `root`, key `~/.ssh/id_ed25519`) | Twin daemon + canonical log plane | Twin daemon process; ALL captain workspaces (`~/.chad/fleet/apps/<app_id>/`); ALL captain_log.jsonl; admiral_notes/; chad_action_queue.json; task artifacts; apps_registry.json; approved profiles registry; `~/.chad/fleet/.env` |
| **Chad's MacBook** | local | goose-runner workers ONLY | Captain repos (where goose actually edits code); goose subprocesses; per-repo verify_cmd execution. NO captain state. |

**Event flow:**
1. Linode Twin determines a slice should dispatch.
2. Linode writes `current_slice.json` to the canonical workspace (Linode disk).
3. Twin sends a dispatch RPC to MacBook's goose-runner over SSH or HTTPS (mTLS via Tailscale).
4. MacBook goose-runner clones/pulls the repo, runs goose, captures output.
5. MacBook streams events back to Linode in real time: each captain_log entry is a single SSH/HTTPS POST that Linode appends to the canonical `captain_log.jsonl` (uses the JSONL append safety helper from PR5).
6. MacBook writes `slice_complete.json` back to Linode via the same channel.
7. Twin reads the canonical log and reviews.

**Canonical log writes ALWAYS happen on Linode.** MacBook never holds authoritative state. If MacBook is offline, dispatched slices queue on Linode (`pending_dispatch_queue`); they execute when MacBook reconnects.

**Heartbeat + log_sink_url contract:**

Every `RegisteredApp` carries:
```python
class WorkerBinding(BaseModel):
    worker_id: str           # e.g. "chad-macbook"
    worker_host: str         # SSH hostname or HTTPS URL via Tailscale
    log_sink_url: str        # Where worker POSTs events; always Linode-side
    last_heartbeat_at: datetime | None  # Updated every 60s by worker
```

**FAIL-CLOSED:**
- Captain cannot activate (`enabled=true`) unless `log_sink_url` is reachable from BOTH the captain's worker AND Twin.
- Worker missing heartbeat for > 5min → captain auto-pauses; Twin surfaces in next AGGREGATE delta DM.
- Log sink unreachable from worker → worker spools events to local `~/.chad/fleet/.pending-events/` and replays on reconnect; never drops.

**Engine prep (PR5) additions:**
- `WorkerBinding` model on `RegisteredApp` (~30 LOC)
- HTTPS event sink endpoint on Twin daemon (~120 LOC) — receives JSONL POSTs, validates worker auth token, appends atomically
- Goose-runner refactor: events stream to log_sink_url instead of local file write (~80 LOC change to existing goose_runner.py)
- Heartbeat poller in worker + Twin watchdog (~60 LOC)

Total hosting-topology engine prep: ~290 LOC (added to PR5 budget below).

### Captain spin-up policy (v7 — parallel, no per-captain approval)

Per Chad's directive: captains spin up in parallel as tasks arrive. NO approval gate per captain. Twin auto-scaffolds, auto-registers, captain manages the task, captain elevates updates to Twin (admiral), Twin only escalates to Chad on the 7 items above.

Safety rails on parallel captain spin-up (5 layers — v8 added resource manifest per R5#3):

- **Profile gate**: if scaffold profile doesn't fit (auth-boundary #5), Twin builds the profile first and asks Chad once. Future captains using that profile spin up freely.
- **Cost gate**: research projects expected captain LLM cost. > $20/day projected → auth-boundary #3 ping. Below threshold → spin up freely. (See cost projection schema in §Step 3.)
- **Resource access gate (R5#3 fix)**: research output MUST include a `resource_access_manifest`. Captain default-deny for shared-resource WRITE/ADMIN access; only the resources explicitly approved in the manifest get credentials provisioned. Detail below.
- **Rate limit**: max 5 NEW captains spun up per 24h window. Hit the cap → queue with explicit state (see below).
- **Resource ceiling**: max 12 concurrent active captains. Hit the cap → queue, do NOT spin up.

#### Captain task states (R5#6 fix — explicit queue UX)

Every task carries a `state` field at `~/.chad/fleet/tasks/<task_id>/state.json`:

| State | Meaning |
|-------|---------|
| `intake_received` | Inbox watcher saw the file |
| `classifying` | Step 2 in progress |
| `researching` | Step 3 in progress |
| `awaiting_chad` | Step 4 clarification queued |
| `scaffold_pending` | Cleared all gates, waiting for next available scaffold lock |
| `queued_rate_limit` | Hit 5/24h cap; will spin up at `earliest_start_at` (rolling window) |
| `queued_concurrency` | Hit 12-concurrent ceiling; will spin up when an active captain completes |
| `queued_capability` | Pending Chad's auth-boundary approval on resource_access_manifest |
| `active` | Captain registered, dispatching slices |
| `paused` | Captain paused (cost overrun, missing heartbeat, twin_hold, etc.) |
| `completed` | All checks in §"validated for close" passed |
| `failed` | Unrecoverable; manifest in `~/.chad/fleet/tasks/<task_id>/failure_reason.txt` |

When a task transitions into `queued_*`, Twin emits a SMALL delta DM (NOT full aggregate):
```
TASK QUEUED t9-quux state=queued_rate_limit
position=1, earliest_start=06:45 (window resets)
override: twin captain unthrottle t9-quux
```

`twin status --captain-queue` (NEW drill, separate from `--queue` which is the chad action queue) shows the captain throttle queue with positions + earliest-start times.

#### Resource access manifest (R5#3 fix — closes the shared-infra hole)

A captain that fits an existing profile, costs <$20/day, and stays under caps can STILL be dangerous if it writes to a shared database, modifies a secret store, changes GitHub repo settings, or mutates remote infra without ever triggering a deploy/destructive boundary. v8 fix: research must enumerate every shared resource the captain plans to touch + access mode.

```python
class ResourceAccess(BaseModel):
    resource_id: str          # e.g. "postgres://prod-db", "github:Chaddacus/chadacys.com",
                              #      "1password:vault-shared", "ssh:noob-root", "saas:zoom-api"
    kind: Literal["repo", "db", "secret_store", "cloud_account", "ssh_host", "saas_api"]
    mode: Literal["read", "write", "admin", "destructive"]
    rationale: str            # WHY captain needs this access
```

Research output (`research.json`) adds:
```json
{
  "resource_access_manifest": [
    {"resource_id": "github:Chaddacus/es-bots", "kind": "repo", "mode": "write", "rationale": "captain commits + opens PRs on its own repo"},
    {"resource_id": "ssh:noob-root", "kind": "ssh_host", "mode": "read", "rationale": "verify_cmd runs `systemctl status` on existing services; no mutation"}
  ]
}
```

**Default-deny scaffold rules:**
- Captain runtime gets credentials ONLY for resources in its manifest at the manifest's mode.
- `mode=write` to a non-captain-owned repo → auth-boundary #1 (production deploy) OR #4 (destructive) depending on resource. Twin asks Chad to approve the manifest entry.
- `mode=admin` or `mode=destructive` to ANY resource → always auth-boundary, always asks.
- `mode=write` to a shared DB or secret_store → always auth-boundary #4.
- `mode=read` to anything → no ping (but still recorded in manifest for audit).

**Captain-owned resources** (no auth-boundary needed):
- The captain's own repo (clone path matches `RegisteredApp.repo_path`)
- The captain's workspace dir (`~/.chad/fleet/apps/<app_id>/`)
- The captain's task artifact bus dir (`~/.chad/fleet/tasks/<task_id>/`)

**Approved capabilities registry** at `~/.chad/captain/approved_capabilities.json`:
```json
{
  "approved": [
    {
      "resource_id": "ssh:noob-root",
      "modes_allowed": ["read"],
      "approved_for_app_ids": ["t4-es-bots"],
      "approved_at": "...",
      "expires_at": null
    }
  ]
}
```

If a future captain requests an already-approved resource+mode for an already-approved app_id → no ping. Different app_id requesting same resource+mode → fresh ping (per-captain authorization, even when shared).

**Engine prep additions (PR5):**
- `ResourceAccess` Pydantic model + `research.json` schema field (~40 LOC)
- Capability registry load/save with flock (~60 LOC, mirrors apps_registry pattern)
- Captain runtime credential provisioner (reads manifest + capability registry, sets up env/secret allowlist for goose subprocess) (~120 LOC)
- Rejection logic in scaffold transaction (manifest entry not approved → block install, ping Chad) (~40 LOC)

### task_class definition (R2#5 + R3#7 fix — structured Pydantic enum, NOT freeform string)

`task_class` is a 4-tuple Pydantic model with literal-string fields, NOT a slash-joined string. Slash form exists ONLY for human-readable display.

```python
class TaskClass(BaseModel):
    schema_version: int = 1
    domain_tag: Literal[
        "manuscript-publishing", "marketing-content", "fleet-infrastructure",
        "federal-rfp", "internal-tooling", "data-pipeline", "integration",
    ]
    authority_profile: Literal[
        "local-only", "local-with-shared-infra", "prod-deploy-later",
        "prod-deploy-immediate", "regulated",
    ]
    repo_shape: Literal[
        "python-pkg", "django-app", "ts-monorepo", "ts-app",
        "static-site", "polyglot", "greenfield",
    ]
    external_surface_level: Literal[
        "no-external", "internal-only", "public-read", "public-write",
    ]
    risk_rank: int  # derived: 1 (local-only/no-external) → 5 (regulated/public-write)

    def display(self) -> str:
        return f"{self.domain_tag}/{self.authority_profile}/{self.repo_shape}/{self.external_surface_level}"

    def canonical_key(self) -> str:
        return f"v{self.schema_version}|{self.domain_tag}|{self.authority_profile}|{self.repo_shape}|{self.external_surface_level}"
```

**Classifier emits structured TaskClass, not strings.** Unknown enum value → `clarify` outcome (or `profile_needed` if it's a repo_shape mismatch). Classifier CANNOT invent new domain_tag values; if the task doesn't fit, it asks Chad.

**No approval gate on TaskClass itself (v8 — R5#7 cleanup).** TaskClass is used ONLY for routing, risk_rank computation, and resource policy hints. Per-captain approval was removed in v7; the auth-boundary gates are: (1) profile-needed when `repo_shape` has no scaffold profile, (2) cost > $20/day, (3) resource_access_manifest entries needing approval. There is NO `approved_classes.json` — that file is removed from PR5 engine prep.

| Component | Meaning |
|-----------|---------|
| `domain_tag` | What the work IS |
| `authority_profile` | What permissions the captain needs |
| `repo_shape` | What the target repo looks like |
| `external_surface_level` | Who sees the output |

Examples:
- `manuscript-publishing/local-only/python-pkg/no-external` (Spark)
- `marketing-content/prod-deploy-later/django-app/public-read` (T3 chadacys marketing)
- `fleet-infrastructure/local-with-shared-infra/python-pkg/no-external` (T4 ES bots — DOES touch noob-root but read-only via cw-gateway)
- `federal-rfp/regulated/polyglot/public-write` (future) — `polyglot` triggers profile-needed (auth-boundary #5); regulated + money/deploy actions trigger #1/#3 at action time

---

## The 11 Steps

```
Chad ──task──▶ INTAKE ──▶ CLASSIFY ──▶ RESEARCH ──▶ CLARIFY (rare)
                                                          │
                                                          ▼
                                                       SCAFFOLD (Twin auto-registers)
                                                          │
                                                          ▼
                                              (existing) PLAN ──▶ DISPATCH ──▶ FLEET
                                                          ▲                       │
                                                          └──── (loop) ───────────┘
                                                          │
                                                          ▼
                                                       REVIEW (Twin = fleet_operator)
                                                          │
                                                          ▼
                                                       AGGREGATE
                                                          │
                                                          ▼
                              ┌─── ESCALATE (auth-boundary only) ──┴── CLOSE ───┐
                              │                                                 │
                              ▼                                                 ▼
                            Chad                                              omni-mem
```

---

### Step 1 — INTAKE

**Goal:** Twin sees every task Chad pushes, in one canonical place.

**Inbox surface:** `~/.chad/fleet/inbox/` directory.
- Chad drops `.md` files manually.
- chad-agent writes `.md` files when Chad chats a task (Zoom DM with `#task` prefix, voice note transcribed).
- Both ingress paths converge to the same directory.

**Watcher implementation (R1#2 fix):**
- `watchfiles` library (cross-platform, uses FSEvents on macOS, inotify on Linux).
- 5-minute reconciliation polling scan as a backstop for missed events.
- Idempotent task IDs from filename SHA prefix; duplicate filenames are dedup'd.

**Required intake schema** (frontmatter):
```markdown
---
task_id: 2026-05-04-spark-launch-prep   # auto-generated from filename if omitted (R1#13 fix)
priority: high | medium | low            # default medium (auto-repair)
deadline: 2026-06-01                     # optional ISO date
related_captains: [spark-of-defiance]    # optional human hint
---

# Task title

Free-form body. Twin parses for: goal, constraints, deadlines,
existing-system references, success criteria.
```

**Twin daemon behavior:**
- Watcher fires on new file
- Auto-repair missing optional frontmatter (task_id from filename, priority=medium, generate deadline=null)
- Move file to `inbox/processing/<task_id>.md`
- Write `intake_received` event to twin journal (omni-mem journal_write)
- Trigger Step 2 (CLASSIFY) inline

**FAIL-CLOSED (refined per R1#13 + R2#8):**
- Auto-repairable issues (missing optional fields) → fix silently, log to twin journal.
- Unrecoverable (missing title, malformed frontmatter that can't be parsed at all) → quarantine to `inbox/quarantine/<task_id>.md` + write to twin journal.
- Quarantine count is reported in EVERY AGGREGATE header (even when 0).
- Quarantine escalates to "Needs your action" inline when count > 5 OR oldest > 24h OR any quarantined file has priority=high. Surface as a single bundled action, not per-file pings.
- `twin quarantine repair` auto-fixes recoverable malformed frontmatter and re-runs intake on the file.

**Reuse vs new:**
- REUSE: omni-mem journal_write, chad-agent Zoom-to-md hook (NEW chad-agent capability, ~30 LOC)
- NEW: ~80 LOC inbox watcher (`apps/chad-twin-daemon/intake.py`)

---

### Step 2 — CLASSIFY

**Goal:** Twin decides routing AND can split a task across captains (R1#8 fix).

**Decision tree:**
```
Read task → search omni-mem (memories + facts + journal) for related work →
Compute candidate set: existing app_ids + their domains.
For each candidate:
  - Score keyword/tag match (0.0-1.0)
  - Score domain fit from related memories
  - Score deadline + dependency feasibility
Aggregate scores → decision:
  - route_existing      → 1 captain owns task in full
  - propose_existing    → 1 captain owns task but new backlog item needs "go"
  - split_task          → multiple captains; emit subtasks with blocked_by
  - scaffold_new        → no captain fits; create one
  - clarify             → top-2 candidates within 0.1 confidence; ask Chad
```

**Classifier output** (R1#8 + R2#3 fix — supports DAG with explicit artifact contracts):
```yaml
task_id: <id>
classification:
  decision: route_existing | propose_existing | split_task | scaffold_new | clarify
  confidence: 0.0-1.0
  rationale: "..."
  alternatives: [{decision: ..., target: ..., score: ...}]
  task_class: <domain_tag>/<authority_profile>/<repo_shape>/<external_surface_level>
  # If single captain:
  target_captain: <app_id>
  # If split_task:
  subtasks:
    - subtask_id: <id>-a
      target_captain: <app_id_a>
      blocked_by: []
      produces: ["fixture:marketing_posts_001.json", "schema:Post.v1"]
      consumes: []
    - subtask_id: <id>-b
      target_captain: <app_id_b>
      blocked_by: [<id>-a]
      produces: ["render:posts/<slug>.html"]
      consumes: ["fixture:marketing_posts_001.json"]
    - subtask_id: <id>-integration   # ONLY when code-level stitching needed
      target_captain: twin-integration   # special "captain" Twin runs itself
      blocked_by: [<id>-a, <id>-b]
      produces: ["task_completion_proof"]
      consumes: ["render:posts/<slug>.html"]
  integration_owner: twin
```

**Artifact contracts:** see Step 5 SCAFFOLD for the artifact bus implementation.

**Classifier engine:** Claude haiku via cw-gateway. Single prompt, structured output (JSON Schema constrained). No tool use, deterministic-ish.

**Memory primitives used (R1#11 fix):**
- omni-mem `search` over memories + facts + journal (NOT `fact_query` — that's not a primitive).
- `search` returns relevance-scored matches; threshold at 0.6 for "related."

**FAIL-CLOSED:** Confidence < 0.7 → emit `clarify` decision; trigger Step 4. Don't auto-scaffold or auto-route on a coin flip.

**Reuse vs new:**
- REUSE: omni-mem search, apps_registry.json load, Claude haiku via cw-gateway
- NEW: ~200 LOC classifier (`apps/chad-twin-daemon/classifier.py`) including DAG output validation

---

### Step 3 — RESEARCH (mandatory before scaffold; R1#7 fix)

**Goal:** Twin frontloads everything a captain needs BEFORE scaffolding. No captain ships with "TODO: figure out X" — that was T3's discovery problem.

**Research is MANDATORY before any `scaffold_new` decision.** Sequencing rule (R1#7): S5 SCAFFOLD cannot run without a complete `research.json` artifact. Skipping research is not allowed.

**Research checklist (per task):**

1. **Codebase scan**
   - `rg --files` + `rg <task_keywords>` across `~/code/`
   - For each hit repo: `git log -1`, `git remote -v`, dir size, language stats
2. **Deployed-surface discovery** (if task touches user-facing surface)
   - DNS + TLS cert chain
   - SSH probe to noob-root for systemd units (timeout 10s; FAIL → record open_question)
   - PaaS detection via `gh secret list`
3. **Web research** (if task is brand/product/market-facing)
   - WebSearch for competitive landscape; cap 3 queries; summarize ≤200 words
4. **Memory search**
   - omni-mem `search` for prior decisions, blockers, patterns
   - omni-mem `search` over journal entries (R1#12 fix — NOT journal_read; use search filtered by topic)
5. **Repo conventions** (for chosen target repo)
   - Read CLAUDE.md, AGENTS.md, README, top-level test config
   - Identify verify_cmd (`make check`, `npm test`, `uv run pytest`, `cargo test`)
6. **Stakeholder/dependency map**
   - What other captain repos does this touch?
   - Any blocking external deps (other captains, manual steps, secrets)?

**Research output schema** (`~/.chad/fleet/inbox/processing/<task_id>.research.json`):
```json
{
  "task_id": "...",
  "researched_at": "...",
  "deployed_surface": {"domain": "...", "repo": "...", "deploy": "...", "confidence": 0.0-1.0},
  "candidate_repos": [{"path": "...", "fit_score": 0.0-1.0, "rationale": "..."}],
  "verify_cmd": "...",
  "related_memories": [{"id": "...", "title": "...", "excerpt": "..."}],
  "dependencies": [{"type": "captain|manual|external", "ref": "...", "blocks": true}],
  "resource_access_manifest": [/* see Captain spin-up policy section */],
  "cost_projection": {
    "model_mix": {"haiku": 0.7, "opus": 0.3},
    "estimated_slices": 24,
    "estimated_tokens_per_slice": {"in": 8000, "out": 2000},
    "retry_factor": 1.4,
    "estimated_days_active": 30,
    "p50_cost_per_day_usd": 4.20,
    "p95_cost_per_day_usd": 11.50,
    "confidence": 0.7
  },
  "open_questions": ["..."],
  "minimal_research_complete": true
}
```

**Cost projection methodology (R5#5 fix):**
- Token estimates per slice come from a simple historical model: read last 50 captain dispatches from the captain_log plane, average tokens per slice by model. New captain bootstraps with conservative defaults: haiku 8K in / 2K out, opus 12K in / 4K out.
- Pricing table at `apps/chad-twin-daemon/llm_pricing.json` (versioned; updated when Anthropic pricing changes).
- `p95_cost_per_day_usd` = `(p95_tokens_per_slice * pricing) * retry_factor * (estimated_slices / estimated_days_active)`.
- Gate fires on **p95**, not p50. Conservative bias.
- If `confidence < 0.7` AND p95 within 30% of $20/day → auth-boundary ping (uncertainty pushes us toward asking).
- **Reconciliation**: every 24h Twin reads actual token usage from captain_log entries, recomputes daily run-rate. Actual rate > $20/day for 2 consecutive days → captain auto-pauses + auth-boundary ping (cost overrun).

**Minimal research** (mandatory): items 1, 4, 5 always. Items 2, 3, 6 conditional on task type but recorded as `null + reason` if skipped.

**FAIL-CLOSED:** Required minimal items unanswerable → record open_question; trigger Step 4. Never proceed to SCAFFOLD with empty `verify_cmd` or empty `candidate_repos`.

**Reuse vs new:**
- REUSE: rg, gh CLI, ssh, omni-mem search, WebSearch, T3 discovery runbook as a checklist template
- NEW: ~350 LOC research orchestrator (`apps/chad-twin-daemon/research.py`) — items run in parallel where safe (1, 2, 3, 4 are independent)

---

### Step 4 — CLARIFY (rare path, idempotent — R1#14 fix)

**Goal:** Twin asks Chad ONLY when research can't resolve genuine ambiguity. ONE message, ALL questions bundled.

**Trigger conditions:**
- Classifier `decision=clarify` (confidence < 0.7 OR top-2 within 0.1)
- Research found ≥1 unresolvable open_question
- Task body explicitly says "ask me before doing X"
- Authority-boundary action needed mid-flight

**Clarification record** (R1#14 + R2#6 fix — global queue with deps):

Single store at `~/.chad/fleet/chad_action_queue.json` (NOT per-question files — one queue Twin reasons about as a whole):

```json
{
  "questions": [
    {
      "question_id": "<task_id>-q1",
      "task_id": "...",
      "asked_at": "...",
      "expires_at": "...",
      "priority": "high|medium|low",
      "outbound_message_id": "<zoom message id>",
      "questions": ["..."],
      "depends_on_questions": [],   // other question_ids whose answers gate this one
      "blocks_tasks": ["<task_id>", ...],
      "task_lock_owner": "twin@<host>:<pid>",
      "reply_state": "pending|received|expired",
      "reply_text": null,
      "reply_received_at": null,
      "answers_parsed": null
    }
  ]
}
```

**Idempotence:**
- Same task already has open clarification → DO NOT send another. Append to existing record's question list. Re-DM only if list changed AND last DM > 1h ago.
- Re-ping after `expires_at`: bundles ALL expired high-priority questions into ONE digest DM, never per-task.

**Cycle detection (R2#6 fix):** Before adding a new question, Twin checks if `depends_on_questions` forms a cycle through the queue. Cycle detected → emit ONE "break-the-tie" question to Chad listing the cyclic deps and asking which to resolve first. Never let two tasks deadlock on each other.

**Reply matching:** Zoom message reply-thread OR explicit `Re: <question_id>` header. Twin polls outbound_message_id replies via chad-agent every 60s.

**Format (single Zoom DM via chad-agent):**
```
Task: <title> (<task_id>) — clarification needed (id <question_id>)

Before I scaffold a captain, I need:

1. <question 1, with options A/B/C if applicable>
2. <question 2>

Research summary: <2-3 sentence what-we-know>
Recommendation: <Twin's best guess + confidence>

Reply with answers or "go with your recommendation" to proceed.
```

**FAIL-CLOSED:** Chad doesn't reply by `expires_at` → task stays in `awaiting_chad/`. Twin does NOT proceed on assumed answers. Surfaces in AGGREGATE.

**Reuse vs new:**
- REUSE: chad-agent Zoom DM tool, chad-agent message-id tracking
- NEW: ~120 LOC clarification dispatcher + reply parser

---

### Step 5 — SCAFFOLD (Twin auto-registers in parallel; v7 — first-of-class gate REMOVED)

**Goal:** Generate a working captain from templates via a profile system. **Twin auto-registers without Chad approval for EVERY captain** unless one of the auth-boundary triggers fires (new scaffold profile needed, projected cost > $20/day, or running into the 5/24h or 12-concurrent caps).

#### 5.1 — Scaffold profiles (R2#1 fix — escape hatch)

A scaffold profile is the contract between research output and a generated captain. The default profile uses Jinja2 templates; new profiles drop in for shapes the default can't express.

**Profile contract** (`apps/chad-captain-scaffold/profiles/<profile_id>/profile.py`):

```python
class ScaffoldProfile:
    profile_id: str                    # "default-python-pkg", "django-app", "ts-app", "static-site"
    supported_repo_shapes: list[str]   # repo_shape vocab from task_class
    required_research_fields: list[str]  # research.json keys this profile needs

    def file_manifest(self, ctx: dict) -> list[FileSpec]:
        """Return list of files to render (path, template, render_vars)."""

    def validator_factory(self, ctx: dict) -> ValidatorSpec | None:
        """Return validator module spec (or None for default chain)."""

    def extras_factory(self, ctx: dict) -> list[ExtraDimSpec]:
        """Return per-app dimension specs."""

    def workspace_strategy(self, ctx: dict) -> WorkspaceSpec:
        """How to init the captain workspace."""

    def verify_strategy(self, ctx: dict) -> str:
        """The verify_cmd to bake into RegisteredApp."""

    def rollback_hooks(self, ctx: dict) -> list[Callable]:
        """Cleanup actions if scaffold fails mid-transaction."""
```

**Profile selection:**
- Twin matches `task_class.repo_shape` to a profile's `supported_repo_shapes`.
- No matching profile → emit `profile_needed` outcome (NOT a broken captain). Twin journals + surfaces in next AGGREGATE. Chad gets a profile-needed ping (auth-boundary #5) including the research output and the missing shape.
- First use of an existing profile = NO ping (v7 — captain spin-up is parallel; only NEW profiles need approval).

**Initial profile catalog** (ship with v1):
- `default-python-pkg` — generic Python package, pytest verify
- `django-app` — Django app with manage.py check + makemigrations check verify, fixture FK validator option
- `ts-app` — TypeScript Node, npm test + tsc verify
- `static-site` — HTML/CSS/JS, htmlproofer + link-check verify

Anything else → first use is `profile_needed`, Chad approves the new profile (and Twin builds it before scaffolding the captain).

#### 5.2 — Dynamic extras discovery (R2#2 fix — kill the AST patch)

**Engine change required (S5f → renamed S5g):** modify `chad_captain.extras.__init__.py::get_extras` to dynamically import `chad_captain.extras.<app_id>` (with `app_id` slug-normalized) and call its `factory()` function. Fall back to `EXTRAS_FACTORIES` dict for legacy apps (spark, author-toolkit, captain-self).

```python
def get_extras(app_id: str) -> list[ExtraDimension]:
    # Legacy explicit registry first (back-compat)
    factory = EXTRAS_FACTORIES.get(app_id)
    if factory:
        return factory()
    # Dynamic discovery: chad_captain.extras.<slug>.factory()
    slug = app_id.replace("-", "_")
    try:
        mod = importlib.import_module(f"chad_captain.extras.{slug}")
    except ImportError:
        return []
    fn = getattr(mod, "factory", None)
    return fn() if callable(fn) else []
```

Scaffold writes `apps/chad-captain/src/chad_captain/extras/<slug>.py` exposing `factory()`. NO AST patching. NO mutation of `EXTRAS_FACTORIES`. New file = new captain extras, full stop.

Same pattern for validators: scaffold writes `apps/chad-captain/src/chad_captain/validators/<slug>.py`; `RegisteredApp.validator_module` already supports dotted-path import.

#### 5.3 — Task-scoped artifact bus (R2#3 fix — multi-captain handoff)

For `split_task` outcomes, Twin owns the artifact bus at `~/.chad/fleet/tasks/<task_id>/`:

```
~/.chad/fleet/tasks/<task_id>/
├── manifest.json              # subtask DAG + produces/consumes
├── artifacts/
│   ├── <subtask_id>/
│   │   ├── <artifact_name>    # files captains write
│   │   └── manifest.json      # what this subtask produced
└── lock                       # flock for cross-subtask coord
```

Captains write artifacts via a small CLI (`chad-captain artifact put --task <id> --subtask <sid> --name <n> --path <p>`).

**Atomic put semantics (R3#10 fix):**
1. Open `~/.chad/fleet/tasks/<task_id>/lock` exclusively (flock)
2. Copy source file → `tasks/<task_id>/artifacts/<subtask_id>/.tmp.<artifact_name>` (always inside the bus to avoid EXDEV cross-device rename)
3. `os.fsync()` the temp file
4. `os.replace()` temp → final artifact path (atomic within same filesystem)
5. Update subtask manifest.json via tempfile + replace
6. Release lock

Never `os.rename` from the captain's repo path into the bus — they may be on different filesystems. Always copy, fsync, replace.

Dependent subtasks read via `chad-captain artifact get --task <id> --consumes <name>` which:
- Validates upstream subtask is in `status=shipped`
- Validates the artifact name is in upstream's `produces`
- Returns the bus path (read-only mount)

When code-level stitching is needed (e.g. captain A's fixture needs to land IN captain B's repo), Twin spawns a `twin-integration` pseudo-captain that:
- Has no goose-runner
- Twin executes the integration step itself (copy artifact, open PR)
- Counts toward task completion the same as a real captain

This is the ONE place Twin mutates task code — explicit, single-step, never via goose.

#### 5.4 — Generated artifacts (per profile)

1. **Registry entry** — atomic write to `~/.chad/captain/apps_registry.json` under flock (R1#3). Validated through `RegisteredApp.model_validate()` BEFORE write. Initial state `enabled=false` (R2#9 staging); flipped to `true` only after all phases succeed.
2. **Workspace** at `~/.chad/fleet/apps/<app_id>/` with backlog seed
3. **Custom validator** at `apps/chad-captain/src/chad_captain/validators/<slug>.py` (only if profile says so)
4. **Extras** at `apps/chad-captain/src/chad_captain/extras/<slug>.py` exposing `factory()`
5. **Backlog seed** at `apps/chad-captain/seeds/<slug>-backlog.json` with task_id on every item (R1#10)
6. **Bootstrap runbook** (greenfield only)
7. **launchd plists** (tick + goose-runner) via `chad-captain install-plists`

#### 5.5 — Scaffold transaction (R2#9 fix — explicit phases + compensation manifest)

Every scaffold run writes a transaction manifest first; rollback reads the manifest and removes ONLY files it created.

```
TRANSACTION SCAFFOLD <app_id> <txn_id>:

phase 0: PRE
  - Acquire flock at ~/.chad/fleet/.scaffold.lock (exclusive, 60s timeout)
  - Acquire flock at ~/.chad/captain/.engine.lock (shared with daemon — Twin is a writer)
  - Validate research.json complete; profile selected; cost projection ≤ $20/day OR auth-boundary ping completed; rate-limit + concurrency gates not exceeded
  - Write manifest at ~/.chad/fleet/scaffolds/<txn_id>/manifest.json:
      {
        "txn_id": "...",
        "app_id": "...",
        "profile_id": "...",
        "phase": "PRE",
        "files_to_create": [...],
        "files_to_modify": [],
        "registry_entry_app_id": "...",
        "started_at": "..."
      }

phase 1: STAGE
  - Render all profile files into ~/.chad/fleet/scaffolds/<txn_id>/staging/
  - Mirror the live src/ tree layout
  - Update manifest.phase = "STAGE"

phase 2: VERIFY (against staging via PYTHONPATH overlay — R3#3 fix)
  - py_compile every generated .py file (catch syntax errors before any import)
  - Build PYTHONPATH overlay: tmpdir with the staging files copied at the
    same relative paths as live src/. Twin runs:
      env PYTHONPATH=<staging_overlay>:<chad-fleet-root>/apps/chad-captain/src \
          uv run --project /Users/chadsimon/code/chad-fleet \
          python -m pytest apps/chad-captain/tests -q
    The overlay is FIRST in PYTHONPATH so generated modules (extras/<slug>.py,
    validators/<slug>.py) shadow any live versions during the test run; uv
    workspace deps still resolve from the real repo.
  - Acceptance test (S5g) MUST prove: with the overlay, `import
    chad_captain.extras.<slug>` resolves to the staging file, NOT live src.
  - On failure → mark manifest.phase = "FAILED_VERIFY", surface in AGGREGATE, exit
  - Update manifest.phase = "VERIFIED"

phase 3: INSTALL (atomic, in dependency order — R4 fix: explicit create vs replace + preimage backup)

  Manifest carries TWO file lists:
    files_to_create: paths that MUST NOT exist before install (errors if present)
    files_to_replace: paths that DO exist; each entry has preimage_sha256 +
                      preimage_backup_path (~/.chad/fleet/scaffolds/<txn_id>/preimages/<path>)

  Before install:
    - Refuse install if any files_to_create path already exists (suggests
      stale prior scaffold; admin must clean up scaffolds/failed/ first)
    - For every path in files_to_replace: copy current contents to
      preimage_backup_path (do NOT use git HEAD — worktree may be dirty
      with pending Twin daemon edits)

  Install loop:
    - For each file in files_to_create:
        - Copy staging → live path via tempfile in same dir + os.replace
        - Append to manifest.installed_files
    - For each file in files_to_replace:
        - Copy staging → live path via tempfile in same dir + os.replace
        - Append to manifest.replaced_files (already preimaged)

  Init workspace dir; write backlog seed (always create-only)
  Update manifest.phase = "INSTALLED"

  ROLLBACK (any phase ≥ 3 failure):
    - For each path in installed_files: delete (back to non-existent)
    - For each path in replaced_files: copy preimage_backup_path → live path
      via tempfile + os.replace (restores EXACT prior contents, ignores git HEAD)
    - Verify by sha256 match against preimage_sha256
    - On preimage restore failure → escalate IMMEDIATELY to Chad (this is
      the worst-case scenario; live src is in unknown state)

phase 4: REGISTER
  - Write registry entry with enabled=false (atomic + flock)
  - Append to manifest.registry_entry_written
  - Update manifest.phase = "REGISTERED"

phase 5: ACTIVATE
  - Run: chad-captain replan --app <app_id> --trigger initial
  - Sanity-check roadmap (slice count, no empty prompts)
  - On success → flip registry.enabled=true + auto_replan=true (atomic)
  - On failure → leave registry.enabled=false; surface in AGGREGATE
  - Install plists; bootstrap launchctl
  - Update manifest.phase = "ACTIVE"

phase 6: COMMIT
  - Move ~/.chad/fleet/scaffolds/<txn_id>/ → ~/.chad/fleet/scaffolds/done/<txn_id>/
  - Release locks

ROLLBACK (any phase failure):
  - Read manifest
  - For each file in installed_files: delete (back to git HEAD if file existed)
  - For registry_entry_written: remove entry from registry
  - Move scaffold dir → ~/.chad/fleet/scaffolds/failed/<txn_id>/
  - Release locks
  - Surface failure in AGGREGATE with manifest path for inspection
```

#### 5.6 — Captain verifier (R1#5 fix)

`apps/chad-captain` has no Makefile. Real verifier: `uv run python -m pytest apps/chad-captain/tests -q` from repo root.

#### 5.7 — Scaffold output to Chad (auth-boundary only — profile-needed OR cost > $20/day)

Captain spin-up is silent in the default path. Chad sees scaffolds in the next on-demand AGGREGATE roll-up (status command).

Scaffold ping fires ONLY for these auth-boundary cases:

**Case A — new scaffold profile needed:**
```
PROFILE NEEDED for task <task_id>
Class: <task_class.display()>
Repo shape: <repo_shape> (no existing profile fits)

Research summary: <2-3 sentence what-we-know>
Twin recommendation: build <profile_id> profile (~<N> LOC, ~<H> hours);
  OR rescope task to <existing_profile> with concrete tradeoff: <...>
Reply "build profile" / "rescope to <profile_id>" / specifics
```

**Case B — projected cost > $20/day:**
```
COST APPROVAL for new captain <app_id>
Class: <task_class.display()>
Projected LLM spend: ~$<N>/day (<token estimate breakdown>)
Backlog scope: <N> items × ~<M> tokens/slice average

Twin recommendation: approve / scope down to <subset> / defer
Reply "approve" / "scope down" / "defer"
```

Default path (profile fits + cost OK + within rate/concurrency gates) writes scaffold record to twin journal; surfaces in next `twin status` aggregate.

#### 5.8 — FAIL-CLOSED summary

- Profile not found → `profile_needed` outcome, Chad ping, no install
- Research incomplete → blocked at Step 3, never reaches Step 5
- Phase verify fails → no install, no registry write
- Phase activate fails → registry stays `enabled=false`, captain not ticking
- Any phase exception → ROLLBACK reads manifest, removes only what it created
- Two simultaneous scaffolds → flock serializes, second waits 60s then errors

#### 5.9 — Slices (R1#20 fix — split into 6 sub-slices, ~1.5k LOC total)

| Slice | What | Path | LOC |
|-------|------|------|-----|
| S5a | Profile contract + 4 default profiles + Jinja2 renderer | `apps/chad-captain-scaffold/profiles/` + `core.py` | ~400 |
| S5b | Transaction manifest + phase orchestration + rollback | `apps/chad-captain-scaffold/transaction.py` | ~350 |
| S5c | Workspace + backlog + branch setup | `apps/chad-captain-scaffold/workspace.py` | ~200 |
| S5d | Plist install + bootstrap variants (greenfield vs existing) | `apps/chad-captain-scaffold/bootstrap.py` | ~200 |
| S5e | Artifact bus CLI (put/get/manifest) | `apps/chad-captain-scaffold/artifacts.py` | ~250 |
| S5f | Engine: dynamic extras discovery + task_id field additions | `apps/chad-captain/src/chad_captain/extras/__init__.py` + `protocol.py` | ~150 |
| S5g | Acceptance tests (end-to-end scaffold one captain per profile in tmpdir) | `apps/chad-captain-scaffold/tests/` | ~500 |

**Anti-overengineering check:** No captain DSL. Profiles are concrete Python classes. The artifact bus is two CLI verbs (`put`, `get`) over a directory. The transaction is 6 named phases with a JSON manifest, not a workflow engine.

---

### Step 6 — PLAN / REPLAN (Twin auto-approves; v5 expanded — full lifecycle, sanity criteria, backlog ownership)

**Goal:** Captain reads backlog, generates roadmap. Twin owns the planning lifecycle end-to-end without bouncing to Chad except on persistent failure.

#### 6.1 — Replan trigger taxonomy

The captain's `replan_if_needed` runs in 6 distinct contexts. **Triggers can collide on the same tick** (e.g. drained AND kill_replan, or scope_change arriving while drained is queued). v6 fix: triggers coalesce into a per-captain queue, processed under the engine lock with explicit priority.

**Trigger priority (highest first):**
```
scope_change > kill_replan > publish > low_yield_streak > drained > initial
```

**Coalescing rule:** when multiple triggers fire within a 60s window OR while a replan is already in progress, they collapse into `~/.chad/fleet/apps/<app_id>/pending_replan_reasons.json` (a list of {trigger, reason, queued_at}). Captain processes ONE replan per lock acquisition, applying the highest-priority queued trigger and discarding lower-priority duplicates of the same trigger.

**`drained` suppression:** ignored if any of (a) current_slice exists and is in flight, (b) pending higher-priority trigger queued, (c) replan rate-limit hit (§6.5).

Each trigger has its own sanity criteria and Twin response:

| Trigger | Source | Sanity criteria | Twin failure handling |
|---------|--------|-----------------|------------------------|
| `initial` | After SCAFFOLD install | (A) slice count ≤ backlog item count × 2; (B) every slice has non-empty system_prompt + user_prompt; (C) every slice cites at least one backlog item by id; (D) total slice count ≥ 1 | Re-replan with hint up to 2x; if still failing, leave registry.enabled=false + escalate to Chad (auth-boundary fallback when scaffold can't produce a sane initial roadmap) |
| `drained` | Engine, when current_slice empty AND auto_replan=True | (A) slice count > 0 (else `roadmap_complete` flow fires instead); (B) all sanity (A)-(D) above; (C) NEW: backlog has been re-prioritized vs last roadmap (no infinite-loop replans of the same shape) | Re-replan once with "produce a different shape" hint; if still same-shape, mark roadmap_complete + close task |
| `kill_replan` | Validator verdict on goose timeout / structural slice failure | All sanity (A)-(D); plus retry_context from validator threaded into next slice's user_prompt | Re-replan once with the killed slice's failure as input; if still failing, escalate |
| `low_yield_streak` | Engine circuit breaker on N soft-accept verdicts | (A)-(D); plus rubric_delta_pp distribution shows new dimensions being moved (not just the saturated ones) | Re-replan ONCE with "rubric saturated; expand backlog" hint; if rubric still saturated → escalate to Chad with explicit "extend rubric or close task" question |
| `publish` | Manual via `chad-captain replan --trigger publish` (e.g. T1 Spark publish prep) | App-defined; replanner's prompt switches to publish-mode template | Same as initial; admiral controls so escalation ping is implicit |
| `scope_change` | Twin, after Chad's clarification answer changes task scope | (A)-(D); plus diff against previous roadmap shows ≥1 changed slice | Re-replan with hint = the new scope; if no slices change, log warning (Chad's answer didn't materially affect plan) |

#### 6.2 — Sanity criteria (decoded)

```python
def replan_sanity(roadmap, backlog, prev_roadmap=None) -> SanityResult:
    if not roadmap.slices:
        return Fail("empty roadmap")
    if len(roadmap.slices) > len(backlog.items) * 2:
        return Fail(f"roadmap explodes backlog ({len(roadmap.slices)} slices for {len(backlog.items)} items)")
    for s in roadmap.slices:
        if not s.system_prompt.strip() or not s.user_prompt.strip():
            return Fail(f"slice {s.slice_id} has empty prompt")
        if not s.references.get("backlog_item_id"):
            return Fail(f"slice {s.slice_id} doesn't cite a backlog item")
    if prev_roadmap is not None:
        # drained-replan: must materially differ from previous
        if _slice_shape_signature(roadmap) == _slice_shape_signature(prev_roadmap):
            return Fail("drained replan produced identical shape — backlog stuck")
    return Ok()
```

`_slice_shape_signature` = sorted tuple of (slice_id, references.backlog_item_id) — catches "captain replanning the same 3 slices over and over."

#### 6.3 — Backlog editing (who owns what)

**Backlog generation lock (R4 fix):** every `feature_backlog.json` carries a monotonic `generation` integer. Replanner reads `generation` at start; if it changes during the replan, replanner discards its work, enqueues a `backlog_changed` trigger, and exits. Backlog mutations bump `generation` under exclusive flock on the same lockfile.

| Action | Owner | Triggers replan? |
|--------|-------|------------------|
| Initial backlog seed | SCAFFOLD (from research-derived items) | Yes — `initial` trigger |
| Mid-flight item priority change | Twin (during REVIEW step), via `chad-captain backlog reprioritize` | No — affects NEXT replan; bumps `generation` |
| Mid-flight item add | Twin only when research surfaces a new dependency; via `chad-captain backlog add --task-id <id>` | No — added items wait for next `drained` trigger; bumps `generation` |
| Mid-flight item remove | Twin only when an item becomes unreachable (e.g. external dep deleted); writes admiral_note explaining why | No — but pruned current_slice if active; bumps `generation` |
| Mid-flight item shipped | Captain marks via merge of slice's PR (existing engine behavior) | No; bumps `generation` |
| Scope change from Chad clarification | Twin updates backlog from clarification answer; THEN triggers `scope_change` replan | YES — explicit |
| Roadmap manual edit | NEVER. Roadmap is regenerated, not edited. | n/a |

**Hard rule:** Twin NEVER hand-edits the roadmap. The roadmap is the captain's artifact, regenerated by replanner. Twin can edit the BACKLOG (which feeds replanner) and trigger replan.

#### 6.3.1 — Scope change during mid-flight slice (R4 fix)

When a `scope_change` trigger fires while a slice is dispatched:

```python
def handle_scope_change(captain, new_scope_diff):
    active_slice = read_current_slice(captain.workspace)
    if active_slice is None:
        update_backlog(new_scope_diff); enqueue_trigger("scope_change"); return

    # Does the scope change touch a backlog item the active slice references?
    affected = scope_diff_touches(new_scope_diff, active_slice.references.backlog_item_id)

    if affected:
        # Pause captain, signal goose-runner to abort current dispatch
        pause_captain(captain.app_id, reason="scope_change_supersedes_active_slice")
        send_goose_abort_signal(active_slice.slice_id)  # SIGTERM to goose subprocess
        # Mark slice superseded so validator doesn't process its eventual SliceComplete
        write_slice_status(active_slice, "superseded_by_scope_change")
        update_backlog(new_scope_diff)
        enqueue_trigger("scope_change")
        unpause_captain(captain.app_id)
    else:
        # Unrelated scope change: defer
        update_backlog(new_scope_diff)
        enqueue_trigger("scope_change")  # processes after active slice validates
```

Engine prep additions (PR5): `pause_captain`/`unpause_captain` already exist; need `send_goose_abort_signal` (SIGTERM to running goose subprocess via PID tracked in slice state) and `superseded_by_scope_change` status handling in validator (it just discards the eventual SliceComplete instead of running validation chain).

#### 6.4 — Cross-captain replan (split_task DAG)

When task X = subtasks A + B (B blocked_by A):
- Captain A finishes its backlog. Twin sees A's `roadmap_complete` event.
- Twin checks: does any subtask have `blocked_by: [A]`? Yes → B.
- Twin runs `chad-captain artifact get --task <task_id> --consumes <name>` for each B `consumes` entry; verifies all artifacts present.
- Twin triggers B's `initial` replan with the artifact paths injected into the user_prompt context.
- B's captain replans accordingly; Twin reviews per (6.1) sanity.

If A produces an artifact B needs but A's roadmap_complete is reached without that artifact → Twin emits `artifact_missing` escalation. Twin first attempts to re-replan A with a hint to produce the missing artifact (one retry); if still missing, escalate to Chad.

**Drained-close suppression (R4 fix):** when a captain participates in a split_task as a producer, the engine's `roadmap_complete` flow checks the manifest at `~/.chad/fleet/tasks/<task_id>/manifest.json` for that captain's `produces` declarations. Any declared artifact missing from the bus → roadmap_complete is BLOCKED (no PR open, no close); captain stays in `producer_pending` state until the artifact is in the bus or Twin re-replans with the missing-artifact hint. This prevents "drained-close while consumer is still waiting" race.

#### 6.5 — Replan failure backstop (anti-infinite-loop)

Per-captain replan rate limit: max 5 replans in any 1-hour window. Hitting the limit:
- Pause captain
- Surface in AGGREGATE as "replan thrashing"
- Twin escalates to Chad with the last 5 replan attempts + their sanity failures
- Resume only on Chad's intervention OR after 1-hour cooldown (whichever first)

This catches the case where the captain's replanner prompt is broken and produces invalid roadmaps faster than Twin can reject them.

#### 6.6 — S6/S7 setup dependencies (R1#15 fix retained)

Step 5 (specifically S5c) is the explicit owner of: workspace init, `feature_backlog.json` write, `captain_branch` setup, plist install. Step 6 only handles replan-and-inspect.

#### 6.7 — Engine support needed (PR5 engine prep additions)

| What | File | LOC |
|------|------|-----|
| `chad-captain backlog reprioritize` subcommand | `cli.py` | ~40 |
| `chad-captain backlog add` subcommand (already exists; verify task_id field support) | `cli.py` | ~20 (audit) |
| Replan rate-limit tracking in workspace state | `protocol.py` (new field on AppWorkspace) | ~30 |
| `_slice_shape_signature` helper for drained-replan sanity | `replanner.py` | ~30 |

Add to PR5 engine prep total: ~120 LOC + tests.

---

### Step 7 — DISPATCH (existing — captain → goose-runner)

**Goal:** Captain dispatches slices to goose, goose edits code, commits.

**No new code.** Existing autonomous loop.

**Twin's role:** Monitors `captain_log.jsonl` for each captain (Step 8).

---

### Step 8 — REVIEW (Twin as fleet_operator; R1#9, #19 fixes)

**Goal:** Twin reviews captain decisions. Accept silently when good. Repair fleet infrastructure when needed. Escalate ONLY on authority boundary.

**Trigger semantics (R1#19 — resolved contradiction):**

Two event tiers:
- **Immediate-handle events** (Twin reacts within 60s):
  - `escalation_raised` — captain explicitly asking for help
  - `pr_conflict` — captain PR can't merge (rebase needed)
  - `circuit_breaker_tripped` — captain paused itself
  - `low_yield_streak` — rubric saturation
  - any captain log entry referencing authority-boundary action
- **Batch-review events** (Twin reads every 15min):
  - `validate` (accept/reject_retry/reject_hard verdicts)
  - `dispatch`
  - `roadmap_drained`
  - `replan` triggers

**Twin's review rubric:**
| Captain verdict | Twin action |
|-----------------|-------------|
| accept (delta ≥ 0) | silent, journal entry |
| soft_accept | log to journal, no action |
| reject_retry | silent (captain handles automatically) |
| reject_hard | inspect diff; either: (a) re-replan with hint, (b) update backlog, (c) escalate to Chad if repeated 2x with same root cause |
| escalate | inspect immediately. Resolve if Twin can (config tweak, restart goose, fleet infrastructure repair). Escalate to Chad ONLY if (a) authority-boundary, or (b) Twin attempted repair and failed |
| kill_replan | check captain pause state; unpause after diagnosis if safe |

**Twin emergency repair (R1#9 + R2#4 fix — file-path allowlist instead of "additive/non-breaking"):**

- If a captain is stuck due to a chad-captain ENGINE bug, Twin opens a PR against `chad-fleet/main` with the fix.
- Captain stays paused during engine repair; resumes only after PR merges + canary verifies.

**Auto-merge ALLOWLIST** (Twin can self-merge without Chad ping when ALL conditions met):
- Diff touches ONLY paths in this allowlist:
  - `apps/chad-captain/tests/**` (test additions/fixes)
  - `apps/chad-captain/runbooks/**` (docs)
  - `apps/chad-captain/seeds/**` (backlog seeds)
  - `apps/chad-captain/src/chad_captain/extras/<slug>.py` (per-app extras only — not the registry)
  - `apps/chad-captain/src/chad_captain/validators/<slug>.py` (per-app validators only)
  - `apps/chad-captain-scaffold/profiles/<new_profile>/` (new profile, not existing)
  - `apps/chad-twin-daemon/**` (Twin's own code)
- AND diff does NOT touch any path in the BLOCKLIST below
- AND diff passes `uv run python -m pytest apps/chad-captain/tests -q` AND `apps/chad-twin-daemon/tests/`
- AND a single-captain canary runs cleanly for 1 tick (Twin picks the lowest-stakes paused captain, unpauses it on the patched engine, watches for one full tick before unpausing the rest)

**BLOCKLIST** (always requires Chad ping, no exceptions):
- `apps/chad-captain/src/chad_captain/protocol.py` (data contracts every captain depends on)
- `apps/chad-captain/src/chad_captain/validator.py` (the default validation chain)
- `apps/chad-captain/src/chad_captain/replanner.py` (planning prompts + roadmap shape)
- `apps/chad-captain/src/chad_captain/scorecard.py` (rubric scorer)
- `apps/chad-captain/src/chad_captain/daemon.py` (autonomous loop)
- `apps/chad-captain/src/chad_captain/goose_runner.py` (worker invocation)
- `apps/chad-captain/src/chad_captain/apps_registry.py` (registry loader)
- `apps/chad-captain/src/chad_captain/extras/__init__.py` (extras dispatcher)
- `apps/chad-captain/src/chad_captain/cli.py` (admin surface)
- `apps/chad-captain/pyproject.toml` (dependency surface)
- Anything outside `apps/chad-captain/`, `apps/chad-twin-daemon/`, `apps/chad-captain-scaffold/`

**Canary model (R3#5 fix — synthetic captain, not "lowest-stakes paused captain"):**

Twin maintains a permanent **synthetic canary captain** at `~/.chad/captain/canary/`:
- `app_id = "_canary"`, `mode = autonomous`, `auto_replan = false`
- Repo is a throwaway local-only git repo with a single dummy slice
- `auto_push = false`, `auto_open_pr = false`, `auto_merge = false` (never touches origin)
- Pre-loaded with one trivial slice ("write 'canary alive' to canary.txt")

The canary is invoked via a new CLI: `chad-captain canary --one-tick --no-push --no-merge --dispatch-limit=1` (added in PR5 engine prep, R3#5). On engine repair PRs:
1. Twin merges the engine PR
2. Runs `chad-captain canary --one-tick` against the new code
3. If exit=0 AND canary.txt has "canary alive" AND no escalation in canary's captain_log → unpause real captains in priority order
4. If canary fails → Twin auto-reverts the engine PR via `git revert`, surfaces failure in AGGREGATE, real captains stay paused

**Rollback plan required:** Every Twin-authored engine PR includes a `Rollback:` line in the body with the exact `git revert <sha>` command. Twin watches real captains for 1 hour post-unpause; if any captain emits `escalation_raised` referencing engine paths in that window, Twin auto-reverts.

**False-negative cost protection:** allowlist + canary + 1-hour watch is the three-layer defense. The synthetic canary is independent of which real captains are healthy at the time, so the canary works even if all real captains are paused by the engine bug.

**Twin's review tools:**
- `tail -f captain_log.jsonl` (watchfiles on captain_log paths)
- Read `slice_complete.json`, diff at `<repo>/.git/`
- Read `admiral_notes/` per app
- Run `chad-captain scorecard --app <id>`
- Write captain admiral_note responses (auto-resolve simple notes)

**FAIL-CLOSED:** Twin NEVER modifies captain code (validator, extras) mid-flight without going through SCAFFOLD's draft+test pipeline. Engine code can be modified directly only with the captain paused.

**Reuse vs new:**
- REUSE: captain_log reader, scorecard, admiral_notes, gh pr CLI for repair PRs
- NEW: ~250 LOC review loop (`apps/chad-twin-daemon/review.py`) including event-tier router

---

### Step 9 — AGGREGATE (v7 — on-demand only, NO daily ritual)

**Goal:** ONE roll-up Twin produces. Iterate-until-done model: AGGREGATE fires when Chad asks (`twin status`) OR when a task transitions (completed, blocked, escalation). NO scheduled 06:00 ritual.

**When AGGREGATE fires:**
- **On Chad's command:** `twin status` (full roll-up) + drill subcommands. ALWAYS leads with "Changed since last viewed" delta so background activity is visible.
- **On task transition:** task complete → bundle into one final Zoom DM (Chad's personal account); task hard-blocked → small delta DM (just the change, not full state); captain spin-up while Chad is offline → enqueued for next-status delta, NOT immediate ping; auth-boundary escalation → uses ESCALATE flow (Step 10).
- **NEVER on a clock.** No daily, no hourly. Silence is the default.

**Activity cursor (R5#2 fix — Chad never loses background changes):**

Twin maintains `~/.chad/fleet/activity_cursor.json`:
```json
{
  "chad_last_viewed_at": "2026-05-04T22:14:11Z",   // bumped on every twin status
  "chad_last_acked_at":  "2026-05-04T22:14:11Z",   // bumped on explicit "ack" reply
  "events_since_view": [
    {"ts":"...","kind":"captain_spinup","app_id":"...","summary":"..."},
    {"ts":"...","kind":"captain_pause","app_id":"...","reason":"..."},
    {"ts":"...","kind":"task_complete","task_id":"...","prs":[...]},
    {"ts":"...","kind":"queue_state_change","task_id":"...","new_state":"queued_concurrency"}
  ]
}
```

`twin status` output FIRST shows:
```
📈 Changed since last viewed (2026-05-04 22:14 → 2026-05-05 06:30):
  + 3 new captains spun up: t6-foo, t7-bar, t8-baz (no auth-boundary)
  + 1 captain paused: t3-marketing (config error — already in needs-action)
  + 2 tasks completed: t4-es-bots, t5-rfp-responder
  + 1 captain queued: t9-quux (queued_rate_limit, earliest start 06:45)

Then full state:
  ...
```

Drill: `twin status --since last-seen` (default), `--since 24h`, `--since <iso-ts>`.

Activity cursor advances ONLY on explicit `twin status` invocations or on Chad's `ack` reply. Auto-bumping it on aggregate ESCALATE pings would re-introduce the "background captain spin-up gets lost" failure.

**Hierarchical format (R2#7 fix):** counts at top, only items needing action inline. Drill commands surface details on demand. Heading carries the trigger context (on-demand vs task-transition).

```
Fleet status — 2026-05-04 14:32 ET (on-demand)

📊 Counts
  Captains: 12 (10 green, 1 paused, 1 attention)
  Active tasks: 30 (24 in-flight, 4 blocked, 2 awaiting your reply)
  Quarantine: 2 files (oldest 6h)
  Sign-offs needed: 3

⚠ Needs your action:
  1. t5-rfp-responder PROFILE NEEDED [auth-boundary #5]
     Class: federal-rfp/regulated/polyglot/public-write
     Repo shape: polyglot (no existing scaffold profile fits)
     Repo: ~/code/cw/rfp-responder (greenfield, would be created by scaffold)
     Backlog: 6 items, top: "intake parser for SAM.gov RFP feed"
     Twin recommendation: build polyglot profile first (~200 LOC, 1 day);
       OR rescope task to python-pkg subset for v1 and split out the
       polyglot bits as a sibling task.
     Action: reply "build profile" / "rescope to python-pkg" / specifics

  2. t4-es-bots TASK COMPLETE — final sign-off
     Bundled PRs: #410 (fb-001), #412 (fb-002), #415 (fb-003)
     Tests: 47 added, all green; verify_cmd passes on each
     Risk: NO production deploy yet (deploy is fb-005, separate task)
     Twin verdict: SHIP. Action: reply "merge" or list concerns.

  3. t3-chadacys-marketing PAUSED — config error
     Why: .chad-captain.t3.json missing in deployed repo
     Auth-boundary: NO. Twin can write the file but needs Chad to confirm
       which Django settings module to point at (test vs dev).
     Action: reply "settings_module=<path>" or "you pick"

🟡 Awaiting your reply (1):
  • t3-config (q-2026-05-04-001), 4h ago, expires in 20h, priority=medium

🟢 Steady state (no action needed):
  10 captains running normally. Drill: `twin status --captain <id>`

Drill commands:
  twin status --captain <id>          per-captain detail
  twin status --task <id>             per-task DAG + artifacts
  twin status --quarantine            list quarantined intakes
  twin status --signoffs              full sign-off packet detail
  twin status --queue                 chad action queue with deps
```

**Per-action evidence (R1#16 fix):**
- PR link (or NEW for scaffold sign-offs)
- Changed surface (paths/files top-level)
- Tests run + result
- Risk + authority-boundary flag
- Twin recommendation (with alternatives when meaningful)
- Exact requested action (one verb: go / merge / hold / fix / specifics)

**Green predicate (R2#7 fix — never lie about "everything else is on track"):**
A captain is "green" only if ALL of:
- mode=autonomous AND not paused
- last validate verdict was accept or soft_accept
- scorecard delta over last 7 days ≥ -0.05
- no unconsumed admiral_notes older than 24h
- no open Twin escalation referencing this captain

A task is "green" only if:
- not in quarantine
- not awaiting Chad reply
- not blocked > 24h on a dependency
- has at least one captain actively working it

If a captain or task fails the green predicate, it MUST appear inline. The roll-up never says "everything else is on track" while hiding a yellow item.

**Quarantine SLO (R2#8 fix):**
- Quarantine count appears in the "Counts" header EVERY aggregate (even when 0).
- Escalate quarantine to inline "Needs your action" when ANY of:
  - count > 5
  - oldest file > 24h
  - any quarantined file has priority=high in its frontmatter
- `twin quarantine repair` auto-fixes recoverable issues (missing optional fields, simple frontmatter typos) and re-feeds the file to intake.

**Reuse vs new:**
- REUSE: scorecard subcommand, captain_log reader, registry load, gh pr list
- NEW: ~400 LOC aggregator (`apps/chad-twin-daemon/aggregate.py`) including drill subcommands and green-predicate evaluator

---

### Step 10 — ESCALATE (auth-boundary only; R1#17 fix)

**Goal:** Twin pings Chad ONLY on real decisions. Bundling, not per-event.

**Escalation matrix (v7):**

| Condition | When Twin pings Chad | Channel | Bundled? |
|-----------|----------------------|---------|----------|
| New scaffold profile needed (auth-boundary #5) | Immediate | Personal Zoom DM | NO |
| Projected captain cost > $20/day (auth-boundary #3) | Immediate | Personal Zoom DM | NO |
| Captain emitted `escalation_raised` Twin can't resolve | Within 15min | Personal Zoom DM (+ SMS if priority=high) | NO |
| Authority-boundary action needed (deploy, external comms, money, destructive) | Immediate | Personal Zoom DM (+ SMS if priority=high or production-touching) | NO |
| Clarification needed (Step 4), priority=high | Immediate | Personal Zoom DM **+ SMS** | NO |
| Clarification needed (Step 4), priority=medium/low | Immediate | Personal Zoom DM | NO |
| TASK COMPLETE — final sign-off (all PRs bundled) | Immediate when task transitions complete | Personal Zoom DM (+ SMS if priority=high) | YES |
| Engine repair PR (behavior-changing, blocklist path) | Immediate | Personal Zoom DM (+ SMS if all captains paused) | NO |
| Captain spin-up (default path, no auth-boundary) | NEVER (silent; surfaces in next on-demand AGGREGATE) | n/a | n/a |
| All captains green, nothing to decide | NEVER | n/a | n/a |

**Channel destinations (v9 — Chad has ONE Zoom account; reuse existing chad-agent S2S):**

- **Zoom DM target = Chad's user JID directly.** Twin reuses the existing chad-agent Zoom S2S creds (already in chad-agent's env: `CHAD_ZOOM_S2S_CLIENT_ID`, `CHAD_ZOOM_S2S_CLIENT_SECRET`, `CHAD_ZOOM_S2S_ACCOUNT_ID`, `CHAD_ZOOM_BOT_JID=chad.simon@cloudwarriors.ai`).
- DM destination is configurable via `CHAD_DM_TARGET` env var (defaults to `chad.simon@cloudwarriors.ai`). Twin sends to Chad's 1:1 DM channel resolved at first run via `list_user_channels_detail` filtered for `type=1` (1:1 personal).
- v8's personal-vs-company isolation was based on a wrong assumption (Chad has only one Zoom account). Removed in v9.

**Startup safety check (slimmer than v8 isolation):**
- On daemon start, Twin calls Zoom `users/me` and confirms returned account_id matches `CHAD_ZOOM_S2S_ACCOUNT_ID`.
- Resolves the DM channel ID for `CHAD_DM_TARGET` once at startup; persists to `~/.chad/fleet/.zoom_dm_channel_cache` for fast subsequent posts.
- Mismatch on either check → daemon refuses to start, logs identity mismatch, escalates via SMS (P0) IF Twilio creds present, else via stderr to systemd journal.

**Twilio SMS channel** (v5 design; v9 — Chad deferred creds, code ships disabled):

- Code ships in PR5 but stays DISABLED until Twilio creds present in `~/.chad/fleet/.env` on Linode.
- Without creds: every SMS path silently degrades — Zoom DM still fires for the same triggers (so high-priority escalations still reach Chad, just without the buzz). Twin logs "SMS disabled (no creds)" once at startup, NOT on every escalation.
- Once Chad drops creds: Twin auto-detects, enables SMS path, sends a one-time confirmation SMS so Chad knows it's hot.
- Required when enabled: `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `TWILIO_FROM_NUMBER`, `CHAD_PHONE_NUMBER`

**Two priority tiers (R4 fix — guarantees on the high-stakes path):**

| Tier | Triggers | Rate behavior |
|------|----------|---------------|
| **P0 — emergency** | Production deploy, destructive op, all-captains-paused, security incident, Twilio outage flagged | Reserve 1 SMS per hour for P0; bypass digest collapsing unless daily hard cap (8/day) is fully exhausted. P0 SMS body always includes `EMERGENCY:` prefix. |
| **P1 — high** | High-priority clarifications, captain escalations Twin can't resolve, profile-needed scaffold, projected-cost > $20/day | Subject to 3/hour, 8/day rate limit. Excess collapses into digest. |

When the daily 8/day cap is exhausted: P0 still sends if any of the 1/hour P0 reserve remains; P1 stops sending SMS entirely until midnight ET, Zoom DMs continue immediately, AGGREGATE flags "SMS daily cap reached."

**SMS body format** (160-char single-segment; reply grammar matches body — R4 fix):
```
CHAD-FLEET P1 q-2026-05-04-001
T3 captain stuck on .chad-captain.t3.json
Reply: Y q-2026-05-04-001 or N q-2026-05-04-001 (or Zoom Re: q-2026-05-04-001)
```

**Reply grammar (case-insensitive, exact regex):**
```
^(?P<answer>y|yes|n|no)\s+(?P<question_id>q-[\w-]+)$
```
Anything that doesn't match → Twin replies via Zoom asking for clarification, does NOT mutate the chad_action_queue.

**Inbound polling (R4 fix — concrete cursor + dedupe):**
- State at `~/.chad/fleet/sms_inbound_cursor.json` carries `{last_sid: str, last_date_sent_iso: str}`
- Twin polls Twilio `Messages` resource every 5min: `GET /2010-04-01/Accounts/<sid>/Messages.json?To=<TWILIO_FROM_NUMBER>&From=<CHAD_PHONE_NUMBER>&DateSent>=<last_date_sent_iso>&PageSize=20`
- For each new SID > last_sid: parse via reply grammar; on match → advance chad_action_queue with answer; persist raw inbound to twin journal with SID
- After processing: update cursor under flock
- **Dedupe by SID** (Twilio guarantees unique SIDs per message) — replays are no-ops
- **Auth failure / network error**: log to twin journal; mark SMS reply ingestion `degraded` in AGGREGATE if polling fails for >15min; AGGREGATE shows "SMS replies degraded — answer via Zoom"

**Suppressed escalation traceability (R4 fix):**
- When digest collapses ≥1 P1 escalations, persist to `~/.chad/fleet/sms_suppressed_escalations.json` with the list of suppressed `question_id`s + suppression reason (rate_limit | daily_cap)
- AGGREGATE drill: `twin status --sms-suppressed` shows suppressed items with their original Zoom DM links
- Suppressed items are ALWAYS in their Zoom DM thread; SMS suppression never loses the actual question

**FAIL-CLOSED**: SMS send failure → log to twin journal, fall back to Zoom DM, NEVER silently drop. Twilio unreachable for >15min during ANY P0 → emit P0 SMS retry every 60s until reachable OR escalate to fleet health alarm in AGGREGATE.

**Hard NO list:**
- Per-PR ready-for-review pings (bundled in task complete) (R1#17)
- Captain accept verdicts
- Replan triggered by drained roadmap
- Plist tick fired
- Goose-runner timeout (Twin handles via captain pause)
- Scorecard noise within ±0.05
- Engine repair PR (additive/non-breaking)

**FAIL-CLOSED:** Twin does NOT auto-execute authority-boundary actions even if Chad is asleep. Pause + journal + ping; resume on Chad's reply.

**Reuse vs new:**
- REUSE: chad-agent Zoom DM tool, AGGREGATE output
- NEW: ~120 LOC escalation policy engine (`apps/chad-twin-daemon/escalate.py`)

---

### Step 11 — CLOSE (task-scoped; R1#10 fix)

**Goal:** Task done. Persist learnings. Move task file to archive.

**Task scoping (R1#10 fix):** Every backlog item, roadmap slice, captain_log entry, and PR carries a `task_id` field. Close queries filter by that field.

Schema additions to existing chad-captain types:
- `FeatureBacklogItem.task_id: str | None` — set by SCAFFOLD when seeding from a task
- `RoadmapSlice.task_id: str | None` — copied from backlog item
- `CaptainLogEntry.task_id: str | None` — copied from current dispatched slice
- `gh pr create` body includes `Closes-Task: <task_id>` (Twin parses)

**Twin's close actions:**
1. Confirm: all backlog items with this task_id are status=shipped or status=deferred
2. Confirm: all PRs labeled with this task_id are merged
3. Confirm: all admiral_notes referencing this task_id are consumed
4. Move `inbox/processing/<task_id>.md` → `inbox/done/<task_id>.md` with completion footer
5. Append `task_completed` event to twin journal
6. omni-mem `save_memory` for durable lessons (blockers, novel decisions, patterns)
7. omni-mem `fact_add` for durable factual relationships established
8. If task spawned a new captain: `save_preference` for that captain's quirks
9. Final ping bundled into next AGGREGATE: "task <id> complete; X PRs merged, Y memories saved."

**FAIL-CLOSED:** Don't close while a captain has open backlog items / unmerged PRs / unconsumed admiral_notes for this task_id.

**Reuse vs new:**
- REUSE: omni-mem save_memory/fact_add/save_preference/journal_write
- NEW: ~80 LOC close handler + chad-captain protocol additions for task_id (~50 LOC engine change)

---

## Components map (revised after R1)

### Exists today
- chad-captain runtime (registry, daemon, validator, replanner, scorecard, fixture validator, custom prompts)
- 4 hand-built captains (T1, T2, T3, T4)
- omni-mem (memory + facts + journal; primitives: search, save_memory, save_preference, journal_write, fact_add)
- chad-agent (Zoom DM, calendar, voice)
- chad-twin agent definition (this agent)

### New — to build (revised LOC after R1+R2)

| Component | Path | LOC | Slice |
|-----------|------|-----|-------|
| **Engine: dynamic extras + task_id field + chad-captain artifact CLI hook** | `apps/chad-captain/src/chad_captain/{extras/__init__.py,protocol.py,cli.py}` | ~250 | **S5f** (must merge first) |
| Scaffold profiles + Jinja2 renderer | `apps/chad-captain-scaffold/{core.py,profiles/}` | ~400 | S5a |
| Scaffold transaction + manifest + rollback | `apps/chad-captain-scaffold/transaction.py` | ~350 | S5b |
| Scaffold workspace (init + backlog + branch) | `apps/chad-captain-scaffold/workspace.py` | ~200 | S5c |
| Scaffold bootstrap (plists + greenfield) | `apps/chad-captain-scaffold/bootstrap.py` | ~200 | S5d |
| Artifact bus CLI + manifest schema | `apps/chad-captain-scaffold/artifacts.py` | ~250 | S5e |
| Scaffold acceptance tests (4 profiles) | `apps/chad-captain-scaffold/tests/` | ~500 | S5g |
| Inbox watcher daemon (watchfiles) | `apps/chad-twin-daemon/intake.py` | ~80 | S1 |
| Classifier (split_task + DAG cycle check) | `apps/chad-twin-daemon/classifier.py` | ~250 | S2 |
| Research orchestrator | `apps/chad-twin-daemon/research.py` | ~350 | S3 |
| Clarification (chad action queue + cycle detection) | `apps/chad-twin-daemon/clarify.py` | ~200 | S4 |
| Review loop (event-tier router + engine repair allowlist) | `apps/chad-twin-daemon/review.py` | ~300 | S6 |
| Aggregator (hierarchical + drill commands + green predicate) | `apps/chad-twin-daemon/aggregate.py` | ~400 | S7 |
| Escalation policy (auth-boundary gate) | `apps/chad-twin-daemon/escalate.py` | ~120 | S8 |
| Close handler (task_id-scoped) | `apps/chad-twin-daemon/close.py` | ~80 | S9 |
| Daemon launcher + systemd unit + watchdog (v7: targets personal Linode VPS) | `apps/chad-twin-daemon/main.py` + `ops/twin-daemon.service` | ~200 | S10 |
| End-to-end tests | `apps/chad-twin-daemon/tests/` | ~700 | per-slice |

**Total new code:** ~4,830 LOC prod + ~700 LOC test = ~5,530 LOC across 16 functional slices + 1 wiring slice.

**Per-slice scope:** All slices ≤ 600 LOC. Highest-leverage clusters:
1. **S5f engine prep** (must merge first; everything depends on dynamic extras + task_id)
2. **S5a-S5g scaffold engine** (the difference between hand-tuned captains and 5-dev-teams)
3. **S6 review** (engine repair allowlist + canary is the autonomy unlock)

---

## Sequencing (revised — research mandatory; engine fixes first)

```
S5f ENGINE (dynamic extras + task_id field — must merge BEFORE scaffold lands)
   │
   ▼
S5a PROFILES + RENDERER ──┐
S5b TRANSACTION ──────────┤
S5c WORKSPACE ────────────┼──▶ S5g ACCEPTANCE
S5d BOOTSTRAP ────────────┤
S5e ARTIFACT BUS ─────────┘
   │
   ▼
S1 INTAKE ──▶ S2 CLASSIFY ──▶ S3 RESEARCH ──▶ S4 CLARIFY (cond)
                                                  │
                                                  ▼
                                              SCAFFOLD pipeline (S5a-S5g, lib-call from Twin)
                                                  │
                          (existing dispatch loop) ◀──── S6 REVIEW ──▶ S7 AGGREGATE ──▶ S8 ESCALATE ──▶ S9 CLOSE
                                                                          ▲
                                                                          │
                                                                       S10 DAEMON wires it all
```

**Critical path:** S5f → (S5a..S5e parallel) → S5g → S1 → S2 → S3 → S6 → S7.

**Build-order deadlocks:** none. S5b/S5c/S5d/S5e are parallel siblings after S5a; S5g gates on all of them. S6+S7+S8+S9 are pipeline stages with no back-edges.

### Runtime deadlock detection (R2#10 fix — the doc now narrows the no-deadlock claim)

Build-time and runtime are different. At runtime, deadlock-shaped failures CAN happen and Twin must detect them:

| Runtime deadlock | Detector | Recovery |
|------------------|----------|----------|
| Task DAG cycle (subtask A blocks B blocks A) | Cycle-check on every classification before SCAFFOLD | Reject classification; emit `clarify` outcome |
| Clarification cycle (q1 depends on q2 depends on q1) | Cycle-check in chad_action_queue.json on every new question | Emit ONE "break-the-tie" question to Chad |
| Scaffold lock held > 60s | flock timeout + watchdog | Surface in AGGREGATE as "scaffold stuck"; admin runs `twin scaffold abort <txn_id>` |
| Captain paused > 24h with no escalation | aggregator predicate | Surface in AGGREGATE; Twin attempts auto-resume, escalates if fails |
| Task blocked > 24h on artifact dependency | aggregator predicate | Surface in AGGREGATE with the missing artifact name + producing subtask state |
| omni-mem unreachable > 1h | watchdog | Surface in AGGREGATE; Twin spool drain status reported |
| Two captains racing on same artifact path | bus-write conflict (rename fails) | Surface in twin journal; second writer retries with suffix |

---

## Decisions baked in (Twin makes; Chad does NOT need to approve)

1. **Inbox = `~/.chad/fleet/inbox/`** — file-based, watchfiles + 5min poll backstop.
2. **Twin daemon hosting = Chad's personal Linode VPS systemd** + **canonical log/event plane on Linode** (v8 R5#1 fix). See "Hosting topology" section below.
3. **Scaffold templates = concrete `.j2` files**, NOT a DSL.
4. **Classifier uses Claude haiku via cw-gateway** (cheap, fast, JSON-schema-constrained).
5. **Twin reads captain_log on file events; tier 1 events handled in 60s, tier 2 batched 15min.**
6. **Aggregate is on-demand only** (`twin status`) + on task transitions. NO daily/scheduled ritual.
7. **Twin auto-registers EVERY captain** in parallel; only profile-needed (auth-boundary #5) and cost > $20/day (auth-boundary #3) gate Twin.
8. **Twin auto-approves roadmaps** if sanity passes; retries 2x with hints; only escalates on persistent failure.
9. **PRs bundled into task complete sign-off**, not per-PR pings.
10. **Authority-boundary list is locked** (see top of doc): production deploys, external comms, money, destructive ops, new scaffold profile needed, genuine ambiguity, final task sign-off.
11. **Hosting**: Twin daemon on Linode (`ssh linode` → `23.92.20.39`); Zoom DMs to Chad's user JID via existing chad-agent S2S creds; SMS deferred (code ships disabled, auto-enables on cred drop).
12. **Captain rate caps**: max 5 new captains spun up per 24h; max 12 concurrent active captains (Linode resource ceiling).

---

## Decisions ANSWERED by Chad (v7 lock-in)

All 6 v4/v5 questions resolved:

1. **Inbox surface:** ✅ `~/.chad/fleet/inbox/` + chad-agent Zoom-to-md hook (both ingress paths)
2. **Aggregate schedule:** ✅ ON-DEMAND ONLY — `twin status` + task transitions. NO daily ritual. Iterate-until-done model.
3. **Escalation channel:** ✅ Personal Zoom DM (separate creds from company chad-agent) + Twilio SMS for P0/P1. Reply via Zoom thread or SMS Y/N.
4. **Twin daemon hosting:** ✅ Chad's personal Linode VPS systemd (NOT noob-root — that's company infra). MacBook still runs goose-runner workers for captains whose repos live there.
5. **Authority-boundary list:** ✅ 7 items locked (with #5 redefined: "new scaffold profile needed" not "first-of-class captain").
6. **First-of-class GATE:** ❌ REMOVED. Captains spin up in parallel automatically. Captain manages task → captain elevates updates to Twin (admiral) → Twin escalates to Chad ONLY on auth-boundary actions. Safety rails: profile-needed gate, $20/day cost gate, 5 new captains / 24h rate limit, 12 concurrent ceiling.

### Open follow-ups — ALL ANSWERED (v9)

1. ✅ **Linode**: host `linode` / `23.92.20.39` / user `root` / key `~/.ssh/id_ed25519` (from Chad's ~/.ssh/config). Twin systemd unit deploys via `ssh linode`.
2. ✅ **Twilio creds: deferred.** SMS code ships in PR5 disabled; auto-enables when creds appear in `~/.chad/fleet/.env`. High-priority escalations still reach Chad via Zoom DM in the meantime.
3. ✅ **Zoom**: ONE account; reuse existing chad-agent S2S creds; DM target = `chad.simon@cloudwarriors.ai` (Chad's user JID); 1:1 channel ID resolved at first run via `list_user_channels_detail`.

---

## Failure modes + recovery (R1#18 fix — strong omni-mem semantics)

| Failure | Detect | Recover |
|---------|--------|---------|
| Twin daemon crashes | launchd KeepAlive | Auto-restart; replay inbox + captain logs from last journal checkpoint |
| Inbox watcher misses a file | 5min reconciliation scan | Catch-up scan finds it, processes idempotently |
| Classifier returns wrong captain | Twin pre-scaffold validation | Re-classify with extra context; if still wrong, escalate (clarify) |
| Scaffold engine writes broken captain | Draft tests + AST-patch validation | No install; partial scaffold left in scaffold-drafts/ for inspection |
| Captain runs amok (PR storm) | Twin watches PR creation rate (>5/hour = anomaly) | Pause captain via existing pause mechanism; ping Chad |
| omni-mem unreachable for memory writes | Retry 3x | Spool to `~/.chad/fleet/.pending-mem/`, drain when reachable. Classification + close BLOCK on memory; non-critical writes continue. |
| omni-mem unreachable for memory reads | Retry 3x | Classify uses local fallback (registry-only matching); research notes "memory unavailable" in research.json |
| Chad doesn't reply to clarification by expires_at | priority-aware re-ping (high only) | Pause task in awaiting_chad/; surface in AGGREGATE; never proceed on assumption |
| Two simultaneous scaffolds racing | flock at `~/.chad/fleet/.scaffold.lock` | Sequential serialization; second waits up to 60s, then errors |
| Captain ticking while Twin is editing engine | Captain pause set BEFORE engine edit; flock on `~/.chad/captain/.engine.lock` | Pause captain; edit engine; verify; unpause |
| Two inbox files arriving same second | SHA-prefix task_ids are unique | Both processed independently |

---

## Anti-stop patterns (vision-aligned)

These failure modes break "Chad is LAST stop only":

1. **Per-PR ping = fail.** PRs bundle into task complete sign-off.
2. **Roadmap approval ping = fail.** Twin auto-approves on sanity pass.
3. **Scaffold approval ping per captain = fail.** Captains spin up in parallel; only NEW scaffold profiles or cost > $20/day need Chad approval (v7).
4. **Mid-flight clarification on resolvable items = fail.** Research must exhaust before asking.
5. **Engine repair ping for additive fix = fail.** Twin owns non-breaking engine repair.
6. **Daily aggregate "anything for me?" ping when nothing actionable = fail.** AGGREGATE is silent if no sign-offs.

---

## Validation taxonomy (v5 — unified definition of "validated")

The fleet has SIX distinct validation surfaces. Each gates a different transition. None is optional; none replaces another.

### Layer 1 — Slice verify_cmd (per-slice repo gate)

**What:** The captain's repo `verify_cmd` (e.g. `make check`, `npm test`, `uv run pytest`) runs after goose finishes editing.

**Gates:** Slice acceptance. Non-zero exit → captain validator downgrades verdict to `reject_retry` or `reject_hard`.

**Owner:** chad-captain engine (`apply_verify_gate` in validator.py — exists).

**Required for every captain:** YES. Every RegisteredApp.verify_cmd must be set; SCAFFOLD refuses to register a captain with empty verify_cmd. (Engine prep PR5 needs to add this validation to apps_registry.)

**Failure mode:** verify_cmd times out → reject_retry; non-zero exit → reject_retry first attempt, reject_hard on retry.

### Layer 2 — Custom validators (per-app contract gate)

**What:** Optional `validator_module` per RegisteredApp. Adds app-specific gates ON TOP OF Layer 1 (e.g. T3's Django fixture FK validator).

**Gates:** Slice acceptance, AFTER Layer 1 verify_cmd passes. Custom validator can ADD failures; cannot SUPPRESS verify_cmd failure.

**Engine enforcement (R4 fix — fixed in PR5):** the engine wraps custom validators so Layer 1 always runs first and its failure short-circuits before the custom hook fires. Order:

```
default structural validation (validate_slice)
  → verify_cmd gate (apply_verify_gate, ENGINE-OWNED)
  → custom validator hook (validator_module.validate_app_completion)
  → scorecard rubric
```

The custom validator receives the verify-gate-passed result; it can downgrade verdict to reject_retry/reject_hard but cannot reverse a verify_cmd failure into accept. Engine prep PR5 refactors `validator.py::_resolve_validate_fn` so verify_cmd is enforced in the wrapper, NOT inside the custom validator.

**Owner:** Per-app captain code. Default chain when `validator_module` is None.

**Required for every captain:** NO. Default chain is sufficient unless the app has fragile contracts (fixtures, migrations, contract tests).

**Catalog of validators we ship in v1:**
- `default` — engine chain (validate_slice → reuse-regression → verify-gate)
- `t3_marketing` — fixture FK gate (PR4, shipped)
- (future) `migration_safety` — gates Django/Postgres migrations against `--check --dry-run`
- (future) `contract_test` — gates API surface changes against pact/openapi diff
- (future) `schema_lock` — rejects schema changes that break consumer fixtures across the fleet

Adding a new validator = new file in `apps/chad-captain/src/chad_captain/validators/<name>.py` exposing `validate_app_completion(...)`. SCAFFOLD selects validator based on profile + research (e.g. django-app profile + research found Django migrations → wire `migration_safety`).

### Layer 3 — Scoreboard rubric (per-captain trend gate)

**What:** Per-app extras dimensions (Step 5.2 dynamic discovery) + baseline rubric. Runs on every accept verdict; produces `rubric_delta_pp`.

**Gates:** Captain `auto_merge_min_delta` (default 0.0 = no regression allowed). Engine's existing `auto_merge` flow uses this. Twin's review uses it as input to the green predicate.

**Owner:** chad-captain engine (`scorecard.py` + `extras/`).

**Required for every captain:** YES (baseline always runs). Per-app extras are optional but strongly recommended (every captain we've shipped has them).

### Layer 4 — Twin review (per-slice operator gate)

**What:** Twin reads captain_log.jsonl entries and applies the review rubric (Step 8 table: accept silent / reject_retry silent / reject_hard inspect / escalate respond).

**Gates:** Whether Twin acts on the captain's verdict (escalate to Chad, repair fleet, do nothing). Does NOT override captain's verdict — Twin is observer + responder, not the engine's validator.

**Twin verdict authority (R4 fix):**
- Twin **CANNOT** turn a captain reject into accept. Captain's reject is binding for the slice.
- Twin **CAN** block close on observed risk after captain accept (e.g. accept verdict but Twin's review surfaced a security smell, performance regression on a tracked benchmark, or unconsumed admiral_note from another captain). When Twin blocks, it writes `~/.chad/fleet/apps/<app_id>/twin_holds/<slice_id>.json` with `reason`, `surfaces_in_aggregate=true`, `expires_at` (default: +24h auto-resolve unless renewed). Close logic (Step 11) reads twin_holds and refuses to close while any unexpired hold exists.
- Twin **CAN** pause a captain on observed risk; pause is recorded under same `twin_hold` semantics so AGGREGATE shows it.

**Owner:** Twin daemon (S6 review.py).

### Layer 5 — Scaffold VERIFY phase (generated-captain gate)

**What:** Phase 2 of the scaffold transaction. Runs the chad-captain test suite against a PYTHONPATH overlay containing the staging files.

**Gates:** Whether the scaffold INSTALL phase fires. VERIFY fail → no install, no registry write.

**Owner:** chad-captain-scaffold (S5b transaction.py).

**What it actually validates:**
- Generated `extras/<slug>.py` imports cleanly and `factory()` returns valid DimensionScores
- Generated `validators/<slug>.py` (if any) imports cleanly and exposes `validate_app_completion`
- The full chad-captain test suite still passes with the staging files in PYTHONPATH (catches break-by-overlay regressions)
- py_compile succeeds on every generated .py
- Backlog seed JSON validates against the schema

### Layer 6 — Engine repair canary (engine-fix gate)

**What:** Synthetic `_canary` captain runs one tick on the patched engine.

**Gates:** Whether Twin auto-merges an engine repair PR + unpauses the real fleet.

**Owner:** Twin daemon + new `chad-captain canary` CLI (PR5 engine prep).

### Cross-task contract validation (NEW in v5)

When `split_task` produces an artifact handoff (subtask A produces `fixture:marketing_posts_001.json`, subtask B consumes it), the artifact bus validates the contract:

1. **At put time:** captain A calls `chad-captain artifact put --name <n> --schema <schema_id>`. Bus checks the file matches the registered schema (e.g. JSON validates against a stored Pydantic schema). Missing schema → put refused.
2. **At get time:** captain B calls `chad-captain artifact get --name <n> --expected-schema <schema_id>`. Schema mismatch → get refused, B's slice fails verify, B replans with the contract error in retry_context.
3. **Schemas live at** `~/.chad/fleet/tasks/<task_id>/schemas/<schema_id>.json` (Pydantic-emitted JSON Schema). SCAFFOLD generates a schema for every `produces` declaration that has a structured shape; freeform artifacts (markdown, text logs) skip schema validation.

**Schemaless artifacts are allowed** but flagged in AGGREGATE under "fleet health" — too many schemaless cross-captain handoffs = future contract drift.

### What "validated for close" means (Step 11 refined; R4 multi-captain fix)

A task closes (Step 11) ONLY when ALL of:
- Every backlog item with this task_id (across EVERY participating captain in a split_task DAG) is `status=shipped` or `status=deferred-with-justification`
- Every PR labeled `Closes-Task: <task_id>` is merged
- Every admiral_note referencing this task_id is consumed (across all participating captains)
- **For every participating captain's repo HEAD:** `verify_cmd` exits 0 (Twin runs once per captain at close to confirm the merge of the last PR didn't break anything; uses `RegisteredApp.verify_host` for noob-root or remote captains, see below)
- For each artifact in `~/.chad/fleet/tasks/<task_id>/artifacts/`: schema validation re-runs and passes (catches the case where a producer's schema was updated mid-flight, leaving a stale artifact)
- **For every participating captain's scoreboard:** last validate entry shows `accept` (no `escalate`/`reject_hard` lingering)
- **No unexpired `twin_hold` records** exist for any participating captain (Layer 4)

### Remote-captain verify execution (R4 fix — `verify_host` field)

Captains whose repos live on noob-root (T4 ES bots) or other remote hosts need explicit verify execution config on `RegisteredApp`:

```python
class VerifyHost(BaseModel):
    kind: Literal["local", "ssh"]
    host: str | None = None          # required when kind=ssh
    cwd: str                          # path to repo root on the target host
    command: str                      # the verify command (overrides verify_cmd if set)
    timeout_seconds: int = 300

class RegisteredApp(BaseModel):
    ...
    verify_host: VerifyHost | None = None  # None = use local verify_cmd
```

Engine helper (PR5) `run_verify(reg_app)`:
- `kind=local` → existing `apply_verify_gate` against repo_path
- `kind=ssh` → `ssh <host> "cd <cwd> && <command>"` with timeout, captured stdout/stderr, exit-code propagation
- SSH failures (network, auth) → return as verify failure with stderr reason; AGGREGATE flags as fleet health (host unreachable)

If any check fails → close blocks, surfaces in AGGREGATE as "task ready except <list>", Twin attempts auto-fix (re-run verify, re-validate schema, refresh from origin), escalates to Chad if auto-fix fails.

### Test-coverage requirements (NEW in v5)

The fleet does NOT enforce a numeric coverage threshold. It enforces:

**Test count regression gate (R4 fix — deterministic enforcement at slice validation time, NOT commit-message parsing):**

The captain validator inspects the slice's `git diff` directly:
- Detects deleted test functions/files in the diff
- Requires `slice_complete.removed_tests_reason: str | None` field (NEW field on `SliceComplete`, added in PR5 engine prep) when deletions are present
- If diff has deletions AND `removed_tests_reason` is None/empty → `reject_retry` with retry_context = "test deletions require removed_tests_reason in slice_complete or PR-body trailer"
- When the captain creates the PR, `Removed-tests: <reason>` is copied from `slice_complete.removed_tests_reason` into the PR body trailer (Twin verifies at close)
- Twin's close handler parses PR body trailers; missing trailer when slice_complete recorded deletions → close blocks

This is enforceable because (a) the diff is structured data the validator already reads, (b) `slice_complete` is the goose-runner's authoritative output, (c) PR body is generated from slice_complete by the engine — not free-form by goose.

**Test count growth signal**: scoreboard's `captain_test_count_growing` extra (already exists for captain-self) is REUSED as the cross-captain pattern. Each captain gets a `<repo>_test_count_growing` extra at SCAFFOLD time, baseline = HEAD test count at registration, target = grow by ≥1 test per shipped backlog item.

**No coverage tools required** (pytest-cov, jest --coverage, etc.) — the count signal is sufficient and avoids tool-specific config drift.

Captains that genuinely need higher rigor (e.g. the future `federal-rfp` captain) opt into a stricter validator (Layer 2) with a coverage-threshold gate.

### What the fleet does NOT validate (deliberate gaps)

- **Performance / latency**: out of scope. If a captain ships a slow regression, scorecard shouldn't catch it; that's a per-app benchmark suite (admiral discipline).
- **Security scanning**: out of scope for v1. If a captain ships secrets, gitleaks runs in CI on the PR (existing org-wide hook). Twin trusts CI.
- **Cross-captain test pollution**: out of scope. Each captain runs its own verify_cmd in its own repo cwd; no shared test state.
- **Documentation completeness**: out of scope. Captains may produce undocumented code; admiral catches in PR review.

---

## What this DOESN'T solve

- Multi-agent within a captain (one slice → multiple workers): existing captain behavior, not in scope here
- Captain-to-captain messaging: deliberately out of scope; Twin orchestrates dependencies via `blocked_by`
- Distributed fleet across machines: single-machine MVP; multi-machine is future work
- Cost tracking per captain: out of scope for v1; add as a captain extra later
- Visual dashboards beyond aggregate DM: chad-dashboard already exists for visual; Twin's aggregate is the chat-surface roll-up
- Performance/latency regressions: per-app benchmarks, not fleet-level
- Security scanning: trusts org-wide CI hooks (gitleaks etc.)
