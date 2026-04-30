"""Next-action generation: playbook-grounded, deterministic recommendations."""

from __future__ import annotations

import re

from state_aggregator import AppSnapshot, FleetState

from captain_core.playbooks import find_playbooks_for_app
from captain_core.stalls import detect_stalls
from captain_core.thresholds import NEXT_ACTIONS_CAP, MIN_TRIGGER_SCORE
from captain_core.types import NextAction, Playbook, StallAlert


def next_actions(
    state: FleetState,
    playbooks: dict[str, Playbook],
    cap: int = NEXT_ACTIONS_CAP,
) -> list[NextAction]:
    """
    Generate ordered next-action recommendations from playbook triggers and stall alerts.

    Logic:
    1. For each app, find matching playbooks.
    2. Score each playbook trigger (when_to_consult bullet) by keyword overlap with app metadata.
    3. Emit a NextAction per high-relevance trigger whose recommendation paragraph is available.
    4. Prepend stall-derived actions for every critical StallAlert (priority 1).
    5. Sort by priority asc; return top `cap`.
    """
    stalls = detect_stalls(state)
    stall_by_app: dict[str, StallAlert] = {s.app_id: s for s in stalls}

    actions: list[NextAction] = []

    # Stall-derived critical actions first (priority 1)
    for stall in stalls:
        if stall.severity == "critical":
            actions.append(
                NextAction(
                    app_id=stall.app_id,
                    title=f"Resolve stall: {stall.app_name}",
                    body=(
                        f"{stall.detail} This app has been inactive for {stall.days_since_progress} "
                        f"day(s). Identify the blocker and restore momentum immediately."
                    ),
                    rationale="stall-detection: critical threshold exceeded",
                    priority=1,
                    playbook_slug=None,
                )
            )

    # Playbook-grounded actions
    priority_counter = 2
    for app in state.apps:
        matched = find_playbooks_for_app(app, playbooks)
        stall = stall_by_app.get(app.id)

        for pb in matched:
            scored = _score_triggers(app, pb)
            for trigger_idx, rec_idx, score in scored:
                if score < MIN_TRIGGER_SCORE:
                    continue
                if rec_idx >= len(pb.recommendations):
                    continue
                rec_text = pb.recommendations[rec_idx]
                trigger_text = pb.when_to_consult[trigger_idx]

                # Elevate priority if app has a stall
                priority = priority_counter
                if stall is not None and stall.severity in ("warn", "critical"):
                    priority = max(1, priority_counter - 1)

                actions.append(
                    NextAction(
                        app_id=app.id,
                        title=_derive_title(rec_text),
                        body=rec_text,
                        rationale=f"playbook:{pb.slug} — trigger: {trigger_text[:80]}",
                        priority=priority,
                        playbook_slug=pb.slug,
                    )
                )
                priority_counter += 1

    # Sort: priority asc, then stall severity desc
    _SEV_NUM = {"critical": 0, "warn": 1, "info": 2, None: 3}

    def _sort_key(a: NextAction) -> tuple:
        stall = stall_by_app.get(a.app_id)
        sev = stall.severity if stall else None
        return (a.priority, _SEV_NUM.get(sev, 3))

    actions.sort(key=_sort_key)

    # Deduplicate same (app_id, playbook_slug, title) to avoid repeats
    seen: set[tuple] = set()
    deduped: list[NextAction] = []
    for a in actions:
        key = (a.app_id, a.playbook_slug, a.title)
        if key not in seen:
            seen.add(key)
            deduped.append(a)

    return deduped[:cap]


def _score_triggers(app: AppSnapshot, pb: Playbook) -> list[tuple[int, int, int]]:
    """
    Return list of (trigger_idx, recommendation_idx, score) tuples.

    Score = count of keyword tokens from the trigger bullet that appear in the
    combined app context string (metadata values, mode, owner_brand, state).
    Recommendation index is estimated as min(trigger_idx, len(recommendations)-1)
    to ensure we always map to a real paragraph.
    """
    app_context = _build_app_context(app)
    results: list[tuple[int, int, int]] = []
    for i, trigger in enumerate(pb.when_to_consult):
        tokens = set(_tokenise(trigger))
        overlap = len(tokens & app_context)
        rec_idx = min(i, max(0, len(pb.recommendations) - 1))
        results.append((i, rec_idx, overlap))
    # Sort by score descending so callers get best-scored first
    results.sort(key=lambda t: -t[2])
    return results


def _build_app_context(app: AppSnapshot) -> set[str]:
    """Build a flat token set from all app-level string fields."""
    parts = [app.mode, app.owner_brand, app.state, app.cadence]
    for v in app.metadata.values():
        if isinstance(v, str):
            parts.append(v)
        elif isinstance(v, list):
            parts.extend(str(i) for i in v)
    combined = " ".join(parts)
    return set(_tokenise(combined))


def _derive_title(rec_text: str) -> str:
    """Extract the bold heading from a recommendation paragraph, or fall back to first line."""
    m = re.search(r"\*\*(.+?)\*\*", rec_text)
    if m:
        return m.group(1)
    first_line = rec_text.splitlines()[0].strip() if rec_text else "Action required"
    return first_line[:80]


def _tokenise(s: str) -> list[str]:
    return [t for t in re.split(r"[-_\s,.;:!?]+", s.lower()) if len(t) > 2]
