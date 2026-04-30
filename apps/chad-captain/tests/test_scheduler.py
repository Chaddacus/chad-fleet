"""Tests for scheduler.next_tick and is_weekday."""

from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from chad_captain.scheduler import is_weekday, next_tick


# 2026-04-29 is a Wednesday in America/New_York
WED_8AM_ET = datetime(2026, 4, 29, 8, 0, tzinfo=ZoneInfo("America/New_York"))
WED_10AM_ET = datetime(2026, 4, 29, 10, 0, tzinfo=ZoneInfo("America/New_York"))
FRI_10AM_ET = datetime(2026, 5, 1, 10, 0, tzinfo=ZoneInfo("America/New_York"))
SAT_10AM_ET = datetime(2026, 5, 2, 10, 0, tzinfo=ZoneInfo("America/New_York"))
SUN_10AM_ET = datetime(2026, 5, 3, 10, 0, tzinfo=ZoneInfo("America/New_York"))


class TestNextTick:
    def test_before_target_hour_today_same_day(self) -> None:
        # 8am, target 9am → 1 hour
        secs = next_tick(WED_8AM_ET, hour=9, tz="America/New_York", weekdays_only=True)
        assert secs == 3600.0

    def test_after_target_hour_rolls_to_next_day(self) -> None:
        # 10am, target 9am → next day 9am, 23 hours away
        secs = next_tick(WED_10AM_ET, hour=9, tz="America/New_York", weekdays_only=True)
        assert secs == 23 * 3600.0

    def test_friday_rolls_past_weekend_when_weekdays_only(self) -> None:
        # Fri 10am, target 9am, weekdays_only → next Mon 9am
        # Fri 10am → Sat 9am (skip) → Sun 9am (skip) → Mon 9am
        # That's 2 days + 23 hours = 71 hours
        secs = next_tick(FRI_10AM_ET, hour=9, tz="America/New_York", weekdays_only=True)
        assert secs == 71 * 3600.0

    def test_friday_does_not_skip_when_weekdays_only_false(self) -> None:
        # Fri 10am, target 9am, NOT weekdays_only → Sat 9am, 23 hours
        secs = next_tick(FRI_10AM_ET, hour=9, tz="America/New_York", weekdays_only=False)
        assert secs == 23 * 3600.0

    def test_saturday_rolls_to_monday_when_weekdays_only(self) -> None:
        # Sat 10am, target 9am, weekdays_only → next Mon 9am
        # Sat 10am → Sun 9am (skip) → Mon 9am
        # 1 day + 23 hours = 47 hours
        secs = next_tick(SAT_10AM_ET, hour=9, tz="America/New_York", weekdays_only=True)
        assert secs == 47 * 3600.0

    def test_naive_datetime_treated_as_utc(self) -> None:
        # 2026-04-29 14:00 UTC = 10am ET (DST), target 9am ET → next day 9am ET = 23 hours
        naive = datetime(2026, 4, 29, 14, 0)
        secs = next_tick(naive, hour=9, tz="America/New_York", weekdays_only=True)
        assert secs == 23 * 3600.0

    def test_returns_non_negative(self) -> None:
        secs = next_tick(WED_8AM_ET, hour=9, tz="America/New_York", weekdays_only=True)
        assert secs >= 0.0

    def test_different_timezone(self) -> None:
        # Wed 14:00 UTC = Wed 06:00 LA. Target 9am LA → 3 hours.
        utc_now = datetime(2026, 4, 29, 13, 0, tzinfo=timezone.utc)
        secs = next_tick(utc_now, hour=9, tz="America/Los_Angeles", weekdays_only=True)
        assert secs == 3 * 3600.0


class TestIsWeekday:
    def test_wednesday_is_weekday(self) -> None:
        assert is_weekday(WED_10AM_ET) is True

    def test_saturday_is_not_weekday(self) -> None:
        assert is_weekday(SAT_10AM_ET) is False

    def test_sunday_is_not_weekday(self) -> None:
        assert is_weekday(SUN_10AM_ET) is False

    def test_naive_datetime_treated_as_utc(self) -> None:
        # Wed 13:00 UTC = Wed 09:00 ET → still Wednesday
        naive = datetime(2026, 4, 29, 13, 0)
        assert is_weekday(naive) is True
