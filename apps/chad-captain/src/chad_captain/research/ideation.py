"""Phase B: feature backlog auto-ideation.

Converts an :class:`AppProfile` (LocalProfile + WebProfile + summary) plus the
already-shipped backlog into a fresh list of candidate features. Phase A
required Chad to seed the backlog manually via the CLI; Phase B does the
seeding from research evidence so the captain can self-direct on what to
build next.

The flow:

    AppProfile + WebProfile + scorecard + already-shipped titles
    → claude_json (Opus) → ranked FeatureBacklogItem candidates

The output is dedup'd against the existing backlog (token-overlap with
queued AND shipped) and merged into ``feature_backlog.json``. Existing
queued items are preserved unless explicitly subsumed; the LLM doesn't
delete or re-rank existing items, only proposes additions.

Triggered manually via ``chad-captain ideate --app <id>`` or
automatically by the daemon on a weekly cadence (or on saturation).
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from chad_captain.llm import LLMError, claude_json
from chad_captain.protocol import (
    AppWorkspace,
    FeatureBacklog,
    FeatureBacklogItem,
    read_feature_backlog,
    write_feature_backlog,
)
from chad_captain.research.synthesize import AppProfile

logger = logging.getLogger(__name__)


IDEATION_SYSTEM = (
    "You are doing product ideation for a software project's autonomous "
    "captain. Your job: identify NEW concrete features the captain should "
    "build next, derived from the project's positioning, the competitive "
    "landscape, and what comparable tools ship. You are NOT inventing "
    "filler — every candidate must be grounded in either a competitor "
    "feature parity gap, a clear domain need, or a stated risk. Be "
    "specific: name what the feature is, why it matters, and how big it "
    "is in slices. Skip anything already shipped or already in the queued "
    "backlog. If the well is genuinely dry, return fewer items rather than "
    "padding."
)


IDEATION_SCHEMA = {
    "type": "object",
    "required": ["candidates"],
    "properties": {
        "candidates": {
            "type": "array",
            "minItems": 0,
            "maxItems": 10,
            "items": {
                "type": "object",
                "required": ["title", "rationale", "priority", "estimated_slice_count"],
                "properties": {
                    "title": {
                        "type": "string",
                        "description": (
                            "≤80-char human-readable feature title, no "
                            "code paths or implementation details"
                        ),
                    },
                    "rationale": {
                        "type": "string",
                        "description": (
                            "Why this feature matters: cite competitor "
                            "parity, domain need, or risk mitigation. "
                            "1-3 sentences."
                        ),
                    },
                    "priority": {
                        "type": "number",
                        "minimum": 0.0,
                        "maximum": 1.0,
                    },
                    "estimated_slice_count": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 8,
                    },
                    "competitive_evidence": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": (
                            "URLs, product names, or excerpts that "
                            "ground this feature in observed reality"
                        ),
                    },
                },
            },
        },
        "saturation_note": {
            "type": "string",
            "description": (
                "If the well is dry (≤2 candidates) explain why; otherwise "
                "leave empty"
            ),
        },
    },
}


def _build_ideation_prompt(
    profile: AppProfile,
    *,
    queued_titles: list[str],
    shipped_titles: list[str],
    scorecard_weak_dims: list[str] | None = None,
) -> str:
    """Assemble the ideation prompt from research artifacts."""
    lines: list[str] = []
    lines.append(f"# App: {profile.app_id}")
    lines.append("")
    lines.append("## Project summary")
    lines.append(profile.summary or "(no summary)")
    lines.append("")
    if profile.local.languages:
        langs = ", ".join(
            f"{k}:{v}" for k, v in list(profile.local.languages.items())[:6]
        )
        lines.append(f"Languages: {langs}")
        lines.append("")
    if profile.web.status == "ok" and profile.web.landscape_md:
        lines.append("## Competitive landscape (from prior research)")
        lines.append(profile.web.landscape_md[:3000])
        lines.append("")
    if scorecard_weak_dims:
        lines.append("## Scorecard weakness areas (lowest scoring dimensions)")
        for d in scorecard_weak_dims[:5]:
            lines.append(f"- {d}")
        lines.append("")
    if queued_titles:
        lines.append("## Already in the backlog (queued — DO NOT propose duplicates)")
        for t in queued_titles[:30]:
            lines.append(f"- {t}")
        lines.append("")
    if shipped_titles:
        lines.append("## Already shipped (DO NOT propose duplicates)")
        for t in shipped_titles[:30]:
            lines.append(f"- {t}")
        lines.append("")
    lines.append(
        "Produce 3-8 NEW feature candidates this captain should add to "
        "its backlog. Anchor each in the competitive landscape or a "
        "concrete domain gap. Be honest about saturation: if you cannot "
        "identify ≥3 grounded candidates beyond what's listed above, "
        "return fewer items and explain in `saturation_note`."
    )
    return "\n".join(lines)


def ideate_features(
    ws: AppWorkspace,
    profile: AppProfile,
    *,
    scorecard_weak_dims: list[str] | None = None,
    model: str = "opus",
    timeout: int = 120,
) -> tuple[list[FeatureBacklogItem], str]:
    """Run the ideation LLM call against ``profile`` + existing backlog.

    Returns ``(new_items, saturation_note)``. ``new_items`` are NOT yet
    persisted — caller decides whether to merge. Caller should also dedup
    against the existing backlog before writing.

    Failures (LLM unavailable, malformed JSON) → ``([], reason)`` so the
    caller can surface the issue without crashing the daemon.
    """
    backlog = read_feature_backlog(ws)
    queued_titles = [i.title for i in backlog.queued()]
    shipped_titles = [i.title for i in backlog.shipped()]
    prompt = _build_ideation_prompt(
        profile,
        queued_titles=queued_titles,
        shipped_titles=shipped_titles,
        scorecard_weak_dims=scorecard_weak_dims,
    )
    try:
        data = claude_json(
            prompt, IDEATION_SCHEMA,
            system=IDEATION_SYSTEM, model=model, timeout=timeout,
        )
    except LLMError as e:
        logger.warning("ideation LLM call failed: %s", e)
        return [], f"llm_error: {e}"

    saturation = str(data.get("saturation_note") or "").strip()
    raw_candidates = data.get("candidates") or []
    out: list[FeatureBacklogItem] = []
    next_id = backlog.next_id()
    for raw in raw_candidates:
        title = str(raw.get("title") or "").strip()
        if not title:
            continue
        try:
            priority = float(raw.get("priority", 0.5) if raw.get("priority") is not None else 0.5)
            raw_est = raw.get("estimated_slice_count")
            est = int(raw_est) if raw_est is not None else 2
        except (TypeError, ValueError):
            continue
        priority = max(0.0, min(1.0, priority))
        est = max(1, min(8, est))
        out.append(FeatureBacklogItem(
            id=next_id,
            title=title[:120],
            rationale=str(raw.get("rationale") or "")[:1000],
            priority=priority,
            estimated_slice_count=est,
            source="auto-ideation",
            competitive_evidence=[
                str(x) for x in (raw.get("competitive_evidence") or [])
            ][:5],
        ))
        # advance id for the next candidate
        n = int(next_id.split("-", 1)[1]) + 1
        next_id = f"fb-{n:03d}"

    return out, saturation


def merge_candidates_into_backlog(
    ws: AppWorkspace,
    candidates: list[FeatureBacklogItem],
    *,
    dedup_threshold: float = 0.6,
) -> tuple[int, int]:
    """Append ``candidates`` to the persisted backlog, skipping any that
    duplicate an existing queued/shipped item by token overlap.

    Returns ``(added_count, skipped_count)``.
    """
    if not candidates:
        return 0, 0
    backlog = read_feature_backlog(ws)
    existing = backlog.items
    added = 0
    skipped = 0
    for cand in candidates:
        if any(
            _title_similarity(cand.title, e.title) >= dedup_threshold
            for e in existing
        ):
            skipped += 1
            continue
        # Reassign id off live backlog state to avoid collisions when
        # the file mutated mid-flight.
        cand.id = backlog.next_id()
        cand.created_at = datetime.now(timezone.utc).isoformat()
        backlog.items.append(cand)
        existing = backlog.items  # next dedup must see this new entry
        added += 1
    if added:
        write_feature_backlog(ws, backlog)
    return added, skipped


def _title_similarity(a: str, b: str) -> float:
    """Cheap Jaccard token overlap; same shape as validator's ship-mark."""
    import re
    stops = {
        "a", "an", "and", "the", "to", "of", "for", "in", "on", "at", "by",
        "with", "as", "is", "are", "be", "or", "feature", "add", "build",
        "ship", "implement", "create", "make", "new", "update", "support",
    }
    def toks(s: str) -> set[str]:
        cleaned = re.sub(r"[^a-z0-9 ]", " ", s.lower())
        return {t for t in cleaned.split() if len(t) >= 3 and t not in stops}
    ta = toks(a)
    tb = toks(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


__all__ = [
    "ideate_features",
    "merge_candidates_into_backlog",
    "_build_ideation_prompt",
    "_title_similarity",
]
