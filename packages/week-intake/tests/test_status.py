"""Status rollup tests with captain HTTP mocked."""

from __future__ import annotations

from unittest.mock import patch

from week_intake.status import per_item_captain_status, rollup
from week_intake.types import RouteTarget, WeekItem


def _routed_item(item_id: str, app_id: str, note_id: str = "note-1") -> WeekItem:
    return WeekItem(
        item_id=item_id,
        week="2026-W19",
        raw_text="x",
        title="x",
        kind="wip",
        state="routed",
        confidence=0.9,
        target=RouteTarget(app_id=app_id),
        captain_note_id=note_id,
    )


def test_per_item_status_not_routed() -> None:
    item = WeekItem(item_id="wk-001", week="2026-W19", raw_text="x", state="parsed")
    status, _ = per_item_captain_status(item)
    assert status == "not_routed"


def test_per_item_status_queued_when_in_queued_list() -> None:
    item = _routed_item("wk-001", "chad-agent", note_id="note-A")
    bundle = {
        "admiral_notes_queued": [{"note_id": "note-A"}],
        "admiral_notes_consumed": [],
    }
    with patch("week_intake.status.get_app_status_http", return_value=bundle):
        status, _ = per_item_captain_status(item)
    assert status == "queued"


def test_per_item_status_consumed_when_in_consumed_list() -> None:
    item = _routed_item("wk-001", "chad-agent", note_id="note-B")
    bundle = {
        "admiral_notes_queued": [],
        "admiral_notes_consumed": [{"note_id": "note-B"}],
    }
    with patch("week_intake.status.get_app_status_http", return_value=bundle):
        status, _ = per_item_captain_status(item)
    assert status == "consumed"


def test_per_item_status_unknown_app_when_404() -> None:
    item = _routed_item("wk-001", "ghost-app")
    with patch("week_intake.status.get_app_status_http", return_value=None):
        status, _ = per_item_captain_status(item)
    assert status == "unknown_app"


def test_per_item_status_unreachable_on_captain_error() -> None:
    from week_intake.captain_client import CaptainError

    item = _routed_item("wk-001", "chad-agent")
    with patch(
        "week_intake.status.get_app_status_http",
        side_effect=CaptainError("api down"),
    ):
        status, _ = per_item_captain_status(item)
    assert status == "unreachable"


def test_rollup_aggregates_counts() -> None:
    items = [
        WeekItem(item_id="wk-001", week="2026-W19", raw_text="a", state="parsed", kind="wip"),
        WeekItem(
            item_id="wk-002",
            week="2026-W19",
            raw_text="b",
            state="needs_clarification",
            kind="greenfield",
        ),
        _routed_item("wk-003", "chad-agent", note_id="note-X"),
    ]
    bundle = {
        "admiral_notes_queued": [{"note_id": "note-X"}],
        "admiral_notes_consumed": [],
    }
    with patch("week_intake.status.get_app_status_http", return_value=bundle):
        report = rollup(items)
    assert report["totals"]["items"] == 3
    assert report["totals"]["routed"] == 1
    assert report["by_state"]["parsed"] == 1
    assert report["by_state"]["needs_clarification"] == 1
    assert report["by_state"]["routed"] == 1
    assert report["by_app"]["chad-agent"] == 1
    assert report["by_app"]["(unrouted)"] == 2

    routed_row = next(r for r in report["items"] if r["item_id"] == "wk-003")
    assert routed_row["captain_note_status"] == "queued"
