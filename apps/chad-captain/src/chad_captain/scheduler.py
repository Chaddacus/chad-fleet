"""Pure asyncio sleep-until-next-cron-tick scheduler.

No system cron, no APScheduler dep. Computes seconds-to-next-tick in
the configured timezone. Weekday filtering skips Saturday/Sunday when
weekdays_only=True.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo


def next_tick(
    now: datetime,
    hour: int,
    tz: str,
    weekdays_only: bool,
) -> float:
    """Return seconds until the next scheduled tick.

    If *now* is already past today's target hour (in the target timezone),
    advances to the next calendar day. If *weekdays_only* is True, skips
    Saturday (weekday 5) and Sunday (weekday 6).

    Args:
        now: Current time (timezone-aware or naive UTC).
        hour: Target hour (0-23) in *tz*.
        tz: IANA timezone name (e.g. "America/New_York").
        weekdays_only: Skip Saturday and Sunday when True.

    Returns:
        Non-negative float: seconds until the next tick.
    """
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)

    target_tz = ZoneInfo(tz)
    local = now.astimezone(target_tz)

    target = local.replace(hour=hour, minute=0, second=0, microsecond=0)
    if target <= local:
        target += timedelta(days=1)

    while weekdays_only and target.weekday() >= 5:
        target += timedelta(days=1)

    delta = (target - local).total_seconds()
    return max(delta, 0.0)


def is_weekday(now: datetime, tz: str = "America/New_York") -> bool:
    """Return True if *now* falls on a weekday in *tz*."""
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    return now.astimezone(ZoneInfo(tz)).weekday() < 5
