"""CLI integration: `chad-week clarify` and `chad-week route` defaulting."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from week_intake.classification import ReclassifyOutput
from week_intake.cli import main
from week_intake.protocol import WeekFolder
from week_intake.types import ClarificationQuestion, RouteTarget, WeekItem


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("CHAD_WEEK_DIR", str(tmp_path / "week"))
    monkeypatch.setenv("CHAD_FLEET_APPS_DIR", str(tmp_path / "fleet"))
    monkeypatch.setenv("CHAD_CAPTAIN_API", "http://127.0.0.1:8109")
    yield tmp_path


def _seed_item(item_id: str = "wk-001") -> WeekItem:
    return WeekItem(
        item_id=item_id,
        week="2026-W19",
        raw_text="set up marketing site",
        title="set up marketing site",
        kind="unknown",
        state="needs_clarification",
        confidence=0.5,
        clarifications=[
            ClarificationQuestion(question_id="q001", prompt="new repo or wip?"),
        ],
    )


def _refresh_ready(target_path: Path) -> tuple[ReclassifyOutput, list[str]]:
    return (
        ReclassifyOutput(
            kind="greenfield",
            confidence=0.95,
            candidate_app_id="spark-marketing",
            greenfield_name="spark-marketing",
            repo_path_hint=str(target_path),
            next_question=None,
            resolution_status="ready",
            rationale="answered as greenfield",
        ),
        [],
    )


def test_clarify_happy_path(env, capsys) -> None:
    folder = WeekFolder(week="2026-W19")
    folder.upsert_item(_seed_item())

    target = env / "fresh-marketing"  # nonexistent → valid scaffold target
    with patch("week_intake.clarifier.classify_item", return_value=_refresh_ready(target)):
        rc = main([
            "clarify", "wk-001", "--week", "2026-W19",
            "--answer", "new repo, call it spark-marketing",
            "--format", "json",
        ])
    assert rc == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["state"] == "ready"
    assert payload["kind"] == "greenfield"
    assert payload["target"]["greenfield_name"] == "spark-marketing"
    assert payload["pending_refresh_question_id"] is None
    assert payload["revision"] == 2


def test_clarify_input_validation(env, capsys) -> None:
    cases = [
        (["clarify", "BAD-ID", "--answer", "x"], "invalid item_id"),
        (["clarify", "wk-001", "--question-id", "garbage", "--answer", "x"], "question_id"),
        (["clarify", "wk-001", "--week", "not-a-week", "--answer", "x"], "week tag"),
        (["clarify", "wk-001"], "--answer is required"),
        (["clarify", "wk-001", "--answer", "x", "--continue"], "mutually exclusive"),
    ]
    for argv, expect in cases:
        rc = main(argv)
        assert rc == 7
        err = capsys.readouterr().err
        assert expect in err, f"argv={argv} stderr={err!r}"


def test_clarify_continue_after_phase2_failure(env, capsys) -> None:
    from week_intake.llm import LLMError

    folder = WeekFolder(week="2026-W19")
    folder.upsert_item(_seed_item())

    # First call: LLM fails. Item ends with answer + pending set.
    with patch("week_intake.clarifier.classify_item", side_effect=LLMError("boom")):
        rc = main([
            "clarify", "wk-001", "--week", "2026-W19", "--answer", "greenfield",
            "--format", "json",
        ])
    assert rc == 11
    capsys.readouterr()

    on_disk = folder.get_item("wk-001")
    assert on_disk.pending_refresh_question_id == "q001"

    # --continue with successful LLM completes the round.
    target = env / "fresh"
    with patch("week_intake.clarifier.classify_item", return_value=_refresh_ready(target)):
        rc = main([
            "clarify", "wk-001", "--week", "2026-W19", "--continue",
            "--format", "json",
        ])
    assert rc == 0
    final = folder.get_item("wk-001")
    assert final.state == "ready"
    assert final.pending_refresh_question_id is None


def test_clarify_continue_no_pending_errors(env, capsys) -> None:
    folder = WeekFolder(week="2026-W19")
    folder.upsert_item(_seed_item())  # no pending refresh
    rc = main(["clarify", "wk-001", "--week", "2026-W19", "--continue"])
    assert rc == 10  # ClarifyError
    err = capsys.readouterr().err
    assert "no pending refresh" in err


def test_clarify_to_route_end_to_end(env, capsys) -> None:
    """Intake → clarify → route uses item.target defaults (no explicit route flags needed)."""
    folder = WeekFolder(week="2026-W19")

    # Seed an item that clarify will mark as ready (existing-app mode).
    item = _seed_item()
    item.kind = "wip"
    folder.upsert_item(item)

    # Pre-create the captain workspace so existing-app routing succeeds.
    (env / "fleet" / "chad-agent").mkdir(parents=True)

    refresh = ReclassifyOutput(
        kind="wip",
        confidence=0.92,
        candidate_app_id="chad-agent",  # has workspace → routeable
        greenfield_name=None,
        repo_path_hint=None,
        next_question=None,
        resolution_status="ready",
        rationale="confirmed as wip on chad-agent",
    )
    with patch("week_intake.clarifier.classify_item", return_value=(refresh, [])):
        assert main([
            "clarify", "wk-001", "--week", "2026-W19",
            "--answer", "wip on chad-agent",
        ]) == 0
    capsys.readouterr()

    after_clarify = folder.get_item("wk-001")
    assert after_clarify.state == "ready"
    assert after_clarify.target.app_id == "chad-agent"

    # Route with NO explicit flags — must default from item.target.
    rc = main(["route", "wk-001", "--week", "2026-W19", "--format", "json"])
    assert rc == 0
    routed = folder.get_item("wk-001")
    assert routed.state == "routed"
    assert routed.captain_note_id is not None

    # Captain workspace got the admiral_note.
    notes = list((env / "fleet" / "chad-agent" / "admiral_notes").glob("*.json"))
    assert len(notes) == 1


def test_route_explicit_app_overrides_stored_repo_path(env, capsys) -> None:
    """An explicit --app discards stored repo_path/greenfield_name."""
    folder = WeekFolder(week="2026-W19")
    item = _seed_item()
    item.state = "ready"
    item.target = RouteTarget(
        app_id="should-be-overridden",
        repo_path="/stale/path",  # would force new_repo mode otherwise
        greenfield_name="stale-name",
    )
    folder.upsert_item(item)
    (env / "fleet" / "chad-agent").mkdir(parents=True)

    rc = main([
        "route", "wk-001", "--week", "2026-W19",
        "--app", "chad-agent",  # existing-app mode (no --repo or --greenfield)
        "--format", "json",
    ])
    assert rc == 0
    routed = folder.get_item("wk-001")
    assert routed.target.app_id == "chad-agent"


def test_route_refuses_when_target_incomplete(env, capsys) -> None:
    """No flags, item.target empty → route refuses with helpful message."""
    folder = WeekFolder(week="2026-W19")
    item = _seed_item()
    item.state = "ready"  # but target is empty
    folder.upsert_item(item)
    rc = main(["route", "wk-001", "--week", "2026-W19"])
    assert rc == 8
    err = capsys.readouterr().err
    assert "cannot route" in err
    assert "missing" in err
