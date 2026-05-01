"""Tests for chad_captain.daemon — autonomous-tick loop (C6)."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from chad_captain import daemon
from chad_captain.apps_registry import AppsRegistry, RegisteredApp
from chad_captain.config import CaptainConfig


# ---------------------------------------------------------------------------
# tick_autonomous_apps — pure function
# ---------------------------------------------------------------------------


def _registry_with(*apps: RegisteredApp) -> AppsRegistry:
    return AppsRegistry(apps=list(apps))


def test_tick_autonomous_apps_skips_observe_only_mode(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    calls: list[str] = []

    monkeypatch.setattr(
        daemon, "load_registry",
        lambda: _registry_with(
            RegisteredApp(
                app_id="auto", name="Auto", repo_path=str(tmp_path),
                mode="autonomous",
            ),
            RegisteredApp(
                app_id="observe", name="Observe", repo_path=str(tmp_path),
                mode="observe_only",
            ),
        ),
    )
    monkeypatch.setattr(
        daemon, "captain_tick",
        lambda ws, **_kw: calls.append(ws.app_id) or "ticked",
    )
    # AppWorkspace.ensure makes dirs under fleet_base; redirect to tmp.
    monkeypatch.setenv("CHAD_FLEET_BASE", str(tmp_path / "fleet"))

    results = daemon.tick_autonomous_apps()

    assert calls == ["auto"]
    assert results == {"auto": "ticked"}


def test_tick_autonomous_apps_continues_on_per_app_exception(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """One bad app must not stop the loop. Surface error in result map."""
    seen: list[str] = []

    monkeypatch.setattr(
        daemon, "load_registry",
        lambda: _registry_with(
            RegisteredApp(
                app_id="bad", name="Bad", repo_path=str(tmp_path),
                mode="autonomous",
            ),
            RegisteredApp(
                app_id="good", name="Good", repo_path=str(tmp_path),
                mode="autonomous",
            ),
        ),
    )

    def fake_tick(ws, **_kw):
        seen.append(ws.app_id)
        if ws.app_id == "bad":
            raise RuntimeError("kaboom")
        return "ok"

    monkeypatch.setattr(daemon, "captain_tick", fake_tick)
    monkeypatch.setenv("CHAD_FLEET_BASE", str(tmp_path / "fleet"))

    results = daemon.tick_autonomous_apps()

    # Both apps were attempted
    assert sorted(seen) == ["bad", "good"]
    # Bad app surfaced as error string; good app surfaced as status
    assert "error" in results["bad"] and "kaboom" in results["bad"]
    assert results["good"] == "ok"


def test_tick_autonomous_apps_passes_repo_path_and_auto_replan(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    captured: dict = {}

    monkeypatch.setattr(
        daemon, "load_registry",
        lambda: _registry_with(
            RegisteredApp(
                app_id="x", name="X", repo_path="/tmp/repo-x",
                mode="autonomous",
            ),
        ),
    )

    def fake_tick(ws, **kw):
        captured["app_id"] = ws.app_id
        captured["kwargs"] = kw
        return "ok"

    monkeypatch.setattr(daemon, "captain_tick", fake_tick)
    monkeypatch.setenv("CHAD_FLEET_BASE", str(tmp_path / "fleet"))

    daemon.tick_autonomous_apps()

    assert captured["app_id"] == "x"
    assert captured["kwargs"]["repo_path"] == "/tmp/repo-x"
    assert captured["kwargs"]["auto_replan"] is True


def test_tick_autonomous_apps_handles_registry_load_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def boom() -> AppsRegistry:
        raise OSError("disk on fire")
    monkeypatch.setattr(daemon, "load_registry", boom)

    results = daemon.tick_autonomous_apps()
    assert "_registry_error" in results
    assert "disk on fire" in results["_registry_error"]


def test_tick_autonomous_apps_returns_empty_when_no_autonomous_apps(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        daemon, "load_registry",
        lambda: _registry_with(
            RegisteredApp(
                app_id="o", name="O", repo_path=str(tmp_path),
                mode="observe_only",
            ),
        ),
    )
    assert daemon.tick_autonomous_apps() == {}


# ---------------------------------------------------------------------------
# _autonomous_tick_loop — asyncio loop
# ---------------------------------------------------------------------------


def _make_config(*, enabled: bool = True, interval: int = 300) -> CaptainConfig:
    return CaptainConfig(
        playbooks_dir=Path("/tmp/_playbooks_doesnt_matter"),
        autonomous_tick_enabled=enabled,
        autonomous_tick_interval_seconds=interval,
    )


def test_autonomous_tick_loop_short_circuits_when_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """enabled=False → loop returns immediately (does not sleep, does not tick)."""
    ticked: list[int] = []
    monkeypatch.setattr(daemon, "tick_autonomous_apps",
                        lambda: ticked.append(1) or {})

    cfg = _make_config(enabled=False)
    asyncio.run(_run_loop_once(daemon._autonomous_tick_loop(cfg)))

    assert ticked == []


def test_autonomous_tick_loop_short_circuits_when_interval_zero(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ticked: list[int] = []
    monkeypatch.setattr(daemon, "tick_autonomous_apps",
                        lambda: ticked.append(1) or {})

    cfg = _make_config(interval=0)
    asyncio.run(_run_loop_once(daemon._autonomous_tick_loop(cfg)))

    assert ticked == []


def test_autonomous_tick_loop_calls_tick_then_sleeps(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One iteration calls tick_autonomous_apps and then asyncio.sleep(interval)."""
    ticked: list[int] = []
    sleeps: list[float] = []

    monkeypatch.setattr(
        daemon, "tick_autonomous_apps",
        lambda: ticked.append(1) or {"a": "ok"},
    )

    async def runner() -> None:
        cfg = _make_config(interval=42)
        loop = asyncio.get_event_loop()
        original_sleep = asyncio.sleep

        async def fake_sleep(secs: float) -> None:
            sleeps.append(secs)
            # First sleep → cancel the coroutine so the loop exits.
            raise asyncio.CancelledError()

        monkeypatch.setattr(asyncio, "sleep", fake_sleep)
        try:
            await daemon._autonomous_tick_loop(cfg)
        except asyncio.CancelledError:
            pass

    asyncio.run(runner())

    assert ticked == [1]
    assert sleeps == [42]


def test_autonomous_tick_loop_recovers_from_tick_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If tick_autonomous_apps raises (shouldn't, but defensive), loop sleeps + continues."""
    sleeps: list[float] = []

    def boom() -> dict:
        raise RuntimeError("upstream registry corrupt")

    monkeypatch.setattr(daemon, "tick_autonomous_apps", boom)

    async def runner() -> None:
        cfg = _make_config(interval=7)

        async def fake_sleep(secs: float) -> None:
            sleeps.append(secs)
            raise asyncio.CancelledError()

        monkeypatch.setattr(asyncio, "sleep", fake_sleep)
        try:
            await daemon._autonomous_tick_loop(cfg)
        except asyncio.CancelledError:
            pass

    asyncio.run(runner())

    # We still slept after the exception, didn't crash
    assert sleeps == [7]


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


async def _run_loop_once(coro):
    """Run a coroutine that should return promptly (disabled-loop short-circuit)."""
    await asyncio.wait_for(coro, timeout=1.0)
