"""Asyncio daemon: runs runner.run_daily() at the configured schedule, AND
runs an autonomous-tick loop that drives every mode=="autonomous" app's
captain_tick on a fixed interval (C6).

Two concurrent loops:
  1. Daily brief loop — fires at config.schedule_hour, builds + emits brief.
  2. Autonomous tick loop — every config.autonomous_tick_interval_seconds,
     iterates registered apps, calls captain_tick(auto_replan=True) for each
     mode=="autonomous" entry. Per-app exceptions are logged but never
     stop the loop.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from chad_captain.apps_registry import RegisteredApp, load_registry
from chad_captain.config import CaptainConfig
from chad_captain.protocol import AppWorkspace
from chad_captain.runner import CaptainRunner, _today_str
from chad_captain.scheduler import next_tick
from chad_captain.validator import captain_tick

logger = logging.getLogger(__name__)


async def _daily_brief_loop(config: CaptainConfig) -> None:
    """Daily-cadence loop: build + emit the captain brief once per day."""
    runner = CaptainRunner(config)

    while True:
        now = datetime.now(timezone.utc)
        sleep_secs = next_tick(
            now,
            hour=config.schedule_hour,
            tz=config.schedule_tz,
            weekdays_only=config.weekdays_only,
        )
        logger.info("Captain daemon: next daily brief in %.0f seconds.", sleep_secs)
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


def tick_autonomous_apps() -> dict[str, str]:
    """Drive one captain_tick for every registered mode=='autonomous' app.

    Returns a map of ``{app_id: status_or_error}`` for diagnostics. Per-app
    exceptions are caught and surfaced as the status string so a single
    bad app cannot stop the loop.

    Pure (no asyncio) so it's straightforward to unit-test and to invoke
    on demand via the CLI / API.
    """
    results: dict[str, str] = {}
    try:
        registry = load_registry()
    except Exception as e:  # noqa: BLE001
        logger.exception("autonomous tick: registry load failed")
        return {"_registry_error": str(e)}

    for app in registry.apps:
        if app.mode != "autonomous":
            continue
        results[app.app_id] = _tick_one(app)
    return results


def _tick_one(app: RegisteredApp) -> str:
    """Drive captain_tick for a single autonomous app. Errors → status string."""
    try:
        ws = AppWorkspace(app.app_id)
        ws.ensure()
        status = captain_tick(
            ws, repo_path=app.repo_path, auto_replan=True,
        )
        return status or "no-op"
    except Exception as e:  # noqa: BLE001
        logger.exception("autonomous tick failed for %s", app.app_id)
        return f"error: {type(e).__name__}: {e}"


async def _autonomous_tick_loop(config: CaptainConfig) -> None:
    """Tick every autonomous-mode app at config.autonomous_tick_interval_seconds.

    Tolerates per-app failures (logged via tick_autonomous_apps) and tolerates
    registry/IO failures by retrying on the next interval. Disable by setting
    autonomous_tick_enabled=False or interval<=0.
    """
    if not config.autonomous_tick_enabled or config.autonomous_tick_interval_seconds <= 0:
        logger.info(
            "Captain daemon: autonomous tick loop disabled "
            "(enabled=%s, interval=%ss)",
            config.autonomous_tick_enabled,
            config.autonomous_tick_interval_seconds,
        )
        return

    interval = config.autonomous_tick_interval_seconds
    logger.info(
        "Captain daemon: autonomous tick loop running every %ds.", interval,
    )
    while True:
        try:
            results = tick_autonomous_apps()
            if results:
                logger.info(
                    "autonomous tick: %s",
                    ", ".join(f"{aid}={st!r}" for aid, st in results.items()),
                )
            else:
                logger.debug("autonomous tick: no autonomous apps registered.")
        except Exception:  # noqa: BLE001
            logger.exception("autonomous tick loop iteration raised; continuing")
        await asyncio.sleep(interval)


async def _daemon_loop(config: CaptainConfig) -> None:
    """Run both the daily-brief loop and the autonomous-tick loop concurrently."""
    await asyncio.gather(
        _daily_brief_loop(config),
        _autonomous_tick_loop(config),
    )


def start_daemon(config: CaptainConfig) -> asyncio.Task:  # type: ignore[type-arg]
    """Schedule the daemon loop as an asyncio Task.

    Must be called from within a running event loop.
    """
    return asyncio.ensure_future(_daemon_loop(config))


def run_forever(config: CaptainConfig) -> None:
    """Blocking entry point — starts a new event loop and runs the daemon."""
    asyncio.run(_daemon_loop(config))
