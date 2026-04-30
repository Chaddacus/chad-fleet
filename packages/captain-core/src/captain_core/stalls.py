"""Stall detection: identify apps with no recent progress."""

from __future__ import annotations

from datetime import UTC, datetime

from state_aggregator import AppSnapshot, FleetState

from captain_core.thresholds import DEFAULT_MODE, STALL_THRESHOLDS
from captain_core.types import StallAlert


def detect_stalls(
    state: FleetState,
    thresholds: dict | None = None,
) -> list[StallAlert]:
    """
    Scan fleet state and return a StallAlert for each app that has stalled.

    Args:
        state: Current fleet snapshot.
        thresholds: Optional override dict. Keys are mode strings; values are
                    (warn_days, critical_days) tuples. None entries mean no alert at
                    that severity. Falls back to STALL_THRESHOLDS for any missing key.

    Returns:
        List of StallAlert, one per stalled app. Archived/shipped apps are omitted
        unless explicitly blocked.
    """
    merged = dict(STALL_THRESHOLDS)
    if thresholds:
        merged.update(thresholds)

    now = datetime.now(UTC)
    alerts: list[StallAlert] = []

    for app in state.apps:
        alert = _evaluate_app(app, now, merged)
        if alert is not None:
            alerts.append(alert)

    # Sort: critical first, then warn, then info; within severity by days desc
    _SEV_ORDER = {"critical": 0, "warn": 1, "info": 2}
    alerts.sort(key=lambda a: (_SEV_ORDER[a.severity], -a.days_since_progress))
    return alerts


def _days_since(ts: datetime, now: datetime) -> int:
    delta = now - ts.replace(tzinfo=UTC) if ts.tzinfo is None else now - ts
    return max(0, delta.days)


def _evaluate_app(
    app: AppSnapshot,
    now: datetime,
    thresholds: dict,
) -> StallAlert | None:
    mode = app.mode if app.mode else DEFAULT_MODE
    warn_days, critical_days = thresholds.get(mode, thresholds.get(DEFAULT_MODE, (3, 7)))

    days = _days_since(app.last_progress_at, now)

    # Blocked apps always emit at least info
    if app.state == "blocked":
        severity = "info"
        if critical_days is not None and days >= critical_days:
            severity = "critical"
        elif warn_days is not None and days >= warn_days:
            severity = "warn"
        detail = (
            f"App is blocked: {app.blocked_reason or 'no reason given'}. "
            f"{days} day(s) since last progress."
        )
        return StallAlert(
            app_id=app.id,
            app_name=app.name,
            days_since_progress=days,
            severity=severity,
            detail=detail,
        )

    # Archived / shipped: never stall
    if warn_days is None and critical_days is None:
        return None

    # launch_driven: thresholds are proximity-aware — only apply within 60 days of
    # expected launch. If no launch_date in metadata, fall back to continuous.
    if mode == "launch_driven":
        return _evaluate_launch_driven(app, days, now, warn_days, critical_days)

    # Standard threshold check
    if critical_days is not None and days >= critical_days:
        return StallAlert(
            app_id=app.id,
            app_name=app.name,
            days_since_progress=days,
            severity="critical",
            detail=f"No progress in {days} day(s) (threshold: {critical_days} days, mode: {mode}).",
        )
    if warn_days is not None and days >= warn_days:
        return StallAlert(
            app_id=app.id,
            app_name=app.name,
            days_since_progress=days,
            severity="warn",
            detail=f"No progress in {days} day(s) (threshold: {warn_days} days, mode: {mode}).",
        )
    return None


def _evaluate_launch_driven(
    app: AppSnapshot,
    days: int,
    now: datetime,
    warn_days: int | None,
    critical_days: int | None,
) -> StallAlert | None:
    """
    launch_driven mode: stall thresholds tighten as launch date approaches.

    - Within 7 days of launch: warn>=1, critical>=2
    - Within 14 days of launch: warn>=1, critical>=2 (same tights)
    - Beyond 14 days: use continuous defaults (3/7)

    If no launch_date metadata is set, fall back to continuous thresholds.
    """
    launch_date_str: str | None = app.metadata.get("launch_date")
    if not launch_date_str:
        # Fall back to continuous defaults
        cont_warn, cont_crit = STALL_THRESHOLDS.get("continuous", (3, 7))
        if cont_crit is not None and days >= cont_crit:
            return StallAlert(
                app_id=app.id,
                app_name=app.name,
                days_since_progress=days,
                severity="critical",
                detail=f"No progress in {days} day(s) (launch_driven mode, no launch date set).",
            )
        if cont_warn is not None and days >= cont_warn:
            return StallAlert(
                app_id=app.id,
                app_name=app.name,
                days_since_progress=days,
                severity="warn",
                detail=f"No progress in {days} day(s) (launch_driven mode, no launch date set).",
            )
        return None

    try:
        launch_dt = datetime.fromisoformat(launch_date_str).replace(tzinfo=UTC)
    except ValueError:
        return None

    days_to_launch = (launch_dt - now).days

    if days_to_launch <= 14:
        # Tight thresholds near launch
        eff_warn = warn_days if warn_days is not None else 1
        eff_crit = critical_days if critical_days is not None else 2
    else:
        # Far from launch — use continuous defaults
        eff_warn, eff_crit = STALL_THRESHOLDS.get("continuous", (3, 7))

    if eff_crit is not None and days >= eff_crit:
        return StallAlert(
            app_id=app.id,
            app_name=app.name,
            days_since_progress=days,
            severity="critical",
            detail=(
                f"No progress in {days} day(s) with launch in {days_to_launch} day(s) "
                f"(launch_driven critical threshold: {eff_crit} days)."
            ),
        )
    if eff_warn is not None and days >= eff_warn:
        return StallAlert(
            app_id=app.id,
            app_name=app.name,
            days_since_progress=days,
            severity="warn",
            detail=(
                f"No progress in {days} day(s) with launch in {days_to_launch} day(s) "
                f"(launch_driven warn threshold: {eff_warn} days)."
            ),
        )
    return None
