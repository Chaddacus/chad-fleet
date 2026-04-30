"""Asyncio daemon: runs runner.run_daily() at the configured schedule.

Loop logic:
  1. Compute seconds to next scheduled tick via scheduler.next_tick().
  2. Sleep until the tick.
  3. If last_run_date != today, call runner.run_daily().
  4. Repeat indefinitely.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from chad_captain.config import CaptainConfig
from chad_captain.runner import CaptainRunner, _today_str
from chad_captain.scheduler import next_tick

logger = logging.getLogger(__name__)


async def _daemon_loop(config: CaptainConfig) -> None:
    runner = CaptainRunner(config)

    while True:
        now = datetime.now(timezone.utc)
        sleep_secs = next_tick(
            now,
            hour=config.schedule_hour,
            tz=config.schedule_tz,
            weekdays_only=config.weekdays_only,
        )
        logger.info("Captain daemon: next tick in %.0f seconds.", sleep_secs)
        await asyncio.sleep(sleep_secs)

        today = _today_str(config.schedule_tz)
        last_run = runner._read_last_run_date()
        if last_run != today:
            logger.info("Captain daemon: running daily pipeline for %s.", today)
            try:
                brief = runner.run_daily()
                logger.info("Captain daemon: brief complete — %s", brief.headline)
            except Exception:  # noqa: BLE001
                logger.exception("Captain daemon: run_daily() raised an exception.")
        else:
            logger.info("Captain daemon: already ran today (%s), skipping.", today)


def start_daemon(config: CaptainConfig) -> asyncio.Task:  # type: ignore[type-arg]
    """Schedule the daemon loop as an asyncio Task.

    Must be called from within a running event loop.
    """
    return asyncio.ensure_future(_daemon_loop(config))


def run_forever(config: CaptainConfig) -> None:
    """Blocking entry point — starts a new event loop and runs the daemon."""
    asyncio.run(_daemon_loop(config))
