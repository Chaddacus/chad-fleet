"""Tests for the PR13 R3#5 engine-repair canary CLI subcommand.

Validates that:
- The synthetic dispatch+validate cycle completes against an empty repo.
- Exit code is 0 on success, 1 on engine breakage.
- All five sub-steps report individually so Twin can pinpoint failures.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest


def _invoke_canary(monkeypatch: pytest.MonkeyPatch, capsys) -> tuple[int, dict]:
    """Run cmd_canary and return (exit_code, parsed_json_payload)."""
    from chad_captain.cli import cmd_canary
    import argparse
    args = argparse.Namespace()
    try:
        cmd_canary(args)
        exit_code = 0
    except SystemExit as e:
        exit_code = int(e.code) if e.code is not None else 0
    out = capsys.readouterr().out.strip()
    # Last line is the JSON payload.
    last_line = out.splitlines()[-1] if out else "{}"
    return exit_code, json.loads(last_line)


def test_canary_succeeds_against_clean_engine(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys,
) -> None:
    """Happy path: canary exits 0 and reports all 5 steps passed."""
    monkeypatch.setenv("CHAD_FLEET_APPS_DIR", str(tmp_path / "fleet"))
    monkeypatch.setenv(
        "CHAD_CAPTAIN_APPS_REGISTRY", str(tmp_path / "registry.json"),
    )
    code, payload = _invoke_canary(monkeypatch, capsys)
    assert code == 0
    assert payload["ok"] is True
    assert set(payload["steps_passed"]) == {
        "git_init", "registry_seed", "dispatch_tick",
        "validate_tick", "captain_log_validate_entry",
    }
    assert payload["steps_failed"] == []
    assert payload["elapsed_seconds"] >= 0


def test_canary_reports_step_failure_with_detail(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys,
) -> None:
    """When a downstream step throws, the canary records it and exits 1."""
    monkeypatch.setenv("CHAD_FLEET_APPS_DIR", str(tmp_path / "fleet"))
    monkeypatch.setenv(
        "CHAD_CAPTAIN_APPS_REGISTRY", str(tmp_path / "registry.json"),
    )

    # Force captain_tick to blow up on the dispatch step.
    def boom(*a, **kw):
        raise RuntimeError("synthetic engine break")

    monkeypatch.setattr("chad_captain.validator.captain_tick", boom)

    code, payload = _invoke_canary(monkeypatch, capsys)
    assert code == 1
    assert payload["ok"] is False
    failed_names = [f["name"] for f in payload["steps_failed"]]
    assert "dispatch_tick" in failed_names
    # Failure detail captures the exception message.
    dispatch_failure = next(
        f for f in payload["steps_failed"] if f["name"] == "dispatch_tick"
    )
    assert "synthetic engine break" in dispatch_failure["detail"]


def test_canary_isolated_from_real_workspace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys,
) -> None:
    """Canary uses its own tmp dir; no files land in the user's
    real ~/.chad path. Verify by pointing CHAD_FLEET_APPS_DIR at a
    sentinel and confirming the canary's writes go to its own tmp
    (overridden inside cmd_canary), not to the env-set sentinel.
    """
    sentinel = tmp_path / "should_not_be_touched"
    monkeypatch.setenv("CHAD_FLEET_APPS_DIR", str(sentinel))
    monkeypatch.setenv(
        "CHAD_CAPTAIN_APPS_REGISTRY", str(tmp_path / "ignored.json"),
    )
    code, _ = _invoke_canary(monkeypatch, capsys)
    assert code == 0
    # cmd_canary overrides CHAD_FLEET_APPS_DIR to its tmp; the sentinel
    # path should not exist after the run.
    assert not sentinel.exists(), (
        "canary wrote to the env-set sentinel instead of using its own tmp"
    )
