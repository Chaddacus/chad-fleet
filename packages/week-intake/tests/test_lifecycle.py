"""Cycle 5 tests: complete/abandon/reopen transitions + audit + restoration."""

from __future__ import annotations

import pytest

from week_intake.lifecycle import (
    ABANDON_FROM,
    COMPLETE_FROM,
    REOPEN_FROM,
    TransitionError,
    abandon_item,
    complete_item,
    reopen_item,
)
from week_intake.protocol import WeekFolder
from week_intake.types import LifecycleEvent, RouteTarget, WeekItem


WEEK = "2026-W19"


@pytest.fixture
def tmp_week(tmp_path, monkeypatch):
    monkeypatch.setenv("CHAD_WEEK_DIR", str(tmp_path))
    yield tmp_path


def _seed(state: str, item_id: str = "wk-1", note_id: str | None = None,
          app_id: str = "chad-agent") -> WeekItem:
    item = WeekItem(
        item_id=item_id, week=WEEK, raw_text="x", title=item_id,
        kind="wip", state=state, confidence=0.9,
        target=RouteTarget(app_id=app_id),
        captain_note_id=note_id,
    )
    WeekFolder(week=WEEK).upsert_item(item)
    return item


# ---------------------------------------------------------------------------
# Allow-set sanity
# ---------------------------------------------------------------------------


def test_complete_from_set() -> None:
    assert COMPLETE_FROM == {"routed", "in_progress", "blocked"}


def test_abandon_from_set() -> None:
    assert ABANDON_FROM == {
        "parsed", "needs_clarification", "ready",
        "routed", "in_progress", "blocked",
    }


def test_reopen_from_set() -> None:
    assert REOPEN_FROM == {"done", "abandoned"}


# ---------------------------------------------------------------------------
# complete
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("source", ["routed", "in_progress", "blocked"])
def test_complete_from_each_allowed(tmp_week, source) -> None:
    _seed(source)
    item = complete_item(WEEK, "wk-1")
    assert item.state == "done"


@pytest.mark.parametrize(
    "source", ["parsed", "ready", "needs_clarification", "done", "abandoned"]
)
def test_complete_from_disallowed_raises(tmp_week, source) -> None:
    _seed(source)
    with pytest.raises(TransitionError):
        complete_item(WEEK, "wk-1")


def test_complete_missing_item_raises(tmp_week) -> None:
    with pytest.raises(TransitionError, match="not found"):
        complete_item(WEEK, "wk-does-not-exist")


# ---------------------------------------------------------------------------
# abandon
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "source",
    ["parsed", "needs_clarification", "ready", "routed", "in_progress", "blocked"],
)
def test_abandon_from_each_allowed(tmp_week, source) -> None:
    _seed(source)
    item = abandon_item(WEEK, "wk-1")
    assert item.state == "abandoned"


@pytest.mark.parametrize("source", ["done", "abandoned"])
def test_abandon_from_terminal_raises(tmp_week, source) -> None:
    _seed(source)
    with pytest.raises(TransitionError):
        abandon_item(WEEK, "wk-1")


def test_abandon_records_reason(tmp_week) -> None:
    _seed("routed")
    item = abandon_item(WEEK, "wk-1", reason="stale, moving on")
    assert item.lifecycle_log[-1].reason == "stale, moving on"


# ---------------------------------------------------------------------------
# reopen restoration
# ---------------------------------------------------------------------------


def test_reopen_restores_routed_after_complete(tmp_week) -> None:
    _seed("routed")
    complete_item(WEEK, "wk-1")
    item = reopen_item(WEEK, "wk-1")
    assert item.state == "routed"


def test_reopen_restores_blocked_after_abandon(tmp_week) -> None:
    _seed("blocked")
    abandon_item(WEEK, "wk-1")
    item = reopen_item(WEEK, "wk-1")
    assert item.state == "blocked"


def test_reopen_restores_needs_clarification_after_abandon(tmp_week) -> None:
    _seed("needs_clarification")
    abandon_item(WEEK, "wk-1")
    item = reopen_item(WEEK, "wk-1")
    assert item.state == "needs_clarification"


def test_reopen_after_multiple_cycles_uses_latest(tmp_week) -> None:
    _seed("routed")
    complete_item(WEEK, "wk-1")
    reopen_item(WEEK, "wk-1")  # routed
    abandon_item(WEEK, "wk-1")  # abandoned (from_state=routed)
    item = reopen_item(WEEK, "wk-1")
    assert item.state == "routed"


def test_reopen_legacy_item_with_no_lifecycle_log_falls_back(tmp_week) -> None:
    folder = WeekFolder(week=WEEK)
    folder.upsert_item(WeekItem(
        item_id="wk-legacy", week=WEEK, raw_text="x", state="done",
        kind="wip", confidence=0.5, lifecycle_log=[],
    ))
    item = reopen_item(WEEK, "wk-legacy")
    assert item.state == "needs_clarification"
    assert item.refresh_warnings
    assert "no terminal-transition history" in item.refresh_warnings[0]


@pytest.mark.parametrize(
    "source",
    ["parsed", "needs_clarification", "ready", "routed", "in_progress", "blocked"],
)
def test_reopen_from_non_terminal_raises(tmp_week, source) -> None:
    _seed(source)
    with pytest.raises(TransitionError):
        reopen_item(WEEK, "wk-1")


# ---------------------------------------------------------------------------
# Persistence + audit
# ---------------------------------------------------------------------------


def test_complete_bumps_revision_by_one(tmp_week) -> None:
    item = _seed("routed")
    assert item.revision == 0
    out = complete_item(WEEK, "wk-1")
    assert out.revision == 1


def test_abandon_bumps_revision_by_one(tmp_week) -> None:
    _seed("routed")
    out = abandon_item(WEEK, "wk-1")
    assert out.revision == 1


def test_reopen_bumps_revision_by_one(tmp_week) -> None:
    _seed("routed")
    complete_item(WEEK, "wk-1")  # rev=1
    out = reopen_item(WEEK, "wk-1")
    assert out.revision == 2


def test_captain_note_id_preserved_across_complete(tmp_week) -> None:
    _seed("routed", note_id="dn-1")
    item = complete_item(WEEK, "wk-1")
    assert item.captain_note_id == "dn-1"


def test_captain_note_id_preserved_across_abandon(tmp_week) -> None:
    _seed("routed", note_id="dn-1")
    item = abandon_item(WEEK, "wk-1")
    assert item.captain_note_id == "dn-1"


def test_captain_note_id_preserved_across_reopen(tmp_week) -> None:
    _seed("routed", note_id="dn-1")
    complete_item(WEEK, "wk-1")
    item = reopen_item(WEEK, "wk-1")
    assert item.captain_note_id == "dn-1"


def test_complete_appends_lifecycle_event(tmp_week) -> None:
    _seed("routed")
    item = complete_item(WEEK, "wk-1")
    assert len(item.lifecycle_log) == 1
    ev = item.lifecycle_log[0]
    assert ev.transition == "complete"
    assert ev.from_state == "routed"
    assert ev.to_state == "done"
    assert isinstance(ev.at, str)


def test_reopen_records_event_with_terminal_from_state(tmp_week) -> None:
    _seed("routed")
    complete_item(WEEK, "wk-1")
    item = reopen_item(WEEK, "wk-1")
    assert len(item.lifecycle_log) == 2
    ev = item.lifecycle_log[-1]
    assert ev.transition == "reopen"
    assert ev.from_state == "done"
    assert ev.to_state == "routed"


def test_persisted_round_trip(tmp_week) -> None:
    _seed("routed")
    complete_item(WEEK, "wk-1")
    # Read back through fresh folder.
    folder = WeekFolder(week=WEEK)
    item = folder.get_item("wk-1")
    assert item is not None
    assert item.state == "done"
    assert len(item.lifecycle_log) == 1


# ---------------------------------------------------------------------------
# Status integration (cycle 2 -> cycle 5 broadening)
# ---------------------------------------------------------------------------


def test_status_returns_done_for_completed_items(tmp_week) -> None:
    from week_intake.status import per_item_captain_detail, per_item_captain_status

    _seed("routed", note_id="n")
    complete_item(WEEK, "wk-1")
    item = WeekFolder(week=WEEK).get_item("wk-1")
    assert per_item_captain_detail(item).note_status == "done"
    assert per_item_captain_status(item)[0] == "done"


def test_status_returns_abandoned_for_abandoned_items(tmp_week) -> None:
    from week_intake.status import per_item_captain_detail, per_item_captain_status

    _seed("routed")
    abandon_item(WEEK, "wk-1")
    item = WeekFolder(week=WEEK).get_item("wk-1")
    assert per_item_captain_detail(item).note_status == "abandoned"
    assert per_item_captain_status(item)[0] == "abandoned"


def test_status_fetches_captain_for_in_progress(tmp_week) -> None:
    from unittest.mock import patch

    from week_intake.status import per_item_captain_detail

    _seed("in_progress", note_id="n-A")
    bundle = {
        "admiral_notes_queued": [{"note_id": "n-A"}],
        "admiral_notes_consumed": [],
    }
    item = WeekFolder(week=WEEK).get_item("wk-1")
    with patch("week_intake.status.get_app_status_http", return_value=bundle):
        d = per_item_captain_detail(item)
    assert d.note_status == "queued"  # captain bundle WAS consulted


def test_status_fetches_captain_for_blocked(tmp_week) -> None:
    from unittest.mock import patch

    from week_intake.status import per_item_captain_detail

    _seed("blocked", note_id="n-A")
    bundle = {
        "admiral_notes_queued": [],
        "admiral_notes_consumed": [{"note_id": "n-A"}],
    }
    item = WeekFolder(week=WEEK).get_item("wk-1")
    with patch("week_intake.status.get_app_status_http", return_value=bundle):
        d = per_item_captain_detail(item)
    assert d.note_status == "consumed"


def test_unreachable_count_includes_in_progress_and_blocked(tmp_week) -> None:
    from unittest.mock import patch

    from week_intake.captain_client import CaptainError
    from week_intake.status import rollup

    _seed("routed", item_id="wk-r", note_id="n-r", app_id="dead")
    _seed("in_progress", item_id="wk-i", note_id="n-i", app_id="dead")
    _seed("blocked", item_id="wk-b", note_id="n-b", app_id="dead")
    items = WeekFolder(week=WEEK).list_items()
    with patch("week_intake.status.get_app_status_http",
               side_effect=CaptainError("dead")):
        report = rollup(items)
    assert report["totals"]["captain_unreachable"] == 3


# ---------------------------------------------------------------------------
# Brief integration
# ---------------------------------------------------------------------------


def test_brief_apps_includes_in_progress(tmp_week) -> None:
    from unittest.mock import patch

    from week_intake.brief import build_brief

    _seed("in_progress", item_id="wk-1", note_id="n", app_id="app-a")
    bundle = {"captain_log_tail": [], "admiral_notes_queued": [{"note_id": "n"}]}
    with patch("week_intake.status.get_app_status_http", return_value=bundle):
        brief = build_brief(WEEK, use_llm=False)
    assert any(a.app_id == "app-a" for a in brief.apps)


def test_brief_terminal_does_not_shadow_linked(tmp_week) -> None:
    from unittest.mock import patch

    from week_intake.brief import build_brief

    # Two items same app: one done (terminal), one blocked (linked).
    _seed("routed", item_id="wk-d", note_id="n-d", app_id="shared")
    complete_item(WEEK, "wk-d")
    _seed("blocked", item_id="wk-b", note_id="n-b", app_id="shared")
    bundle = {
        "captain_log_tail": [],
        "current_slice": {"title": "live-slice"},
        "admiral_notes_queued": [{"note_id": "n-b"}],
    }
    with patch("week_intake.status.get_app_status_http", return_value=bundle):
        brief = build_brief(WEEK, use_llm=False)
    apps = [a for a in brief.apps if a.app_id == "shared"]
    assert len(apps) == 1
    # Slice in flight must come from the LINKED row, not the terminal row.
    assert apps[0].slice_in_flight == "live-slice"
    # item_ids listed should be only the linked item.
    assert apps[0].item_ids == ["wk-b"]


# ---------------------------------------------------------------------------
# LifecycleEvent persistence sanity
# ---------------------------------------------------------------------------


def test_lifecycle_log_round_trips_through_jsonl(tmp_week) -> None:
    _seed("routed")
    complete_item(WEEK, "wk-1")
    folder = WeekFolder(week=WEEK)
    item = folder.get_item("wk-1")
    assert isinstance(item.lifecycle_log[0], LifecycleEvent)
    assert item.lifecycle_log[0].transition == "complete"
