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

import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path

from pydantic import BaseModel, Field, ValidationError

from chad_captain.extras import get_extras
from chad_captain.llm import LLMError, claude_json
from chad_captain.protocol import (
    AdmiralNote,
    AppWorkspace,
    Roadmap,
    RoadmapSlice,
    list_unread_admiral_notes,
    read_captain_log,
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
)


class ReplanContext(BaseModel):
    """Bundle of inputs the replanner consumes."""

    trigger: str
    profile: AppProfile
    scorecard: Scorecard
    recent_decisions: list[dict] = Field(default_factory=list)
    admiral_notes: list[str] = Field(default_factory=list)


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
                "required": ["slice_id", "objective"],
                "properties": {
                    "slice_id": {"type": "string"},
                    "objective": {"type": "string"},
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
    "names the file(s), function(s), or behavior to change."
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
) -> Roadmap:
    """Produce a fresh Roadmap for ``ws``. Persists it to ``ws.roadmap_path``."""
    if trigger not in REPLAN_TRIGGERS:
        raise ValueError(f"unknown trigger {trigger!r}; one of {REPLAN_TRIGGERS}")

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

    notes = _collect_admiral_notes(ws)

    ctx = ReplanContext(
        trigger=trigger,
        profile=profile,
        scorecard=scorecard,
        recent_decisions=recent_decisions,
        admiral_notes=notes,
    )

    roadmap = (
        _llm_roadmap(ctx, app_id=ws.app_id)
        if use_llm
        else _fallback_roadmap(ctx, app_id=ws.app_id)
    )
    roadmap.generated_by = "replanner"
    roadmap.generated_at = datetime.now(timezone.utc).isoformat()
    write_roadmap(ws, roadmap)
    return roadmap


def replan_if_needed(ws: AppWorkspace, repo_path: str | Path) -> Roadmap | None:
    """Inspect the current roadmap + log and call ``replan`` if a trigger
    applies. Returns the new Roadmap if it ran, else None."""
    trigger = _detect_trigger(ws)
    if trigger is None:
        return None
    return replan(ws, repo_path, trigger=trigger)


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

    notes = list_unread_admiral_notes(ws)
    for n in notes:
        try:
            data = json.loads(n.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        if "replan" in (data.get("body") or "").lower():
            return "admiral_note"
    return None


def _collect_admiral_notes(ws: AppWorkspace, *, limit: int = 5) -> list[str]:
    notes_paths = list_unread_admiral_notes(ws)[-limit:]
    out: list[str] = []
    for p in notes_paths:
        try:
            note = AdmiralNote.model_validate_json(p.read_text())
        except (OSError, ValidationError) as e:
            logger.warning("could not parse admiral note %s: %s", p, e)
            continue
        out.append(note.body)
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

    slices: list[RoadmapSlice] = []
    for raw in data.get("slices", []):
        try:
            slices.append(RoadmapSlice(
                slice_id=str(raw["slice_id"]),
                objective=str(raw["objective"]),
                phase=str(raw.get("phase") or ""),
                estimated_minutes=int(raw.get("estimated_minutes") or 30),
                blocked_by=list(raw.get("blocked_by") or []),
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
    if p.web.status == "ok" and p.web.landscape_md:
        lines.append("## Competitive landscape (excerpt)")
        lines.append(p.web.landscape_md[:1200])
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
                RoadmapSlice(slice_id="S1", objective="Run full test suite and ensure 100% pass; fix any newly failing tests in place"),
                RoadmapSlice(slice_id="S2", objective="Audit and bump one outdated direct dependency to its latest minor version", blocked_by=["S1"]),
            ],
        )
    slices: list[RoadmapSlice] = []
    for i, d in enumerate(weak[:5], start=1):
        objective = _objective_for_weak_dim(d.name, d.rationale)
        slices.append(RoadmapSlice(
            slice_id=f"S{i}",
            objective=objective,
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
