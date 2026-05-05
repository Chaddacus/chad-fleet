"""Replanner — produces a fresh Roadmap when the captain runs out of work.

Inputs:
    - App research profile (from chad_captain.research)
    - Latest scorecard (chad_captain.scorecard with extras)
    - Recent captain log entries (verdicts, dispatches)
    - Unconsumed admiral notes
Output:
    - Roadmap with 3-7 concrete, actionable slices

Replan triggers (caller's responsibility):
    - No roadmap on file (initial bootstrap)
    - next_queued_slice returns None (roadmap exhausted)
    - Two soft_accepts in a row (low-yield streak)
    - Admiral note with intent="replan"

The actual slice generation goes through ``claude_json`` (Pro/Max
subscription, no API key). On any LLM failure the replanner falls back
to a deterministic skeleton roadmap so the captain doesn't sit idle.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

from pydantic import BaseModel, Field, ValidationError

from tracked_app_registry.storage import append_jsonl, read_jsonl

from chad_captain.extras import get_extras
from chad_captain.llm import LLMError, claude_json
from chad_captain.protocol import (
    AdmiralNote,
    AppWorkspace,
    Roadmap,
    RoadmapSlice,
    consume_admiral_note,
    list_unread_admiral_notes,
    read_captain_log,
    read_feature_backlog,
    read_roadmap,
    write_roadmap,
)
from chad_captain.research import AppProfile, load_profile, synthesize_profile
from chad_captain.scorecard import Scorecard, score_repo

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Reasons / models
# ---------------------------------------------------------------------------


REPLAN_TRIGGERS = (
    "initial",
    "exhausted",
    "soft_accept_streak",
    "admiral_note",
    "manual",
    # PR2 R3-MED-3: T1 Spark publish phase trigger. Lets admiral mark a
    # replan as "we're now in publish mode" so the replanner prompt can
    # pivot from drafting to launch.
    "publish",
)


# PR7 R3#7 — replan rate limit + drained-replan sanity helpers.
#
# Hard cap on replans per captain per rolling hour. Without this a runaway
# trigger loop (e.g. roadmap drained → replan → all skipped → drained →
# replan ...) burns LLM spend with nothing to show for it. 5/h is generous
# for healthy captains and tight enough to surface pathological loops.
REPLAN_RATE_LIMIT_PER_HOUR = 5

# Jaccard threshold above which a fresh roadmap is treated as a near-duplicate
# of the prior roadmap (drained-replan sanity). 0.8 lets normal incremental
# planning through (some shape overlap is expected) but blocks "the LLM
# regenerated the same 5 slices verbatim and we just drained them all."
REPLAN_DUPLICATE_JACCARD_THRESHOLD = 0.8


class ReplanRateLimited(RuntimeError):
    """Raised when replan() is called more than REPLAN_RATE_LIMIT_PER_HOUR
    times in the trailing hour. Caller (daemon, CLI) should log + skip
    rather than propagate; admiral can override via `chad-captain replan
    --force` (records a `manual` trigger and bypasses the limit)."""


class ReplanDuplicate(RuntimeError):
    """Raised when the freshly-generated roadmap shape closely matches the
    roadmap we just drained — implies the captain is stuck in a loop the
    LLM cannot break out of. Caller surfaces this to admiral via the
    captain log + escalation rather than silently retrying."""


def _slice_shape_signature(slc: RoadmapSlice) -> str:
    """Deterministic 16-hex-char fingerprint of a slice's *shape* — phase
    + objective normalized to lowercase, whitespace-collapsed, with task
    nouns ('add', 'fix', 'update', 'wire', etc.) stripped so two slices
    that say "Add foo endpoint" / "add the foo endpoint" hash identically.

    Used for drained-replan sanity (Jaccard overlap of slice shapes
    between consecutive roadmaps) and for replan_history audit trails.
    """
    phase = (slc.phase or "").strip().lower()
    objective = (slc.objective or "").strip().lower()
    # Strip common task verbs/articles that don't change shape meaning.
    objective = re.sub(
        r"\b(add|fix|update|wire|implement|build|create|refactor|hook|introduce|the|a|an)\b",
        "",
        objective,
    )
    # Collapse whitespace AFTER stripping so removed words don't leave double spaces.
    objective = re.sub(r"\s+", " ", objective).strip()
    text = f"{phase}|{objective}"
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _check_replan_rate_limit(
    ws: AppWorkspace,
    *,
    now: datetime | None = None,
    cap: int = REPLAN_RATE_LIMIT_PER_HOUR,
) -> None:
    """Raise ReplanRateLimited if the captain has replanned > cap times in
    the trailing hour. Callers that want to bypass this (admiral force,
    initial bootstrap) skip the check entirely.

    History is stored in a plain JSONL appended via the same atomic
    helper used for captain_log, so concurrent daemon + CLI replans
    cannot lose entries.
    """
    if not ws.replan_history_path.exists():
        return
    cutoff = (now or datetime.now(timezone.utc)) - timedelta(hours=1)
    cutoff_iso = cutoff.isoformat()
    recent = 0
    for entry in read_jsonl(ws.replan_history_path):
        ts = entry.get("ts", "")
        if ts and ts >= cutoff_iso:
            recent += 1
    if recent >= cap:
        raise ReplanRateLimited(
            f"app={ws.app_id}: {recent} replans in the last hour (cap={cap}); "
            f"refusing to replan again before {(cutoff + timedelta(hours=1)).isoformat()}"
        )


def _record_replan(
    ws: AppWorkspace,
    *,
    trigger: str,
    roadmap: Roadmap,
) -> None:
    """Append one entry to replan_history.jsonl. Captures the slice-shape
    set so future drained-replan checks can compare without re-reading
    the prior roadmap.json (which gets overwritten on the next replan).
    """
    ws.root.mkdir(parents=True, exist_ok=True)
    append_jsonl(
        ws.replan_history_path,
        {
            "ts": datetime.now(timezone.utc).isoformat(),
            "trigger": trigger,
            "slice_count": len(roadmap.slices),
            "shape_signatures": [_slice_shape_signature(s) for s in roadmap.slices],
        },
    )


def _drained_replan_sanity(
    prior: Roadmap | None,
    fresh: Roadmap,
    *,
    threshold: float = REPLAN_DUPLICATE_JACCARD_THRESHOLD,
) -> None:
    """Raise ReplanDuplicate if `fresh` is structurally near-identical to
    `prior`. Compares Jaccard overlap of slice-shape signatures.

    Skipped when prior is None (initial bootstrap) or either roadmap is
    empty (caller must handle empty-fresh elsewhere).
    """
    if prior is None or not prior.slices or not fresh.slices:
        return
    prior_sigs = {_slice_shape_signature(s) for s in prior.slices}
    fresh_sigs = {_slice_shape_signature(s) for s in fresh.slices}
    union = prior_sigs | fresh_sigs
    if not union:
        return
    overlap = len(prior_sigs & fresh_sigs) / len(union)
    if overlap >= threshold:
        raise ReplanDuplicate(
            f"app={fresh.app_id}: fresh roadmap shape overlap with prior "
            f"is {overlap:.2f} (threshold={threshold:.2f}); "
            f"prior_slices={len(prior.slices)} fresh_slices={len(fresh.slices)} "
            f"shared_signatures={sorted(prior_sigs & fresh_sigs)}"
        )


class ReplanContext(BaseModel):
    """Bundle of inputs the replanner consumes."""

    trigger: str
    profile: AppProfile
    scorecard: Scorecard
    recent_decisions: list[dict] = Field(default_factory=list)
    admiral_notes: list[str] = Field(default_factory=list)
    code_inventory: dict = Field(default_factory=dict)
    backlog_queued: list[dict] = Field(default_factory=list)  # top N queued features
    backlog_shipped: list[str] = Field(default_factory=list)  # recent shipped titles


REPLAN_SCHEMA = {
    "type": "object",
    "required": ["objective_summary", "slices"],
    "properties": {
        "objective_summary": {"type": "string"},
        "slices": {
            "type": "array",
            "minItems": 1,
            "maxItems": 8,
            "items": {
                "type": "object",
                "required": ["slice_id", "objective", "title"],
                "properties": {
                    "slice_id": {"type": "string"},
                    "objective": {"type": "string"},
                    "title": {
                        "type": "string",
                        "description": (
                            "Human-readable headline ≤80 chars, no file paths "
                            "or code identifiers. Plain English describing what "
                            "this slice does (e.g. 'Add billing entitlements "
                            "API endpoint' or 'Shrink launch ops service file')."
                        ),
                    },
                    "phase": {"type": "string"},
                    "estimated_minutes": {"type": "integer"},
                    "blocked_by": {"type": "array", "items": {"type": "string"}},
                },
            },
        },
    },
}


REPLAN_SYSTEM = (
    "You are the captain replanning a roadmap for an autonomous coding fleet. "
    "Your job is to emit 3-7 concrete, surgical slices a downstream coding "
    "agent will execute one at a time. Each slice must satisfy ALL of these "
    "rules:\n"
    "1. Surgical: ≤ 100 LOC and ≤ 3 files of churn.\n"
    "2. Independently testable: the slice is done when a specific test, "
    "scorecard dimension, or runtime check passes.\n"
    "3. Self-contained: the objective is a single paragraph the agent can "
    "execute without further clarification.\n"
    "4. Sequenced via blocked_by — earlier slices unblock later ones; do NOT "
    "create a single mega-slice.\n"
    "Reject vague objectives like 'improve X' or 'refactor Y' — every slice "
    "names the file(s), function(s), or behavior to change.\n"
    "Each slice MUST also include a `title` — a ≤80-char plain-English "
    "headline for dashboard display. The title is what a human glances at "
    "to know what's happening. NO file paths, NO code identifiers, NO "
    "module names. Examples: 'Add billing entitlements API endpoint', "
    "'Shrink launch ops service file', 'Wire tier-gated permission check'. "
    "The verbose `objective` is for the coding agent; the `title` is for "
    "the human watching the dashboard."
)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def replan(
    ws: AppWorkspace,
    repo_path: str | Path,
    *,
    trigger: str = "manual",
    refresh_research: bool = False,
    use_llm: bool = True,
    log_history_limit: int = 20,
    force: bool = False,
    enforce_duplicate_check: bool = True,
) -> Roadmap:
    """Produce a fresh Roadmap for ``ws``. Persists it to ``ws.roadmap_path``.

    PR7 R3#7: rate-limit and drained-replan-sanity gates. ``force=True``
    bypasses the rate limit (admiral override). ``enforce_duplicate_check``
    can be set False for the very first roadmap of a captain (initial
    bootstrap is allowed to look like the seed roadmap).
    """
    if trigger not in REPLAN_TRIGGERS:
        raise ValueError(f"unknown trigger {trigger!r}; one of {REPLAN_TRIGGERS}")

    # Rate limit BEFORE any LLM call — burning $$ on the LLM and then
    # tossing the result is worse than skipping the replan entirely.
    if not force and trigger != "initial":
        _check_replan_rate_limit(ws)

    # Snapshot prior roadmap for the drained-replan sanity check below.
    prior_roadmap = read_roadmap(ws) if enforce_duplicate_check else None

    profile = (
        synthesize_profile(ws, repo_path, refresh=True)
        if refresh_research
        else (load_profile(ws) or synthesize_profile(ws, repo_path))
    )
    scorecard = score_repo(repo_path, extras=get_extras(ws.app_id))

    captain_log = read_captain_log(ws, limit=log_history_limit)
    recent_decisions = [
        {
            "ts": e.ts,
            "kind": e.kind,
            "verdict": e.verdict or "",
            "rationale": e.rationale,
        }
        for e in captain_log
    ]

    notes_with_paths = _collect_admiral_notes(ws)
    notes = [body for body, _ in notes_with_paths]
    inventory = _build_code_inventory(repo_path)
    backlog = read_feature_backlog(ws)
    backlog_queued = [
        {
            "id": i.id,
            "title": i.title,
            "rationale": i.rationale,
            "priority": i.priority,
            "estimated_slice_count": i.estimated_slice_count,
            "source": i.source,
        }
        for i in backlog.queued(top=8)
    ]
    backlog_shipped = [i.title for i in backlog.shipped(last=20)]

    ctx = ReplanContext(
        trigger=trigger,
        profile=profile,
        scorecard=scorecard,
        recent_decisions=recent_decisions,
        admiral_notes=notes,
        code_inventory=inventory,
        backlog_queued=backlog_queued,
        backlog_shipped=backlog_shipped,
    )

    roadmap = (
        _llm_roadmap(ctx, app_id=ws.app_id)
        if use_llm
        else _fallback_roadmap(ctx, app_id=ws.app_id)
    )
    roadmap.generated_by = "replanner"
    roadmap.generated_at = datetime.now(timezone.utc).isoformat()

    # PR7 R3#7: drained-replan sanity. If the new roadmap is structurally
    # near-identical to the one we just exhausted, the LLM is stuck and
    # admiral needs to intervene. Record the attempt in replan_history
    # before raising so the audit trail captures the failed loop iteration.
    if enforce_duplicate_check:
        try:
            _drained_replan_sanity(prior_roadmap, roadmap)
        except ReplanDuplicate:
            _record_replan(ws, trigger=f"{trigger}:duplicate", roadmap=roadmap)
            raise

    write_roadmap(ws, roadmap)
    _record_replan(ws, trigger=trigger, roadmap=roadmap)

    # Mark admiral notes as consumed only after roadmap write succeeds.
    # If we crash mid-replan, the notes are still in queue for the next attempt.
    from chad_captain.protocol import (
        CaptainLogEntry, append_captain_log,
    )
    for _body, path in notes_with_paths:
        note_id = path.stem  # e.g. "note-20260501T142914960453"
        try:
            consume_admiral_note(ws, path)
        except OSError as e:
            logger.warning("could not consume admiral note %s: %s", path, e)
            continue
        # Log the note→replan link so the dashboard can show "your note
        # produced this roadmap" without parsing JSONL by hand.
        append_captain_log(
            ws,
            CaptainLogEntry(
                app_id=ws.app_id,
                slice_id=None,
                kind="note_response",
                rationale=(
                    f"note {note_id} consumed by replan trigger={trigger}; "
                    f"new roadmap has {len(roadmap.slices)} slices"
                ),
                references={
                    "note_id": note_id,
                    "trigger": trigger,
                    "roadmap_generated_at": roadmap.generated_at,
                },
            ),
        )

    return roadmap


def replan_if_needed(ws: AppWorkspace, repo_path: str | Path) -> Roadmap | None:
    """Inspect the current roadmap + log and call ``replan`` if a trigger
    applies. Returns the new Roadmap if it ran, else None.

    Saturation gate: if the trigger fires AND the feature backlog has zero
    queued items AND there are no unread admiral notes, pause the app
    with a ``backlog_saturated`` marker instead of generating filler
    work. The pause expires in 24h or when fresh backlog items arrive
    (via ``chad-captain backlog add`` / ``chad-captain ideate`` — both
    auto-clear saturation pauses).
    """
    trigger = _detect_trigger(ws)
    if trigger is None:
        return None
    # Saturation only applies to mid-life apps that have already shipped
    # features. Initial replans (no roadmap yet) and admiral-driven replans
    # always proceed regardless of backlog state.
    if trigger in ("exhausted", "soft_accept_streak") and _is_backlog_saturated(ws):
        _trigger_saturation_pause(ws)
        return None
    return replan(ws, repo_path, trigger=trigger)


def _is_backlog_saturated(ws: AppWorkspace) -> bool:
    """True when the captain has nothing left to ship: backlog queued is
    empty AND no admiral note is steering. Admiral notes always win — if
    Chad sent guidance, replan the roadmap honoring it instead of pausing.
    """
    if list_unread_admiral_notes(ws):
        return False
    backlog = read_feature_backlog(ws)
    return len(backlog.queued()) == 0


def _trigger_saturation_pause(ws: AppWorkspace) -> None:
    """Write a 24h pause file with ``reason='backlog_saturated'`` and emit
    an escalation log entry the dashboard surfaces as 'needs you'.

    Idempotent — re-running on an already-saturated app extends the pause
    horizon but doesn't double-log.
    """
    from datetime import datetime, timedelta, timezone
    from chad_captain.protocol import (
        CaptainLogEntry, append_captain_log, read_captain_log,
    )
    from chad_captain.validator import _write_pause_until
    until = datetime.now(timezone.utc) + timedelta(hours=24)
    _write_pause_until(ws, until.isoformat(), reason="backlog_saturated")

    # Don't double-log: skip if the most recent escalation was already
    # backlog_saturated within the last 6h (lighter dedup than checking
    # every entry — saturation is a slow-moving state).
    recent = read_captain_log(ws, limit=10)
    for e in recent:
        if (
            e.kind == "escalation_raised"
            and (e.references or {}).get("event") == "backlog_saturated"
        ):
            try:
                ts = datetime.fromisoformat(e.ts)
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)
                if datetime.now(timezone.utc) - ts < timedelta(hours=6):
                    return
            except (ValueError, TypeError):
                continue

    backlog = read_feature_backlog(ws)
    shipped = len(backlog.shipped())
    append_captain_log(
        ws,
        CaptainLogEntry(
            app_id=ws.app_id,
            slice_id=None,
            kind="escalation_raised",
            rationale=(
                f"backlog saturated — {shipped} features shipped, 0 queued. "
                "Run `chad-captain ideate --app "
                + f"{ws.app_id} --refresh-research` to refill the backlog, "
                "or send an admiral note to steer."
            ),
            references={"event": "backlog_saturated", "shipped_count": str(shipped)},
        ),
    )


# ---------------------------------------------------------------------------
# Trigger detection
# ---------------------------------------------------------------------------


def _detect_trigger(ws: AppWorkspace) -> str | None:
    rm = read_roadmap(ws)
    if rm is None:
        return "initial"
    has_queued = any(s.status == "queued" for s in rm.slices)
    if not has_queued:
        return "exhausted"

    log = read_captain_log(ws, limit=4)
    soft_accepts = [e for e in log if e.kind == "validate" and e.verdict == "soft_accept"]
    if len(soft_accepts) >= 2 and all(
        e.kind == "validate" and e.verdict == "soft_accept" for e in log[-2:]
    ):
        return "soft_accept_streak"

    # Any unread admiral note triggers a replan — Chad sent direction,
    # captain acts on it. Previously we required the literal word "replan"
    # in the body, which silently dropped natural-language notes like
    # "look at test density and file size health".
    if list_unread_admiral_notes(ws):
        return "admiral_note"
    return None


def _collect_admiral_notes(
    ws: AppWorkspace, *, limit: int = 5
) -> list[tuple[str, Path]]:
    """Return up to ``limit`` unread admiral notes as ``(body, path)`` tuples.

    Path is preserved so the caller can move successfully-consumed notes to
    ``admiral_notes/consumed/`` after the roadmap write succeeds.
    """
    notes_paths = list_unread_admiral_notes(ws)[-limit:]
    out: list[tuple[str, Path]] = []
    for p in notes_paths:
        try:
            note = AdmiralNote.model_validate_json(p.read_text())
        except (OSError, ValidationError) as e:
            logger.warning("could not parse admiral note %s: %s", p, e)
            continue
        out.append((note.body, p))
    return out


# ---------------------------------------------------------------------------
# LLM path
# ---------------------------------------------------------------------------


def _llm_roadmap(ctx: ReplanContext, *, app_id: str) -> Roadmap:
    prompt = _build_prompt(ctx)
    try:
        data = claude_json(
            prompt,
            REPLAN_SCHEMA,
            model="opus",
            system=REPLAN_SYSTEM,
            timeout=180,
        )
    except LLMError as e:
        logger.warning("replanner LLM call failed (%s); falling back to skeleton", e)
        return _fallback_roadmap(ctx, app_id=app_id)

    # PR6/v8: derive task_id to stamp on every generated slice.
    # Captains scaffolded by Twin have a backlog where every item shares
    # one task_id. Pre-Twin captains have no task_id (None on every item).
    # Strategy: pick the most common non-None task_id among queued items;
    # None if no item carries one. All emitted slices get this task_id so
    # the eventual CaptainLogEntry can be filtered by task in close.
    derived_task_id: str | None = None
    if ctx.backlog_queued:
        from collections import Counter
        task_id_counts = Counter(
            item.get("task_id") for item in ctx.backlog_queued
            if isinstance(item, dict) and item.get("task_id")
        )
        if task_id_counts:
            derived_task_id = task_id_counts.most_common(1)[0][0]

    slices: list[RoadmapSlice] = []
    for raw in data.get("slices", []):
        try:
            slices.append(RoadmapSlice(
                slice_id=str(raw["slice_id"]),
                objective=str(raw["objective"]),
                title=_clean_title(raw.get("title"), str(raw["objective"])),
                phase=str(raw.get("phase") or ""),
                estimated_minutes=int(raw.get("estimated_minutes") or 30),
                blocked_by=list(raw.get("blocked_by") or []),
                task_id=derived_task_id,
            ))
        except (KeyError, ValueError) as e:
            logger.warning("dropping malformed slice %r: %s", raw, e)
            continue
    if not slices:
        return _fallback_roadmap(ctx, app_id=app_id)

    return Roadmap(
        app_id=app_id,
        slices=slices,
        objective_summary=str(data.get("objective_summary") or ""),
    )


_TITLE_MAX_CHARS = 80
_PHASE_PREFIX_RE = re.compile(
    r"^(FEATURE|REMEDIATION|HOUSEKEEPING|FIX|TEST|REFACTOR|CHORE|DOCS)\s*:\s*",
    re.IGNORECASE,
)


def _clean_title(raw: object, objective_fallback: str) -> str:
    """Return a sanitized headline for dashboard display.

    Falls back to the first sentence of the objective (truncated) when the
    LLM omits or fumbles the title field.
    """
    candidate = str(raw or "").strip()
    if not candidate:
        candidate = _derive_title_from_objective(objective_fallback)
    candidate = _PHASE_PREFIX_RE.sub("", candidate).strip()
    if len(candidate) > _TITLE_MAX_CHARS:
        candidate = candidate[: _TITLE_MAX_CHARS - 1].rstrip() + "…"
    return candidate


def _derive_title_from_objective(objective: str) -> str:
    """Pull a one-line headline out of the verbose slice objective."""
    text = objective.strip()
    # First sentence (split on period+space, ignoring code paths).
    for sep in (". ", ".\n", "\n"):
        idx = text.find(sep)
        if 0 < idx <= 120:
            text = text[:idx]
            break
    return text.strip()


def _build_code_inventory(repo_path: str | Path) -> dict:
    """Survey the repo's existing modules so the planner doesn't generate
    slices that build parallel modules to ones that already exist.

    Live failure mode that motivated this: PR #142 shipped a top-level
    `billing/` Plan/Subscription module while `apps/billing/` already
    existed with a Stripe webhook stub. Captain didn't see the existing
    package and built a parallel one. New `billing/` models are not
    wired to the existing webhook → Stripe pings stay no-op.

    Returns ``{"top_level_dirs": [...], "django_apps": [...],
    "service_modules": [...], "view_modules": [...]}``. Lightweight —
    walks 3 levels and a curated set of names. Excludes vendor/.venv etc.

    All paths are repo-relative."""
    repo = Path(repo_path).expanduser().resolve()
    out: dict[str, list[str]] = {
        "top_level_dirs": [],
        "django_apps": [],
        "service_modules": [],
        "view_modules": [],
    }
    if not repo.is_dir():
        return out

    SKIP = {
        ".git", ".venv", "venv", "node_modules", "__pycache__",
        ".pytest_cache", ".mypy_cache", ".ruff_cache", "dist", "build",
        "target", ".next", ".turbo", "out", "coverage", ".cache",
        ".idea", ".vscode", ".artifacts", "vendor",
    }

    # Top-level dirs (1 level deep, alphabetical, capped).
    try:
        entries = sorted(p.name for p in repo.iterdir() if p.is_dir() and p.name not in SKIP and not p.name.startswith("."))
        out["top_level_dirs"] = entries[:25]
    except OSError:
        pass

    # Django apps: directory containing models.py with a non-abstract
    # Django Model class. Same heuristic the rubric uses, surfaced here
    # for the planner.
    apps_root = repo / "apps"
    candidates = [apps_root] if apps_root.is_dir() else []
    candidates.extend(p for p in repo.iterdir() if p.is_dir() and p.name not in SKIP and not p.name.startswith("."))

    seen: set[str] = set()
    for root in candidates:
        try:
            for child in root.iterdir():
                if not child.is_dir() or child.name in SKIP or child.name.startswith("."):
                    continue
                models_py = child / "models.py"
                if models_py.exists():
                    try:
                        text = models_py.read_text(encoding="utf-8", errors="replace")[:8_000]
                    except OSError:
                        continue
                    if re.search(r"\bmodels\.Model\b", text):
                        rel = str(child.relative_to(repo))
                        if rel not in seen:
                            out["django_apps"].append(rel)
                            seen.add(rel)
        except OSError:
            continue

    # Service / view modules — single-file presence is enough signal.
    for pat in ("**/services/*.py", "**/services.py"):
        for p in repo.glob(pat):
            if any(part in SKIP for part in p.parts):
                continue
            if p.name == "__init__.py":
                continue
            rel = str(p.relative_to(repo))
            out["service_modules"].append(rel)
    for pat in ("**/views.py", "**/views/*.py", "**/api/views.py", "**/api/viewsets/*.py"):
        for p in repo.glob(pat):
            if any(part in SKIP for part in p.parts):
                continue
            if p.name == "__init__.py":
                continue
            rel = str(p.relative_to(repo))
            out["view_modules"].append(rel)

    out["django_apps"] = sorted(set(out["django_apps"]))[:30]
    out["service_modules"] = sorted(set(out["service_modules"]))[:30]
    out["view_modules"] = sorted(set(out["view_modules"]))[:30]
    return out


def _rubric_is_stalled(recent_decisions: list[dict]) -> bool:
    """Heuristic: if the last 4 validate entries are all soft_accept with
    abs(delta_pp) < 0.5, the rubric isn't responding to the work being
    done. Return True so the prompt pivots the next batch toward
    feature work instead of more remediation."""
    validates = [
        d for d in recent_decisions if d.get("kind") == "validate"
    ]
    if len(validates) < 4:
        return False
    tail = validates[-4:]
    for d in tail:
        if d.get("verdict") != "soft_accept":
            return False
        # rationale carries delta info as string '+0.41pp' / '+0.00pp'.
        # If we can't parse it, conservatively don't flag stall.
        rat = d.get("rationale") or ""
        m = re.search(r"([+-]?\d+\.\d+)\s*pp", rat)
        if not m:
            return False
        try:
            if abs(float(m.group(1))) >= 0.5:
                return False
        except ValueError:
            return False
    return True


def _build_prompt(ctx: ReplanContext) -> str:
    p = ctx.profile
    sc = ctx.scorecard
    lines: list[str] = []
    lines.append(f"App: {p.app_id}")
    lines.append(f"Replan trigger: {ctx.trigger}")
    lines.append("")
    lines.append("## Project summary")
    lines.append(p.summary or "(no summary)")
    lines.append("")
    if p.local.languages:
        langs = ", ".join(f"{k}:{v}" for k, v in list(p.local.languages.items())[:6])
        lines.append(f"Languages: {langs}")
    lines.append("")
    lines.append("## Scorecard (lowest-scoring dimensions first)")
    sorted_dims = sorted(sc.dimensions, key=lambda d: d.score)
    for d in sorted_dims[:6]:
        lines.append(f"- {d.name} = {d.score:.2f}: {d.rationale}")
    lines.append(f"Aggregate: {sc.aggregate:.2f}")
    lines.append("")
    if ctx.recent_decisions:
        lines.append("## Recent captain decisions (most recent last)")
        for d in ctx.recent_decisions[-8:]:
            lines.append(f"- {d['ts']} {d['kind']} {d['verdict']}: {d['rationale']}")
        lines.append("")
    if ctx.admiral_notes:
        lines.append("## Admiral notes (treat as direct steering input)")
        for note in ctx.admiral_notes:
            lines.append(f"- {note}")
        lines.append("")
    if ctx.backlog_queued:
        lines.append("## Feature backlog — queued items (highest priority first)")
        lines.append(
            "These are the curated next features for this app. PREFER picking "
            "FEATURE slices from this list over inventing new ones. The id, "
            "title, and rationale are stable; cite the id in slice titles "
            "(e.g. 'Cover A/B testing — POST endpoint [fb-001]') so the "
            "captain can mark items shipped after merge."
        )
        for item in ctx.backlog_queued:
            est = item.get("estimated_slice_count") or 2
            src = item.get("source") or "manual"
            rat = item.get("rationale") or ""
            lines.append(
                f"- [{item['id']}] (priority {item['priority']:.2f}, ~{est} slices, "
                f"source={src}) {item['title']}"
                + (f" — {rat[:200]}" if rat else "")
            )
        lines.append("")
    if ctx.backlog_shipped:
        lines.append("## Already-shipped features — DO NOT propose duplicates")
        for title in ctx.backlog_shipped:
            lines.append(f"- {title}")
        lines.append(
            "If your candidate FEATURE slice substantially overlaps any of "
            "the above, drop it and pick a different backlog item."
        )
        lines.append("")
    if p.web.status == "ok" and p.web.landscape_md:
        lines.append("## Competitive landscape (excerpt)")
        lines.append(p.web.landscape_md[:1200])
        lines.append("")

    inv = ctx.code_inventory or {}
    if inv:
        lines.append("## Existing code inventory — REUSE these, do not parallel")
        lines.append(
            "Before generating any FEATURE slice that creates a new "
            "package or module, check this list. If the same domain "
            "(billing, tenants, launch_ops, arc, etc.) already has a "
            "module here, EXTEND it rather than creating a parallel "
            "package elsewhere in the tree. Live failure: a previous "
            "cycle shipped top-level `billing/` while `apps/billing/` "
            "already existed; the new module's models were never wired "
            "to the existing webhook handler. Don't repeat this."
        )
        if inv.get("django_apps"):
            lines.append("")
            lines.append("Django apps with existing models.py:")
            for a in inv["django_apps"]:
                lines.append(f"- {a}")
        if inv.get("service_modules"):
            lines.append("")
            lines.append("Existing service modules:")
            for s in inv["service_modules"][:20]:
                lines.append(f"- {s}")
        if inv.get("view_modules"):
            lines.append("")
            lines.append("Existing view modules:")
            for v in inv["view_modules"][:20]:
                lines.append(f"- {v}")
        lines.append("")

    # Add cycle-progress context. If trailing rubric deltas have been
    # tiny, the lowest-scoring dim is likely insensitive to incremental
    # work — pivot the next batch toward feature/product work.
    rubric_stalled = _rubric_is_stalled(ctx.recent_decisions)
    if rubric_stalled:
        lines.append("## ⚠ Rubric stall detected")
        lines.append(
            "The last several validates have rubric_delta_pp ≈ 0. The "
            "lowest-scoring dim isn't moving. Do NOT generate another "
            "round of remediation slices targeting the same dim — that "
            "loop has already been tried and the rubric is insensitive "
            "to it. Instead, this batch should be majority FEATURE work "
            "(building the product described in the summary above) plus "
            "at most ONE remediation slice."
        )
        lines.append("")

    lines.append(
        "Produce 3-7 surgical slices. Mix work types so the cycle "
        "ships both product progress AND code-health gains:\n"
        "  - At least 1-2 FEATURE slices that advance the product "
        "described in the summary (new endpoints, new UI surface, "
        "new domain logic, new CLI flag, etc).\n"
        "  - 1-2 remediation slices targeting the lowest-scoring "
        "dimensions (file_size_health, test_density, "
        "migrations_consistent, etc).\n"
        "  - At most 1 housekeeping slice (docs, config, lint).\n"
        "Each slice's slice_id must be unique within this roadmap "
        "(e.g. S1, S2, S3). When sequencing "
        "matters, set blocked_by to the prerequisite slice_id(s)."
    )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Deterministic fallback (works without the LLM)
# ---------------------------------------------------------------------------


def _fallback_roadmap(ctx: ReplanContext, *, app_id: str) -> Roadmap:
    """Deterministic skeleton roadmap derived from scorecard weak spots.

    Used when the LLM call fails or is disabled. Each weak dimension yields
    one obvious remediation slice. Captain still gets work to do.
    """
    sc = ctx.scorecard
    weak = [d for d in sc.dimensions if d.score < 0.7]
    if not weak:
        # Healthy scorecard — schedule a smoke-tests slice to keep activity going.
        return Roadmap(
            app_id=app_id,
            objective_summary="Healthy scorecard; smoke tests + dependency refresh",
            slices=[
                RoadmapSlice(slice_id="S1", title="Run full test suite", objective="Run full test suite and ensure 100% pass; fix any newly failing tests in place"),
                RoadmapSlice(slice_id="S2", title="Bump one outdated dependency", objective="Audit and bump one outdated direct dependency to its latest minor version", blocked_by=["S1"]),
            ],
        )
    slices: list[RoadmapSlice] = []
    for i, d in enumerate(weak[:5], start=1):
        objective = _objective_for_weak_dim(d.name, d.rationale)
        slices.append(RoadmapSlice(
            slice_id=f"S{i}",
            objective=objective,
            title=_clean_title(None, objective),
            phase="compliance",
            estimated_minutes=45,
            blocked_by=[f"S{i-1}"] if i > 1 else [],
        ))
    return Roadmap(
        app_id=app_id,
        objective_summary=f"Fallback plan: lift {len(slices)} weak scorecard dimension(s) above 0.7",
        slices=slices,
    )


def _objective_for_weak_dim(name: str, rationale: str) -> str:
    obj_map = {
        "tests_present": "Add a tests/ directory with at least one pytest-style test file covering the most-changed source module; aim for ≥1 test file per 5 source files.",
        "tests_recent": "Touch the existing test suite — add 2-3 new test cases for the behavior most recently changed (per git log) and commit.",
        "todo_pressure": "Resolve or remove 5 TODO/FIXME/XXX/HACK markers (whichever are 1-line fixes); leave a one-sentence rationale comment for any deferred ones.",
        "skip_pressure": "Pick the oldest @pytest.mark.skip in the test suite, fix the underlying issue, remove the skip marker.",
        "secret_hygiene": "Remove any hardcoded secrets/keys from non-test source files. Replace with env-var lookup; add a placeholder to .env.example.",
        "file_size_health": "Take the largest source file and split one cohesive group of functions/classes into a sibling module. ≤ 100 LOC moved per slice.",
        "docs_present": "Add a README.md (one screen) covering: what this is, how to run it, where the entrypoint is.",
        "voice_guide_intact": "Restore VOICE_GUIDE.md from git history or recreate from the most recent draft chapter's voice samples.",
        "chapters_word_count_target": "Open the shortest chapter; expand it by ~500 words while staying within the [1500, 6000] word band.",
        "sentinel_present": "Add a .sentinel file written by the daily run script as proof-of-life; commit the heartbeat-write step.",
        "typescript_typecheck_clean": "Fix the first 3 TypeScript errors reported by `npx tsc --noEmit`; commit each fix as a separate change.",
        "captain_test_count_growing": "Add 5 new captain unit tests covering an edge case currently uncovered (search for branches without test_ files).",
    }
    return obj_map.get(
        name,
        f"Address scorecard finding for '{name}': {rationale}. Make the smallest correct change.",
    )


__all__ = [
    "REPLAN_TRIGGERS",
    "ReplanContext",
    "replan",
    "replan_if_needed",
]
