# Fleet Process — End-to-End Spec (v4)

> **Status:** v4 after codex R1 (22) + R2 (10) + R3 (10) addressed. **Ready for Chad's approval.**

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

**Total engine prep:** ~560 LOC + tests. Ships as **PR5 (engine prep) BEFORE PR6+ (twin daemon).**
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

## Authority boundary (definitive list)

Twin escalates to Chad ONLY for:

1. **Production deploys** to user-facing surfaces (Spark publish to KDP, chadacys.com push, customer-running services)
2. **External communications** (any non-Chad recipient: customers, prospects, public posts, federal RFP responses)
3. **Money** (any payment, subscription change, contractor invoice, AWS spend > $50/event)
4. **Destructive ops** that can't be reverted by `git revert` (DB drops, force-push to main, secret rotation)
5. **New captain registration** ONLY the FIRST time a `task_class` is seen (definition below; subsequent tasks of same class auto-register without ping; see Step 5)
6. **Genuine direction ambiguity** (research + classifier confidence both < 0.7)
7. **Final task completion sign-off** (bundled, not per-PR — R1#17 fix)

Everything else: Twin acts on its own. PR review for non-authority-boundary work is BUNDLED into final task sign-off, not a per-PR ping.

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

**First-of-class trigger:** `canonical_key()` not in approved-classes registry, OR `risk_rank` for the canonical_key is higher than previously approved.

**Approved-classes registry:** `~/.chad/captain/approved_classes.json` (same atomic+flock treatment as `apps_registry.json`):
```json
{
  "schema_version": 1,
  "approved": [
    {
      "canonical_key": "v1|manuscript-publishing|local-only|python-pkg|no-external",
      "task_class": {...},
      "approved_at": "...",
      "example_task_id": "...",
      "example_app_id": "spark-of-defiance",
      "denied_boundaries": []  // e.g. ["no-money", "no-deploy"]
    }
  ]
}
```

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
- `federal-rfp/regulated/polyglot/public-write` (future) — high-risk class, would always be first-of-class

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
  "open_questions": ["..."],
  "minimal_research_complete": true
}
```

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

### Step 5 — SCAFFOLD (Twin auto-registers; R1#1, #3, #4, #5, #6, #20 + R2#1, #2, #3, #9 fixes)

**Goal:** Generate a working captain from templates via a profile system. **Twin auto-registers without Chad approval** UNLESS this is the FIRST captain for a `task_class` Twin has never seen, OR the FIRST use of a new scaffold profile.

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
- No matching profile → emit `profile_needed` outcome (NOT a broken captain). Twin journals + AGGREGATE surfaces it. Chad gets first-of-class ping that includes "this requires a new scaffold profile; here's the research output, here's the missing shape."
- First use of an existing profile = first-of-class trigger (counts as auth-boundary).

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
  - Validate research.json complete; profile selected; task_class approved (or first-of-class flag set)
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

phase 3: INSTALL (atomic, in dependency order)
  - For each file in files_to_create:
      - Copy staging → live path via tempfile + rename
      - Append to manifest.installed_files
  - Init workspace dir; write backlog seed
  - Update manifest.phase = "INSTALLED"

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

#### 5.7 — Scaffold output to Chad (only first-of-class)

```
Scaffolded NEW captain class: <app_id>
Class: <task_class>     (e.g. marketing-content/prod-deploy-later/django-app/public-read)
Profile: <profile_id>   (django-app, default-python-pkg, ts-app, static-site, NEW)

Repo: <path>
Mode: autonomous, auto_replan=False (Twin will flip after first replan inspection)
Validator: default chain | custom (<reason>)
Backlog: <N> items, top: <fb-001 title>
Verify: <verify_cmd>

This is the first captain in class "<task_class>"
[and/or: This is the first use of profile "<profile_id>"].
Approve to register? Reply "go" or list specific concerns.
Future captains in this class+profile register without asking.
```

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

### Step 6 — PLAN (existing — captain replanner; Twin auto-approves; R1#1, #15 fixes)

**Goal:** Captain reads backlog, generates roadmap.

**Twin's role:**
1. After SCAFFOLD installs the captain, Twin runs `chad-captain replan --app <app_id> --trigger initial`.
2. Twin inspects the roadmap (sanity: slice count matches backlog, no slice exceeds estimated_slice_count from backlog by >2x, no slice has empty system_prompt).
3. **Twin auto-approves** roadmap and flips `auto_replan=True` if sanity passes (R1#1 fix).
4. If sanity fails → Twin re-runs replan with hint context, retries up to 2x. After 2 failures, escalate to Chad with the failed roadmap.

**S6/S7 setup dependencies (R1#15 fix):** Step 5 (specifically S5c) is the explicit owner of: workspace init, `feature_backlog.json` write, `captain_branch` setup, plist install. Step 6 only handles replan-and-inspect.

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

### Step 9 — AGGREGATE (R1#16 + R2#7 + R2#8 fixes — hierarchical, scales, quarantine SLOs)

**Goal:** ONE roll-up Twin produces, on a schedule. Format scales to 12+ captains, 30+ tasks.

**Schedule:** Daily at 06:00 ET + on-demand via `twin status` and drill commands.

**Hierarchical format (R2#7 fix):** counts at top, only items needing action inline. Drill commands surface details on demand.

```
Fleet status — 2026-05-04 06:00 ET

📊 Counts
  Captains: 12 (10 green, 1 paused, 1 attention)
  Active tasks: 30 (24 in-flight, 4 blocked, 2 awaiting your reply)
  Quarantine: 2 files (oldest 6h)
  Sign-offs needed: 3

⚠ Needs your action:
  1. t5-rfp-responder NEW CAPTAIN CLASS [first-of-class]
     Class: federal-rfp/regulated/polyglot/public-write
     Profile: NEW (no profile fits — `polyglot` shape)
     Repo: ~/code/cw/rfp-responder (greenfield)
     Backlog: 6 items, top: "intake parser for SAM.gov RFP feed"
     Risk: federal compliance scope; no money/auth-boundary in immediate slices
     Twin recommendation: build polyglot profile first (~200 LOC, 1 day);
       OR scope captain to python-pkg subset for v1 and split out the
       polyglot bits as a sibling captain.
     Action: reply "go polyglot-profile" / "go python-pkg-only" / specifics

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

**Escalation matrix:**

| Condition | When Twin pings Chad | Channel | Bundled? |
|-----------|----------------------|---------|----------|
| FIRST captain of a new task_class | Immediate | Zoom DM | NO |
| Captain emitted `escalation_raised` Twin can't resolve | Within 15min | Zoom DM | NO |
| Authority-boundary action needed (deploy, external comms, money, destructive) | Immediate | Zoom DM | NO |
| Clarification needed (Step 4) | Immediate | Zoom DM | NO |
| TASK COMPLETE — final sign-off (all PRs bundled) | Daily AGGREGATE or immediate if priority=high | Zoom DM | YES |
| Engine repair PR (behavior-changing) | Immediate | Zoom DM | NO |
| All captains green, nothing to decide | NEVER | n/a | n/a |

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
| Daemon launcher + systemd unit + watchdog | `apps/chad-twin-daemon/main.py` + `ops/twin-daemon.service` | ~200 | S10 |
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
2. **Twin daemon hosting = noob-root systemd** (R3#9 fix — MacBook launchd doesn't run while asleep, breaks 24/7 fleet ops). MacBook launchd is fallback for local-only MVP/replay. Captains running goose-runner that need to interact with local files on Chad's machine still run on Chad's MacBook with `caffeinate` while active; the Twin daemon (orchestrator) lives on noob-root.
3. **Scaffold templates = concrete `.j2` files**, NOT a DSL.
4. **Classifier uses Claude haiku via cw-gateway** (cheap, fast, JSON-schema-constrained).
5. **Twin reads captain_log on file events; tier 1 events handled in 60s, tier 2 batched 15min.**
6. **Aggregate at 06:00 ET daily.**
7. **Twin auto-registers captains** unless first-of-class.
8. **Twin auto-approves roadmaps** if sanity passes; retries 2x with hints; only escalates on persistent failure.
9. **PRs bundled into task complete sign-off**, not per-PR pings.
10. **Authority-boundary list is locked** (see top of doc): production deploys, external comms, money, destructive ops, first-of-class captains, genuine ambiguity, final task sign-off.

---

## Decisions awaiting Chad (Step 4-bundled questions)

ONE Zoom DM batch when Twin reaches the implementation gate:

1. **Inbox surface confirmed:** `~/.chad/fleet/inbox/` + chad-agent Zoom-to-md hook. ✅ default; reply "different" if not.
2. **Aggregate schedule confirmed:** 06:00 ET daily + on-demand. Reply "different" if not.
3. **Escalation channel:** Zoom DM only (default), OR add iMessage/SMS for priority=high?
4. **Twin daemon hosting:** noob-root systemd is the proposed default (MacBook launchd doesn't run while asleep). Confirm or pick MacBook with explicit understanding of sleep gaps.
5. **Authority-boundary list:** confirm the 7-item list above is complete and correct.
6. **First-of-class definition:** is "task_class" defined by classifier domain tags (e.g. "manuscript-publishing", "infrastructure", "marketing-content"), or by repo, or admiral-defined?

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
3. **Scaffold approval ping per captain = fail.** Only first-of-class needs Chad approval.
4. **Mid-flight clarification on resolvable items = fail.** Research must exhaust before asking.
5. **Engine repair ping for additive fix = fail.** Twin owns non-breaking engine repair.
6. **Daily aggregate "anything for me?" ping when nothing actionable = fail.** AGGREGATE is silent if no sign-offs.

---

## What this DOESN'T solve

- Multi-agent within a captain (one slice → multiple workers): existing captain behavior, not in scope here
- Captain-to-captain messaging: deliberately out of scope; Twin orchestrates dependencies via `blocked_by`
- Distributed fleet across machines: single-machine MVP; multi-machine is future work
- Cost tracking per captain: out of scope for v1; add as a captain extra later
- Visual dashboards beyond aggregate DM: chad-dashboard already exists for visual; Twin's aggregate is the chat-surface roll-up
