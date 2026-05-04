"""Cycle 4 tests: cross-week active item view."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from week_intake.active import (
    ACTIVE_STATES,
    ActiveRow,
    _discover_weeks,
    _is_active,
    _select_weeks,
    _sort_within_week,
    list_active,
)
from week_intake.protocol import WeekFolder
from week_intake.types import RouteTarget, WeekItem


# ---------------------------------------------------------------------------
# Allowlist sanity
# ---------------------------------------------------------------------------


def test_active_states_excludes_terminal() -> None:
    assert ACTIVE_STATES == {
        "parsed",
        "needs_clarification",
        "ready",
        "routed",
        "in_progress",
        "blocked",
    }
    assert "done" not in ACTIVE_STATES
    assert "abandoned" not in ACTIVE_STATES


# ---------------------------------------------------------------------------
# `_is_active`
# ---------------------------------------------------------------------------


def _item(state: str, item_id: str = "wk-1") -> WeekItem:
    return WeekItem(item_id=item_id, week="2026-W19", raw_text="x", state=state)


def _item_construct(state: str, item_id: str = "wk-1") -> WeekItem:
    """Bypass Pydantic validation — used to test unknown/legacy persisted states."""
    return WeekItem.model_construct(
        item_id=item_id,
        week="2026-W19",
        raw_text="x",
        title="x",
        kind="unknown",
        state=state,
        confidence=0.0,
        target=RouteTarget(),
        clarifications=[],
        captain_note_id=None,
        revision=0,
        pending_refresh_question_id=None,
        refresh_warnings=[],
        created_at="2026-05-04T00:00:00+00:00",
        updated_at="2026-05-04T00:00:00+00:00",
    )


@pytest.mark.parametrize("state", sorted(ACTIVE_STATES))
def test_is_active_true_for_each_active_state(state: str) -> None:
    assert _is_active(_item(state), None) is True


@pytest.mark.parametrize("state", ["done", "abandoned"])
def test_is_active_false_for_terminal_states(state: str) -> None:
    assert _is_active(_item(state), None) is False


def test_is_active_false_for_unknown_persisted_states() -> None:
    # Pydantic Literal would reject these via WeekItem(...) — use model_construct
    # to simulate a corrupt persisted row that survived list_items's tolerant path.
    assert _is_active(_item_construct("paused"), None) is False
    assert _is_active(_item_construct(""), None) is False


def test_is_active_with_state_filter_narrows() -> None:
    assert _is_active(_item("blocked"), "blocked") is True
    assert _is_active(_item("routed"), "blocked") is False
    assert _is_active(_item("done"), "done") is False  # terminal still excluded


# ---------------------------------------------------------------------------
# `_select_weeks`
# ---------------------------------------------------------------------------


def test_select_weeks_returns_current_plus_lookback() -> None:
    discovered = ["2026-W14", "2026-W15", "2026-W16", "2026-W17", "2026-W18", "2026-W19"]
    out = _select_weeks(discovered, "2026-W19", 4)
    assert out == ["2026-W19", "2026-W18", "2026-W17", "2026-W16", "2026-W15"]


def test_select_weeks_lookback_zero_returns_only_current() -> None:
    out = _select_weeks(["2026-W18", "2026-W19"], "2026-W19", 0)
    assert out == ["2026-W19"]


def test_select_weeks_drops_future_weeks() -> None:
    out = _select_weeks(["2026-W19", "2026-W20", "2026-W21"], "2026-W19", 4)
    assert "2026-W20" not in out
    assert "2026-W21" not in out
    assert out == ["2026-W19"]


def test_select_weeks_drops_old_weeks_beyond_bound() -> None:
    # current=W19 lookback=2 means earliest is W17; W14 is dropped.
    out = _select_weeks(["2026-W14", "2026-W19"], "2026-W19", 2)
    assert out == ["2026-W19"]


def test_select_weeks_includes_current_even_if_not_discovered() -> None:
    out = _select_weeks(["2026-W18"], "2026-W19", 4)
    assert "2026-W19" in out
    assert "2026-W18" in out


def test_select_weeks_skips_unparseable_tags_in_discovered() -> None:
    out = _select_weeks(["junk", "not-a-week", "2026-W18", "2026-W19"], "2026-W19", 4)
    assert out == ["2026-W19", "2026-W18"]


def test_select_weeks_negative_lookback_raises() -> None:
    with pytest.raises(ValueError, match="lookback must be >= 0"):
        _select_weeks([], "2026-W19", -1)


def test_select_weeks_year_boundary() -> None:
    # current=2025-W01 lookback=2 should reach back to 2024-W51.
    discovered = ["2024-W50", "2024-W51", "2024-W52", "2025-W01"]
    out = _select_weeks(discovered, "2025-W01", 2)
    assert "2024-W51" in out
    assert "2024-W52" in out
    assert "2024-W50" not in out
    assert out[0] == "2025-W01"


# ---------------------------------------------------------------------------
# `_sort_within_week`
# ---------------------------------------------------------------------------


def _it_with(item_id: str, created: str) -> WeekItem:
    return WeekItem(
        item_id=item_id, week="2026-W19", raw_text="x",
        state="parsed", created_at=created, updated_at=created,
    )


def test_sort_within_week_newest_created_first() -> None:
    a = _it_with("wk-001", "2026-05-01T10:00:00+00:00")
    b = _it_with("wk-002", "2026-05-03T10:00:00+00:00")
    c = _it_with("wk-003", "2026-05-02T10:00:00+00:00")
    out = _sort_within_week([a, b, c])
    assert [it.item_id for it in out] == ["wk-002", "wk-003", "wk-001"]


def test_sort_within_week_tiebreak_by_item_id() -> None:
    a = _it_with("wk-002", "2026-05-01T10:00:00+00:00")
    b = _it_with("wk-001", "2026-05-01T10:00:00+00:00")
    out = _sort_within_week([a, b])
    assert [it.item_id for it in out] == ["wk-001", "wk-002"]


# ---------------------------------------------------------------------------
# `_discover_weeks`
# ---------------------------------------------------------------------------


def test_discover_weeks_filters_junk(tmp_path: Path) -> None:
    (tmp_path / "2026-W18").mkdir()
    (tmp_path / "2026-W19").mkdir()
    (tmp_path / "junk").mkdir()
    (tmp_path / "tmp123").mkdir()
    (tmp_path / "README.md").write_text("nope")
    out = _discover_weeks(tmp_path)
    assert sorted(out) == ["2026-W18", "2026-W19"]


def test_discover_weeks_empty_base(tmp_path: Path) -> None:
    assert _discover_weeks(tmp_path) == []


def test_discover_weeks_missing_base(tmp_path: Path) -> None:
    assert _discover_weeks(tmp_path / "does-not-exist") == []


# ---------------------------------------------------------------------------
# End-to-end: list_active
# ---------------------------------------------------------------------------


@pytest.fixture
def tmp_week_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("CHAD_WEEK_DIR", str(tmp_path))
    yield tmp_path


def _seed(week: str, item_id: str, state: str, created: str | None = None) -> None:
    folder = WeekFolder(week=week)
    created = created or datetime.now(timezone.utc).isoformat()
    folder.upsert_item(WeekItem(
        item_id=item_id, week=week, raw_text="x", title=item_id,
        kind="wip", state=state, confidence=0.9,
        created_at=created, updated_at=created,
    ))


def test_list_active_returns_only_non_terminal(tmp_week_dir) -> None:
    _seed("2026-W19", "wk-1", "routed")
    _seed("2026-W19", "wk-2", "done")
    _seed("2026-W19", "wk-3", "abandoned")
    rows = list_active(now_week="2026-W19")
    assert sorted(r.item.item_id for r in rows) == ["wk-1"]


def test_list_active_newest_week_first(tmp_week_dir) -> None:
    _seed("2026-W17", "wk-old", "routed")
    _seed("2026-W18", "wk-mid", "routed")
    _seed("2026-W19", "wk-new", "routed")
    rows = list_active(now_week="2026-W19", lookback=4)
    assert [r.week for r in rows] == ["2026-W19", "2026-W18", "2026-W17"]


def test_list_active_state_filter(tmp_week_dir) -> None:
    _seed("2026-W19", "wk-1", "routed")
    _seed("2026-W19", "wk-2", "blocked")
    _seed("2026-W19", "wk-3", "ready")
    rows = list_active(now_week="2026-W19", state="blocked")
    assert [r.item.item_id for r in rows] == ["wk-2"]


def test_list_active_state_filter_unknown_raises(tmp_week_dir) -> None:
    with pytest.raises(ValueError, match="state must be None"):
        list_active(now_week="2026-W19", state="blockd")


def test_list_active_negative_lookback_raises(tmp_week_dir) -> None:
    with pytest.raises(ValueError, match="lookback must be >= 0"):
        list_active(lookback=-1, now_week="2026-W19")


def test_list_active_lookback_zero_only_current_week(tmp_week_dir) -> None:
    _seed("2026-W18", "wk-old", "routed")
    _seed("2026-W19", "wk-new", "routed")
    rows = list_active(now_week="2026-W19", lookback=0)
    assert [r.item.item_id for r in rows] == ["wk-new"]


def test_list_active_excludes_weeks_outside_lookback(tmp_week_dir) -> None:
    _seed("2026-W14", "wk-veryold", "routed")
    _seed("2026-W19", "wk-new", "routed")
    rows = list_active(now_week="2026-W19", lookback=2)
    assert [r.item.item_id for r in rows] == ["wk-new"]


def test_list_active_excludes_future_weeks(tmp_week_dir) -> None:
    _seed("2026-W19", "wk-now", "routed")
    _seed("2026-W20", "wk-future", "routed")
    rows = list_active(now_week="2026-W19", lookback=4)
    assert all(r.week != "2026-W20" for r in rows)


def test_list_active_within_week_ordering(tmp_week_dir) -> None:
    _seed("2026-W19", "wk-1", "routed", created="2026-05-04T08:00:00+00:00")
    _seed("2026-W19", "wk-2", "routed", created="2026-05-04T10:00:00+00:00")
    _seed("2026-W19", "wk-3", "routed", created="2026-05-04T09:00:00+00:00")
    rows = list_active(now_week="2026-W19", lookback=0)
    assert [r.item.item_id for r in rows] == ["wk-2", "wk-3", "wk-1"]


def test_list_active_empty_base(tmp_path) -> None:
    rows = list_active(base=tmp_path / "nonexistent", now_week="2026-W19")
    assert rows == []


def test_list_active_returns_active_row_dataclass(tmp_week_dir) -> None:
    _seed("2026-W19", "wk-1", "routed")
    rows = list_active(now_week="2026-W19")
    assert len(rows) == 1
    assert isinstance(rows[0], ActiveRow)
    assert rows[0].week == "2026-W19"
    assert rows[0].item.item_id == "wk-1"
