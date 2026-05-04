"""Cycle 3 tests: brief aggregation, window math, narrative, cache."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from week_intake.brief import (
    AppActivity,
    AttentionRow,
    WeekBrief,
    _hash_facts,
    _is_window_truncated,
    build_brief,
    iso_week_bounds,
    render_markdown,
)
from week_intake.protocol import WeekFolder
from week_intake.types import RouteTarget, WeekItem


# ---------------------------------------------------------------------------
# Fixtures + helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def tmp_week(tmp_path, monkeypatch):
    monkeypatch.setenv("CHAD_WEEK_DIR", str(tmp_path))
    yield tmp_path


def _seed(item: WeekItem) -> None:
    folder = WeekFolder(week=item.week)
    folder.upsert_item(item)


# Use a stable test week. ts() returns ISO strings inside [start, end).
WEEK = "2026-W19"
WEEK_START = datetime.fromisocalendar(2026, 19, 1).replace(tzinfo=timezone.utc)


def _ts(days: float = 1.0) -> str:
    return (WEEK_START + timedelta(days=days)).isoformat()


def _ts_before() -> str:
    return (WEEK_START - timedelta(hours=1)).isoformat()


def _ts_after() -> str:
    return (WEEK_START + timedelta(days=8)).isoformat()


def _routed(item_id: str, app_id: str, note_id: str = "n") -> WeekItem:
    return WeekItem(
        item_id=item_id,
        week=WEEK,
        raw_text="x",
        title=item_id,
        kind="wip",
        state="routed",
        confidence=0.9,
        target=RouteTarget(app_id=app_id),
        captain_note_id=note_id,
    )


# ---------------------------------------------------------------------------
# Week-window math
# ---------------------------------------------------------------------------


def test_iso_week_bounds_w19_2026() -> None:
    start, end = iso_week_bounds("2026-W19")
    assert start.weekday() == 0  # Monday
    assert (end - start) == timedelta(days=7)
    assert start.tzinfo == timezone.utc


def test_iso_week_bounds_year_boundary_2024_w01() -> None:
    start, end = iso_week_bounds("2024-W01")
    assert start.year == 2024
    assert (end - start) == timedelta(days=7)


def test_iso_week_bounds_2025_w52() -> None:
    start, end = iso_week_bounds("2025-W52")
    assert start.year == 2025
    assert (end - start) == timedelta(days=7)


# ---------------------------------------------------------------------------
# _is_window_truncated
# ---------------------------------------------------------------------------


def test_truncated_empty_tail() -> None:
    assert _is_window_truncated([], WEEK_START) is True


def test_truncated_non_list_tail() -> None:
    assert _is_window_truncated("not-a-list", WEEK_START) is True
    assert _is_window_truncated({"x": 1}, WEEK_START) is True


def test_truncated_all_unparseable_ts() -> None:
    tail = [{"ts": "garbage", "kind": "x"}, {"ts": None, "kind": "y"}]
    assert _is_window_truncated(tail, WEEK_START) is True


def test_truncated_oldest_inside_window() -> None:
    tail = [{"ts": _ts(0.5), "kind": "x"}, {"ts": _ts(2), "kind": "y"}]
    assert _is_window_truncated(tail, WEEK_START) is True


def test_truncated_oldest_after_window_end() -> None:
    """Tail entirely after week → cannot prove we saw week's events."""
    tail = [{"ts": _ts_after(), "kind": "x"}]
    assert _is_window_truncated(tail, WEEK_START) is True


def test_not_truncated_oldest_strictly_before_week_start() -> None:
    tail = [{"ts": _ts_before(), "kind": "x"}, {"ts": _ts(1), "kind": "y"}]
    assert _is_window_truncated(tail, WEEK_START) is False


# ---------------------------------------------------------------------------
# Per-app aggregation
# ---------------------------------------------------------------------------


def _bundle(captain_log_tail=None, paused_until=None, current_slice=None,
            queued: list[str] | None = None):
    return {
        "captain_log_tail": captain_log_tail or [],
        "paused_until": paused_until,
        "current_slice": current_slice,
        "admiral_notes_queued": [{"note_id": n} for n in (queued or [])],
        "admiral_notes_consumed": [],
    }


def test_aggregate_counts_pr_open_and_merge_windowed(tmp_week) -> None:
    _seed(_routed("wk-1", "app-a"))
    log = [
        {"ts": _ts_before(), "kind": "pull_request_opened"},  # outside window
        {"ts": _ts(1), "kind": "pull_request_opened"},
        {"ts": _ts(2), "kind": "pull_request_opened"},
        {"ts": _ts(3), "kind": "pull_request_merged"},
        {"ts": _ts_after(), "kind": "pull_request_merged"},  # outside
    ]
    with patch(
        "week_intake.status.get_app_status_http",
        return_value=_bundle(captain_log_tail=log),
    ):
        brief = build_brief(WEEK, use_llm=False)
    a = brief.apps[0]
    assert a.prs_opened == 2
    assert a.prs_merged == 1


def test_aggregate_only_escalation_raised_counts_no_double_count(tmp_week) -> None:
    _seed(_routed("wk-1", "app-a"))
    log = [
        {"ts": _ts(1), "kind": "validate", "verdict": "escalate"},
        {"ts": _ts(2), "kind": "escalation_raised"},
    ]
    with patch(
        "week_intake.status.get_app_status_http",
        return_value=_bundle(captain_log_tail=log),
    ):
        brief = build_brief(WEEK, use_llm=False)
    # validate+verdict=escalate is a state signal, NOT a weekly counter.
    assert brief.apps[0].escalations_raised == 1


def test_aggregate_escalation_resolved_counts(tmp_week) -> None:
    _seed(_routed("wk-1", "app-a"))
    log = [
        {"ts": _ts(1), "kind": "escalation_raised"},
        {"ts": _ts(2), "kind": "escalation_resolved"},
    ]
    with patch(
        "week_intake.status.get_app_status_http",
        return_value=_bundle(captain_log_tail=log),
    ):
        brief = build_brief(WEEK, use_llm=False)
    a = brief.apps[0]
    assert a.escalations_raised == 1
    assert a.escalations_resolved == 1


def test_aggregate_last_dispatch_picks_newest_in_window(tmp_week) -> None:
    _seed(_routed("wk-1", "app-a"))
    log = [
        {"ts": _ts(1), "kind": "dispatch"},
        {"ts": _ts(3), "kind": "dispatch"},
        {"ts": _ts(2), "kind": "dispatch"},
        {"ts": _ts_after(), "kind": "dispatch"},  # outside
    ]
    with patch(
        "week_intake.status.get_app_status_http",
        return_value=_bundle(captain_log_tail=log),
    ):
        brief = build_brief(WEEK, use_llm=False)
    assert brief.apps[0].last_dispatch_ts == _ts(3)


def test_aggregate_unparseable_ts_excluded(tmp_week) -> None:
    _seed(_routed("wk-1", "app-a"))
    log = [
        {"ts": "garbage", "kind": "pull_request_opened"},
        {"ts": _ts(1), "kind": "pull_request_opened"},
    ]
    with patch(
        "week_intake.status.get_app_status_http",
        return_value=_bundle(captain_log_tail=log),
    ):
        brief = build_brief(WEEK, use_llm=False)
    assert brief.apps[0].prs_opened == 1


def test_aggregate_empty_log_zero_counters(tmp_week) -> None:
    _seed(_routed("wk-1", "app-a"))
    with patch("week_intake.status.get_app_status_http", return_value=_bundle()):
        brief = build_brief(WEEK, use_llm=False)
    a = brief.apps[0]
    assert a.prs_opened == 0
    assert a.escalations_raised == 0
    assert a.last_dispatch_ts is None


def test_aggregate_malformed_entries_tolerated(tmp_week) -> None:
    _seed(_routed("wk-1", "app-a"))
    log = [
        "scalar-entry",
        {"ts": _ts(1), "kind": 42},  # non-string kind
        {"ts": _ts(2), "kind": "pull_request_opened"},
        None,
    ]
    with patch(
        "week_intake.status.get_app_status_http",
        return_value=_bundle(captain_log_tail=log),
    ):
        brief = build_brief(WEEK, use_llm=False)
    assert brief.apps[0].prs_opened == 1


# ---------------------------------------------------------------------------
# Multi-item-per-app dedup
# ---------------------------------------------------------------------------


def test_two_items_same_app_yield_one_app_row(tmp_week) -> None:
    _seed(_routed("wk-1", "chad-agent", note_id="n-1"))
    _seed(_routed("wk-2", "chad-agent", note_id="n-2"))
    log = [{"ts": _ts(1), "kind": "pull_request_opened"}]
    with patch(
        "week_intake.status.get_app_status_http",
        return_value=_bundle(captain_log_tail=log,
                             queued=["n-1", "n-2"]),
    ):
        brief = build_brief(WEEK, use_llm=False)
    assert len(brief.apps) == 1
    assert brief.apps[0].app_id == "chad-agent"
    assert brief.apps[0].item_ids == ["wk-1", "wk-2"]
    assert brief.apps[0].prs_opened == 1  # NOT doubled


def test_one_escalation_two_items_yields_one_attention_row(tmp_week) -> None:
    _seed(_routed("wk-1", "chad-agent", note_id="n-1"))
    _seed(_routed("wk-2", "chad-agent", note_id="n-2"))
    log = [{"ts": _ts(1), "kind": "escalation_raised", "rationale": "stuck"}]
    with patch(
        "week_intake.status.get_app_status_http",
        return_value=_bundle(captain_log_tail=log,
                             queued=["n-1", "n-2"]),
    ):
        brief = build_brief(WEEK, use_llm=False)
    assert len(brief.attention_items) == 1
    a = brief.attention_items[0]
    assert a.app_id == "chad-agent"
    assert a.attention_reason == "escalation"
    assert a.item_ids == ["wk-1", "wk-2"]


# ---------------------------------------------------------------------------
# Attention precedence (app-scoped)
# ---------------------------------------------------------------------------


def test_attention_escalation_beats_pause(tmp_week) -> None:
    _seed(_routed("wk-1", "chad-agent"))
    future_iso = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    log = [{"ts": _ts(1), "kind": "escalation_raised"}]
    with patch(
        "week_intake.status.get_app_status_http",
        return_value=_bundle(captain_log_tail=log, paused_until=future_iso),
    ):
        brief = build_brief(WEEK, use_llm=False)
    assert brief.attention_items[0].attention_reason == "escalation"


def test_attention_pause_parse_error_surfaces(tmp_week) -> None:
    _seed(_routed("wk-1", "chad-agent"))
    with patch(
        "week_intake.status.get_app_status_http",
        return_value=_bundle(paused_until="garbage"),
    ):
        brief = build_brief(WEEK, use_llm=False)
    assert brief.attention_items[0].attention_reason == "pause_parse_error"


# ---------------------------------------------------------------------------
# Brief assembly
# ---------------------------------------------------------------------------


def test_brief_totals_and_events_total(tmp_week) -> None:
    _seed(_routed("wk-1", "app-a"))
    _seed(WeekItem(item_id="wk-2", week=WEEK, raw_text="x", state="parsed"))
    log = [
        {"ts": _ts(1), "kind": "pull_request_opened"},
        {"ts": _ts(2), "kind": "pull_request_merged"},
        {"ts": _ts(3), "kind": "roadmap_complete"},
    ]
    with patch(
        "week_intake.status.get_app_status_http",
        return_value=_bundle(captain_log_tail=log),
    ):
        brief = build_brief(WEEK, use_llm=False)
    assert brief.totals["items"] == 2
    assert brief.totals["routed"] == 1
    assert brief.totals["events_total"] == 3


def test_brief_no_routed_empty_apps_and_attention(tmp_week) -> None:
    _seed(WeekItem(item_id="wk-1", week=WEEK, raw_text="x", state="parsed"))
    brief = build_brief(WEEK, use_llm=False)
    assert brief.apps == []
    assert brief.attention_items == []
    assert brief.totals["events_total"] == 0


def test_brief_routed_without_app_id_skipped(tmp_week) -> None:
    _seed(WeekItem(
        item_id="wk-1", week=WEEK, raw_text="x", state="routed",
        kind="wip", confidence=0.5, target=RouteTarget(app_id=None),
    ))
    brief = build_brief(WEEK, use_llm=False)
    assert brief.apps == []


# ---------------------------------------------------------------------------
# Narrative path (LLM mocked)
# ---------------------------------------------------------------------------


def test_narrative_happy_path_caches(tmp_week) -> None:
    _seed(_routed("wk-1", "app-a"))
    with patch("week_intake.status.get_app_status_http", return_value=_bundle()):
        with patch(
            "week_intake.brief.claude_complete", return_value="all quiet."
        ) as m:
            brief = build_brief(WEEK, use_llm=True)
        assert m.call_count == 1
    assert brief.narrative == "all quiet."
    cache_path = WeekFolder(week=WEEK).root / "brief.cache.json"
    assert cache_path.exists()
    payload = json.loads(cache_path.read_text())
    assert payload["narrative"] == "all quiet."
    assert payload["prompt_version"] == 1


def test_narrative_llm_error_no_cache_write(tmp_week) -> None:
    from week_intake.llm import LLMError

    _seed(_routed("wk-1", "app-a"))
    with patch("week_intake.status.get_app_status_http", return_value=_bundle()):
        with patch("week_intake.brief.claude_complete", side_effect=LLMError("fail")):
            brief = build_brief(WEEK, use_llm=True)
    assert brief.narrative == ""
    assert not (WeekFolder(week=WEEK).root / "brief.cache.json").exists()


def test_cache_hit_skips_llm(tmp_week) -> None:
    _seed(_routed("wk-1", "app-a"))
    with patch("week_intake.status.get_app_status_http", return_value=_bundle()):
        with patch(
            "week_intake.brief.claude_complete", return_value="first call."
        ) as m:
            build_brief(WEEK, use_llm=True)
            assert m.call_count == 1
            # Second call: same facts → cache hit, no LLM.
            brief2 = build_brief(WEEK, use_llm=True)
            assert m.call_count == 1
    assert brief2.narrative == "first call."
    assert brief2.used_cache is True


def test_cache_miss_when_facts_change_busts_narrative(tmp_week) -> None:
    _seed(_routed("wk-1", "app-a"))
    log_v1 = []
    log_v2 = [{"ts": _ts(1), "kind": "pull_request_opened"}]
    with patch(
        "week_intake.brief.claude_complete",
        side_effect=["first.", "second."],
    ) as m:
        with patch(
            "week_intake.status.get_app_status_http",
            return_value=_bundle(captain_log_tail=log_v1),
        ):
            build_brief(WEEK, use_llm=True)
        with patch(
            "week_intake.status.get_app_status_http",
            return_value=_bundle(captain_log_tail=log_v2),
        ):
            brief2 = build_brief(WEEK, use_llm=True)
    assert m.call_count == 2
    assert brief2.narrative == "second."


def test_cache_invalidates_on_prompt_version_bump(tmp_week, monkeypatch) -> None:
    _seed(_routed("wk-1", "app-a"))
    with patch(
        "week_intake.brief.claude_complete",
        side_effect=["v1 prose.", "v2 prose."],
    ) as m:
        with patch("week_intake.status.get_app_status_http", return_value=_bundle()):
            build_brief(WEEK, use_llm=True)
            monkeypatch.setattr("week_intake.brief._BRIEF_PROMPT_VERSION", 2)
            brief2 = build_brief(WEEK, use_llm=True)
    assert m.call_count == 2
    assert brief2.narrative == "v2 prose."


def test_refresh_forces_regenerate(tmp_week) -> None:
    _seed(_routed("wk-1", "app-a"))
    with patch(
        "week_intake.brief.claude_complete",
        side_effect=["first.", "second."],
    ) as m:
        with patch("week_intake.status.get_app_status_http", return_value=_bundle()):
            build_brief(WEEK, use_llm=True)
            brief2 = build_brief(WEEK, use_llm=True, refresh=True)
    assert m.call_count == 2
    assert brief2.narrative == "second."
    assert brief2.used_cache is False


# ---------------------------------------------------------------------------
# Cache resilience
# ---------------------------------------------------------------------------


def test_corrupt_cache_is_treated_as_miss(tmp_week) -> None:
    _seed(_routed("wk-1", "app-a"))
    folder = WeekFolder(week=WEEK)
    folder.ensure()
    cache = folder.root / "brief.cache.json"
    cache.write_text("not-json{{{", encoding="utf-8")
    with patch("week_intake.status.get_app_status_http", return_value=_bundle()):
        with patch(
            "week_intake.brief.claude_complete", return_value="regen."
        ) as m:
            brief = build_brief(WEEK, use_llm=True)
    assert m.call_count == 1
    assert brief.narrative == "regen."
    assert brief.used_cache is False


def test_cache_missing_input_hash_is_treated_as_miss(tmp_week) -> None:
    _seed(_routed("wk-1", "app-a"))
    folder = WeekFolder(week=WEEK)
    folder.ensure()
    cache = folder.root / "brief.cache.json"
    cache.write_text(json.dumps({"prompt_version": 1, "narrative": "stale."}))
    with patch("week_intake.status.get_app_status_http", return_value=_bundle()):
        with patch(
            "week_intake.brief.claude_complete", return_value="fresh."
        ) as m:
            brief = build_brief(WEEK, use_llm=True)
    assert m.call_count == 1
    assert brief.narrative == "fresh."


def test_cache_wrong_type_for_prompt_version_treated_as_miss(tmp_week) -> None:
    _seed(_routed("wk-1", "app-a"))
    folder = WeekFolder(week=WEEK)
    folder.ensure()
    cache = folder.root / "brief.cache.json"
    cache.write_text(json.dumps({
        "prompt_version": "one",
        "input_hash": "x",
        "narrative": "stale",
    }))
    with patch("week_intake.status.get_app_status_http", return_value=_bundle()):
        with patch(
            "week_intake.brief.claude_complete", return_value="ok."
        ) as m:
            build_brief(WEEK, use_llm=True)
    assert m.call_count == 1


# ---------------------------------------------------------------------------
# --no-llm semantics (v3 explicit)
# ---------------------------------------------------------------------------


def test_no_llm_skips_shellout_and_cache_read(tmp_week) -> None:
    _seed(_routed("wk-1", "app-a"))
    folder = WeekFolder(week=WEEK)
    folder.ensure()
    cache = folder.root / "brief.cache.json"
    # Pre-populate cache with valid-shaped narrative; we want to prove
    # --no-llm ignores it.
    cache.write_text(json.dumps({
        "prompt_version": 1,
        "input_hash": "anything",
        "narrative": "PRE-CACHED PROSE",
    }))
    with patch("week_intake.status.get_app_status_http", return_value=_bundle()):
        with patch("week_intake.brief.claude_complete") as m:
            brief = build_brief(WEEK, use_llm=False)
    assert m.call_count == 0
    assert brief.narrative == ""
    # Cache file is left as-is; we did not write to it either.
    assert json.loads(cache.read_text())["narrative"] == "PRE-CACHED PROSE"


# ---------------------------------------------------------------------------
# Cache path respects CHAD_WEEK_DIR
# ---------------------------------------------------------------------------


def test_cache_path_under_chad_week_dir(tmp_week) -> None:
    _seed(_routed("wk-1", "app-a"))
    with patch("week_intake.status.get_app_status_http", return_value=_bundle()):
        with patch("week_intake.brief.claude_complete", return_value="ok."):
            build_brief(WEEK, use_llm=True)
    cache = tmp_week / WEEK / "brief.cache.json"
    assert cache.exists()


# ---------------------------------------------------------------------------
# Markdown render
# ---------------------------------------------------------------------------


def test_render_markdown_contains_week_and_summary() -> None:
    brief = WeekBrief(
        week="2026-W19",
        week_start_utc=WEEK_START.isoformat(),
        week_end_utc=(WEEK_START + timedelta(days=7)).isoformat(),
        totals={"items": 2, "routed": 1, "captain_unreachable": 0,
                "needs_attention": 1, "events_total": 4},
        apps=[AppActivity(
            app_id="chad-agent", prs_opened=2, prs_merged=1,
            roadmap_completes=1, escalations_raised=1,
            slice_in_flight="build-it", item_ids=["wk-1"],
        )],
        attention_items=[AttentionRow(
            app_id="chad-agent",
            attention_reason="escalation",
            pause_reason=None,
            last_action_rationale="stuck on validate",
            item_ids=["wk-1"],
        )],
        narrative="Two PRs landed on chad-agent this week.",
        prompt_version=1,
        used_cache=False,
    )
    md = render_markdown(brief)
    assert "# Week 2026-W19" in md
    assert "Two PRs landed on chad-agent" in md
    assert "## Apps" in md
    assert "chad-agent" in md
    assert "## Attention" in md
    assert "stuck on validate" in md


def test_render_markdown_unavailable_narrative_line() -> None:
    brief = WeekBrief(
        week="2026-W19",
        week_start_utc=WEEK_START.isoformat(),
        week_end_utc=(WEEK_START + timedelta(days=7)).isoformat(),
        totals={"items": 0, "routed": 0, "captain_unreachable": 0,
                "needs_attention": 0, "events_total": 0},
        apps=[], attention_items=[], narrative="",
        prompt_version=1, used_cache=False,
    )
    md = render_markdown(brief)
    assert "(narrative unavailable)" in md


# ---------------------------------------------------------------------------
# Hash determinism sanity
# ---------------------------------------------------------------------------


def test_hash_facts_deterministic() -> None:
    a = {"x": 1, "y": [1, 2]}
    b = {"y": [1, 2], "x": 1}  # same dict, different key order
    assert _hash_facts(a) == _hash_facts(b)


def test_hash_facts_changes_on_value_change() -> None:
    a = {"x": 1}
    b = {"x": 2}
    assert _hash_facts(a) != _hash_facts(b)
