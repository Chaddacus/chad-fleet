# Captain Dashboard — handoff brief for claude-design

This is a **frontend-only** task. The backend is shipped, tested, dogfooded,
and live. You're consuming a stable HTTP API and producing the admiral's
view of his fleet.

## What you are building

The **Captain Dashboard**: the admiral's operating surface for a fleet of
LLM-supervised coding agents. Chad (the admiral) reads this view to know
what each captain is doing, leaves notes when he wants to steer, and only
intervenes when a captain escalates.

Not a marketing page. Not a generic dashboard. This is the cockpit.

## Mental model (read this first)

```
Admiral (Chad)              Captain (LLM)              Goose-runner (per app)
   │                              │                              │
   └─ writes admiral_notes ──────►│                              │
                                  │                              │
                                  ├─ replan / dispatch ─────────►│
                                  │                              ├─ runs goose
                                  │◄────── reads completion ─────┤
                                  ├─ validates (rubric + scorecard)
                                  ├─ accept / reject / replan
                                  └─ writes captain_log.jsonl
```

- The **admiral** is one human. There is no multi-tenant. Skip account /
  team / settings UI entirely.
- A **captain** is a per-app LLM that supervises one repository. There are
  ~2-10 captains in the fleet at steady state.
- An **app** is a tracked repository with a `mode`:
  - `autonomous` — captain dispatches goose; full slice loop. e.g. `author-toolkit`
  - `observe_only` — captain watches and replans, admiral edits manually.
    e.g. `spark-of-defiance` (it's a manuscript, not a codebase)
- A **slice** is a single unit of work. The captain dispatches one slice at
  a time per app. Verdicts: `accept`, `soft_accept`, `reject_retry`,
  `reject_hard`, `revert`, `kill_replan`, `escalate`.

The admiral spends most time on L1 (overview), drills into L2 only when
something needs attention, and into L3 only when validating slice evidence.

## Where this lives

**Repo:** `chad-fleet` at `/Users/chadsimon/code/chad-fleet`
**App:** `apps/chad-dashboard` (Next.js 14, React 18, Tailwind, vitest)
**Route:** new top-level route `/captain` — do NOT touch the existing
`/apps`, `/inbox`, `/views`, or `/` (chat) routes. Those wire to a
different backend (`state-aggregator`).

Add a nav link "Captain" to `app/layout.tsx`. Otherwise additive only.

## Backend contract (READ-ONLY for you — do NOT modify)

The captain API runs on **http://localhost:8109** (separate process from
state-aggregator). Set env `NEXT_PUBLIC_CAPTAIN_URL` with that as default;
proxy through Next API routes (`app/api/captain/...`) to avoid CORS,
mirroring the pattern in `app/api/state/route.ts`.

### Endpoints you consume

| Method | Path | Returns |
|--------|------|---------|
| GET    | `/health` | `{ ok, fleet_base, registered_apps }` |
| GET    | `/apps` | `{ count, apps: [{ app_id }] }` |
| GET    | `/apps/{id}` | bundle: current_slice + roadmap + captain_log_tail + progress_tail + unread_admiral_notes |
| GET    | `/apps/{id}/roadmap` | full Roadmap |
| GET    | `/apps/{id}/log?limit=N` | captain log entries |
| GET    | `/apps/{id}/scorecard?repo_path=P` | live Scorecard with extras |
| GET    | `/apps/{id}/research` | cached AppProfile (or 404) |
| POST   | `/apps/{id}/note` | `{ body, expects_response }` → writes admiral note |
| POST   | `/apps/{id}/replan` | `{ trigger, repo_path, refresh_research, no_llm }` → returns new Roadmap |
| POST   | `/apps/{id}/tick` | `{ repo_path }` → runs one captain tick |

### Pydantic shapes (TypeScript types you must mirror)

Source of truth: `apps/chad-captain/src/chad_captain/protocol.py` +
`scorecard.py` + `research/`. The full schemas:

```typescript
type CaptainVerdict =
  | 'accept' | 'soft_accept' | 'reject_retry' | 'reject_hard'
  | 'revert' | 'kill_replan' | 'escalate';

type SliceStatus = 'queued' | 'in_flight' | 'done' | 'skipped' | 'blocked';

interface CurrentSlice {
  slice_id: string;
  app_id: string;
  objective: string;
  system_prompt: string;
  user_prompt: string;
  repo_path: string;
  max_turns: number;
  max_tool_repetitions: number;
  timeout_seconds: number;
  started_at: string | null;
  deadline: string | null;
  issued_at: string;
  expected_rubric_categories: string[];
  parent_slice_id: string | null;  // non-null = this is a retry
}

interface RoadmapSlice {
  slice_id: string;
  objective: string;
  phase: string;
  estimated_minutes: number;
  blocked_by: string[];
  status: SliceStatus;
  notes: string;
}

interface Roadmap {
  app_id: string;
  generated_at: string;
  generated_by: 'initial' | 'replanner' | 'manual';
  objective_summary: string;
  slices: RoadmapSlice[];
}

interface CaptainLogEntry {
  ts: string;
  app_id: string;
  slice_id: string | null;
  kind: 'validate' | 'replan' | 'dispatch' | 'stall_detected'
       | 'note_received' | 'note_response' | 'escalation_raised'
       | 'escalation_resolved';
  verdict: CaptainVerdict | null;
  rubric_delta_pp: number | null;
  rationale: string;
  references: Record<string, unknown>;
}

interface ProgressEvent {
  ts: string;
  slice_id: string;
  kind: 'slice_started' | 'tool_call' | 'tool_result'
       | 'stdout_chunk' | 'heartbeat' | 'slice_completing' | 'slice_aborted';
  detail: Record<string, unknown>;
}

interface DimensionScore {
  name: string;       // e.g. "tests_present", "voice_guide_intact"
  score: number;      // 0..1
  rationale: string;
  detail: Record<string, unknown>;
}

interface Scorecard {
  repo_path: string;
  dimensions: DimensionScore[];
  aggregate: number;  // 0..1
}

interface AppProfile {
  app_id: string;
  generated_at: string;
  local: {
    repo_path: string;
    name: string;
    has_readme: boolean;
    readme_excerpt: string;
    top_dirs: string[];
    manifests: Record<string, string>;
    languages: Record<string, number>;
    recent_commits: { sha: string; date: string; author: string; subject: string }[];
    notes: string[];
  };
  web: {
    status: 'ok' | 'skipped' | 'error';
    reason: string;
    landscape_md: string;  // markdown
    model: string;
  };
  summary: string;
}

interface AppStateBundle {
  app_id: string;
  current_slice: CurrentSlice | null;
  roadmap: Roadmap | null;
  captain_log_tail: CaptainLogEntry[];   // last 20
  progress_tail: ProgressEvent[];        // last 10
  unread_admiral_notes: string[];        // filenames
}
```

## What the views must do

### L1 — Fleet overview (`/captain`)

Default landing. Card per app (~2-10 cards). Each card shows at a glance:

- App name + ID + mode badge (autonomous / observe_only)
- **Current activity:** if `current_slice` exists, show its slice_id +
  objective (truncated) + how long it's been in flight (since
  `started_at`). If no current_slice, show "idle" + last log entry's
  rationale.
- **Last verdict:** the most recent `validate` entry's verdict, with a
  pp-delta if present. Verdict gets a color:
  - `accept` → green
  - `soft_accept` → yellow-ish
  - `reject_retry` → orange
  - `reject_hard` / `revert` / `kill_replan` → red
  - `escalate` → bright red, badge "NEEDS YOU"
- **Scorecard aggregate:** 0.00-1.00 with a tiny sparkline if you can
  compute it from log history (optional — skip if it complicates).
- **Unread admiral notes count** (small badge).
- **Roadmap progress:** "3/7 slices done" inline.

Click a card → L2.

The L1 must scale. If there are 0 apps, show a friendly empty state with
the registration command. If there are 12 apps, the grid still works.

Polling cadence: refresh L1 every **15 seconds**. Show a tiny "updated 3s
ago" indicator.

### L2 — App detail (`/captain/[app_id]`)

Three panes side-by-side on wide screens, stacked on narrow:

**Pane A — Current slice + progress (left, ~40% width)**
- Current slice card (objective, started_at, max_turns, parent_slice_id
  badge if it's a retry).
- Progress event stream (`progress_tail`): tool calls, heartbeats, with
  timestamps. Auto-scroll to bottom. Truncate long detail blobs with a
  click-to-expand.
- If no current_slice: show "idle since X" + last completed slice's
  summary.

**Pane B — Roadmap + captain log (center, ~30% width)**
- Roadmap as an ordered list. Each slice shows: slice_id, status badge,
  objective (truncate), and `blocked_by` arrows visually. Done slices
  collapsed; queued/in_flight expanded.
- Below it: captain log tail. Each entry shows `kind`, `verdict` (with
  color), `rationale`. Clickable entries expand to show `references`.

**Pane C — Admiral console (right, ~30% width)**
- **Note input:** textarea + submit button. Posts to
  `POST /apps/{id}/note`. After successful POST, clear input + show toast
  "note delivered". The note will appear in `unread_admiral_notes` until
  the captain consumes it on next tick.
- **Action buttons:**
  - "Replan" — opens a small dialog asking trigger (default: `manual`),
    refresh_research toggle, no_llm toggle. POSTs to `/replan`. On
    success, the L2 view should refetch state.
  - "Tick now" — POST to `/tick`. Useful for kicking the captain
    immediately instead of waiting for the launchd schedule.
- **Notes thread:** list of past notes with their captain responses
  (the API doesn't surface this yet; for now show only the unread queue.
  Mark a TODO comment so we can add a `/notes` endpoint later).
- **Scorecard pane:** call `/scorecard?repo_path=...` and render each
  dimension as a row: name, score bar (0..1), rationale. Group baseline
  vs app-extra dimensions visually if you can detect them (the seven
  baselines are well-known names — see types). Repo path comes from the
  registered app — for now, accept it as a query param the user can
  override; default to the path embedded in current_slice.repo_path or
  empty.

Polling cadence: 5 seconds when the tab is visible.

### L3 — Slice evidence (`/captain/[app_id]/slice/[slice_id]`)

When the admiral wants to see exactly what a slice did. Shows:

- Slice metadata (objective, parent_slice_id, issued_at, started_at)
- Captain log entries scoped to this slice_id (chronological)
- Progress events scoped to this slice_id (chronological, full detail)
- A diff viewer if `slice_complete.diff_path` is in the log entry's
  `references`. Render as a `<pre>` block with monospace; don't pull in
  a heavyweight diff library yet.

This is the "why did the captain do this" view. Should be navigable
from L2 by clicking a roadmap slice or log entry.

## Visual constraints

The existing dashboard at `/Users/chadsimon/code/chad-fleet/apps/chad-dashboard`
is dark, monospace, terminal-aesthetic. Match it. Read these files to
absorb the look:

- `app/layout.tsx` (nav + body shell)
- `app/apps/page.tsx` (card grid pattern — copy the structure)
- `app/apps/[id]/page.tsx` (detail view pattern)
- `app/globals.css` (Tailwind base)

Specifically:
- **Color palette:** gray-950 bg, gray-900 cards, gray-800 borders,
  gray-100 primary text, gray-400 secondary, gray-500 tertiary.
- **Accents:** green-300 / yellow-300 / red-300 on -900 bg with -700
  border for badges (see existing STATE_CLASSES dict).
- **Font:** monospace everywhere except headings which use the default
  sans (see `font-mono` class on body).
- **Density:** tight. Borders 1px. Rounded `rounded` (= 0.25rem) not
  `rounded-lg`. No shadows. No gradients.
- **No icons unless you bring lucide-react and add it to package.json.**
  Text + emoji-free badges work fine for v1.

## Stack constraints

- **Framework:** Next.js 14 App Router. Server Components for initial
  data fetch; client components (`'use client'`) only for the things
  that poll or have state.
- **Styling:** Tailwind v3. No new UI libs. No shadcn. No headlessui.
- **Tests:** vitest with happy-dom (already wired). Add at least one
  test per new page that asserts the empty state and the populated
  state. Pattern: read existing `tests/` dir.
- **Type safety:** strict TypeScript. Mirror the Pydantic types into
  `lib/captainTypes.ts` (don't pollute existing `lib/types.ts`).
- **Data fetching:** `fetch()` in server components for initial render;
  `setInterval` polling in client components with cleanup on unmount.
  No SWR, no react-query, no websockets.
- **API proxy:** All client-side fetches go through `/api/captain/...`
  Next routes that proxy to `localhost:8109`. Don't expose the captain
  port to the browser directly. Mirror `app/api/state/route.ts`.

## Non-goals (do NOT build these)

- Auth / login / session — single-user local tool.
- LinkedIn / social media share buttons.
- Charts/graphs beyond simple progress bars (the sparkline is optional).
- Mobile-first responsive — desktop-primary, tolerable on iPad. Phone
  is out of scope; the admiral isn't scrolling captain log on a phone.
- Notification toasts beyond the basic "note delivered" / "replan
  queued" success flashes.
- Editing slices / roadmap directly. The admiral steers via notes only.
- A settings page. Everything that's configurable lives in
  `~/.chad/captain/apps_registry.json` (Chad edits manually) or env
  vars.

## Acceptance checklist

When you're done:

- [ ] `/captain` renders a card per registered app. Empty state when zero
  apps. Tested.
- [ ] Click-through to `/captain/[app_id]` works for at least
  `spark-of-defiance` and `author-toolkit`.
- [ ] L2 three-pane layout renders even when current_slice is null
  (idle apps).
- [ ] Admiral note input POSTs successfully and shows confirmation.
- [ ] Replan button triggers `/replan` and refreshes the view.
- [ ] Polling refreshes L1 every 15s and L2 every 5s without flicker.
- [ ] Verdict color-coding matches the rubric (accept green, escalate
  bright red, etc.).
- [ ] L3 slice evidence page renders log + progress for a given
  slice_id.
- [ ] `pnpm test` passes (or whatever the existing test command is —
  check `package.json` scripts).
- [ ] `pnpm typecheck` clean.
- [ ] No new top-level deps added beyond what's already in package.json
  (unless you really need lucide-react for icons — fine to add).

## How to verify locally

The captain API is real and runs:

```bash
cd /Users/chadsimon/code/chad-fleet/apps/chad-captain
uv run chad-captain-api --port 8109
```

The fleet has two seeded workspaces ready to view:

- `spark-of-defiance` (observe_only, current_slice=null, has roadmap)
- `author-toolkit` (autonomous, current_slice exists from earlier test
  dispatch)

```bash
cd /Users/chadsimon/code/chad-fleet/apps/chad-dashboard
pnpm dev    # or `npm run dev`
open http://localhost:3000/captain
```

If the API is down, every captain endpoint falls through with an empty
state — your L1 should display "captain API unreachable" instead of
crashing.

## Quick read-list (in order)

1. This brief (you're here).
2. `apps/chad-captain/README.md` — full backend ops doc, role diagram,
   per-app workspace layout.
3. `apps/chad-captain/src/chad_captain/protocol.py` — every shape the
   API returns. Mirror these.
4. `apps/chad-captain/src/chad_captain/api.py` — exact endpoint
   handlers. Confirms request/response shapes.
5. `apps/chad-dashboard/app/apps/page.tsx` — existing card-grid pattern
   to match.
6. `apps/chad-dashboard/app/apps/[id]/page.tsx` — existing detail-page
   pattern.

That's it. Code the dashboard. The captain is the hard part and it's
already done.
