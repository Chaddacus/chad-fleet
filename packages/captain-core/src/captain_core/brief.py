"""Daily brief composition."""

from __future__ import annotations

from datetime import UTC, datetime

from state_aggregator import FleetState

from captain_core.actions import next_actions
from captain_core.stalls import detect_stalls
from captain_core.types import Brief, Playbook, StallAlert


def compose_daily_brief(
    state: FleetState,
    playbooks: dict[str, Playbook],
    *,
    use_llm: bool = False,
) -> Brief:
    """
    Compose a daily brief from fleet state and loaded playbooks.

    Args:
        state: Current fleet snapshot.
        playbooks: Slug-keyed dict of loaded Playbook objects.
        use_llm: If True, post-process body through voice-drafter.
                 Currently a no-op placeholder for a future slice.

    Returns:
        A Brief with deterministic content.

    Note:
        LLM integration (use_llm=True) is a future slice.
        TODO: integrate voice-drafter Drafter once that slice ships.
    """
    stalls = detect_stalls(state)
    actions = next_actions(state, playbooks)

    headline = _build_headline(stalls)
    apps_summary = _build_apps_summary(state, stalls, actions)
    body = _build_body(state, stalls, actions, apps_summary)

    # use_llm=True placeholder — LLM polish is a future slice
    if use_llm:
        # TODO: integrate voice-drafter Drafter here once that package ships.
        # drafter = Drafter()
        # body = drafter.polish(body)
        pass

    return Brief(
        generated_at=datetime.now(UTC),
        headline=headline,
        body=body,
        apps_summary=apps_summary,
        stalls=stalls,
        next_actions=actions,
        recommended_slices=[],
        inbox_recent_count=len(state.inbox_recent),
    )


def _build_headline(stalls: list[StallAlert]) -> str:
    critical = [s for s in stalls if s.severity == "critical"]
    warn = [s for s in stalls if s.severity == "warn"]
    info = [s for s in stalls if s.severity == "info"]

    parts: list[str] = []
    if critical:
        parts.append(f"{len(critical)} critical stall{'s' if len(critical) > 1 else ''}")
    if warn:
        parts.append(f"{len(warn)} warning{'s' if len(warn) > 1 else ''}")
    if info:
        parts.append(f"{len(info)} info alert{'s' if len(info) > 1 else ''}")

    if not parts:
        return "Fleet nominal — no stalls detected"

    summary = ", ".join(parts)
    # Append most severe stall detail
    worst = stalls[0]  # already sorted critical-first
    detail = f"{worst.app_name} blocked {worst.days_since_progress} day(s)"
    return f"{summary}: {detail}"


def _build_apps_summary(state: FleetState, stalls: list[StallAlert], actions) -> list[dict]:
    stall_by_app = {s.app_id: s for s in stalls}
    action_by_app: dict[str, str] = {}
    for a in actions:
        if a.app_id not in action_by_app:
            action_by_app[a.app_id] = a.title

    summaries: list[dict] = []
    for app in state.apps:
        stall = stall_by_app.get(app.id)
        top_action = action_by_app.get(app.id, "")
        summaries.append(
            {
                "app_id": app.id,
                "app_name": app.name,
                "state": app.state,
                "mode": app.mode,
                "last_progress_days": _days_since_now(app.last_progress_at),
                "stall_severity": stall.severity if stall else None,
                "top_action": top_action,
            }
        )
    return summaries


def _build_body(
    state: FleetState,
    stalls: list[StallAlert],
    actions,
    apps_summary: list[dict],
) -> str:
    lines: list[str] = []

    lines.append(f"Daily brief generated at {state.generated_at.strftime('%Y-%m-%d %H:%M UTC')}.")
    lines.append(f"Fleet: {len(state.apps)} app(s) tracked.")
    lines.append("")

    if not stalls:
        lines.append("No stalls detected. All apps are making progress within threshold.")
    else:
        critical = [s for s in stalls if s.severity == "critical"]
        warn = [s for s in stalls if s.severity == "warn"]
        if critical:
            lines.append(f"Critical stalls ({len(critical)}):")
            for s in critical:
                lines.append(f"  - {s.app_name}: {s.detail}")
        if warn:
            lines.append(f"Warnings ({len(warn)}):")
            for s in warn:
                lines.append(f"  - {s.app_name}: {s.detail}")
    lines.append("")

    lines.append("App status:")
    for summary in apps_summary:
        stall_tag = f" [{summary['stall_severity'].upper()}]" if summary["stall_severity"] else ""
        action_tag = f" Next: {summary['top_action']}" if summary["top_action"] else ""
        lines.append(
            f"  {summary['app_name']} ({summary['state']}, {summary['last_progress_days']}d ago){stall_tag}{action_tag}"
        )
    lines.append("")

    if actions:
        lines.append(f"Top {len(actions)} recommended action(s):")
        for i, a in enumerate(actions, 1):
            lines.append(f"  {i}. [{a.app_id}] {a.title}")
            lines.append(f"     {a.rationale}")
    else:
        lines.append("No specific actions recommended at this time.")

    return "\n".join(lines)


def _days_since_now(ts) -> int:
    from datetime import timezone
    now = datetime.now(timezone.utc)
    ts_aware = ts.replace(tzinfo=timezone.utc) if ts.tzinfo is None else ts
    return max(0, (now - ts_aware).days)
