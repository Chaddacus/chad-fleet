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


# ---------------------------------------------------------------------------
# Cycle 2 enrichment — per_item_captain_detail + rollup output
# ---------------------------------------------------------------------------

from datetime import datetime, timedelta, timezone

from week_intake.status import (
    CaptainItemStatus,
    per_item_captain_detail,
    rollup,
)


def _future_iso(minutes: int = 60) -> str:
    return (datetime.now(timezone.utc) + timedelta(minutes=minutes)).isoformat()


def _past_iso(minutes: int = 60) -> str:
    return (datetime.now(timezone.utc) - timedelta(minutes=minutes)).isoformat()


def _ts(minutes_ago: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)).isoformat()


# ----- slice fallback chain ------------------------------------------------


def test_detail_slice_uses_title_when_present() -> None:
    item = _routed_item("wk-1", "chad-agent")
    bundle = {
        "current_slice": {"title": "Build it", "objective": "obj", "slice_id": "s1"},
    }
    with patch("week_intake.status.get_app_status_http", return_value=bundle):
        d = per_item_captain_detail(item)
    assert d.slice_in_flight == "Build it"


def test_detail_slice_falls_back_to_objective_when_title_invalid() -> None:
    item = _routed_item("wk-1", "chad-agent")
    bundle = {"current_slice": {"title": 42, "objective": "the obj", "slice_id": "s1"}}
    with patch("week_intake.status.get_app_status_http", return_value=bundle):
        d = per_item_captain_detail(item)
    assert d.slice_in_flight == "the obj"


def test_detail_slice_falls_back_to_slice_id() -> None:
    item = _routed_item("wk-1", "chad-agent")
    bundle = {"current_slice": {"title": None, "objective": "  ", "slice_id": "s7"}}
    with patch("week_intake.status.get_app_status_http", return_value=bundle):
        d = per_item_captain_detail(item)
    assert d.slice_in_flight == "s7"


def test_detail_slice_none_when_current_slice_not_dict() -> None:
    item = _routed_item("wk-1", "chad-agent")
    bundle = {"current_slice": ["not", "a", "dict"]}
    with patch("week_intake.status.get_app_status_http", return_value=bundle):
        d = per_item_captain_detail(item)
    assert d.slice_in_flight is None


# ----- pause semantics -----------------------------------------------------


def test_detail_pause_active_when_future_iso() -> None:
    item = _routed_item("wk-1", "chad-agent")
    bundle = {"paused_until": _future_iso(30), "pause_reason": "rate-limited"}
    with patch("week_intake.status.get_app_status_http", return_value=bundle):
        d = per_item_captain_detail(item)
    assert d.pause_active is True
    assert d.pause_reason == "rate-limited"
    assert d.pause_parse_error is False
    assert d.needs_attention is True
    assert d.attention_reason == "pause"


def test_detail_pause_inactive_when_past_iso() -> None:
    item = _routed_item("wk-1", "chad-agent")
    bundle = {"paused_until": _past_iso(30), "pause_reason": "stale"}
    with patch("week_intake.status.get_app_status_http", return_value=bundle):
        d = per_item_captain_detail(item)
    assert d.pause_active is False
    assert d.needs_attention is False
    assert d.attention_reason is None


def test_detail_pause_parse_error_marks_attention() -> None:
    item = _routed_item("wk-1", "chad-agent")
    bundle = {"paused_until": "garbage-not-iso", "pause_reason": "x"}
    with patch("week_intake.status.get_app_status_http", return_value=bundle):
        d = per_item_captain_detail(item)
    assert d.pause_active is False
    assert d.pause_parse_error is True
    assert d.needs_attention is True
    assert d.attention_reason == "pause_parse_error"


def test_detail_pause_parse_error_when_non_string() -> None:
    item = _routed_item("wk-1", "chad-agent")
    bundle = {"paused_until": 1234567890}
    with patch("week_intake.status.get_app_status_http", return_value=bundle):
        d = per_item_captain_detail(item)
    assert d.pause_parse_error is True
    assert d.attention_reason == "pause_parse_error"


def test_detail_pause_naive_iso_normalized_to_utc() -> None:
    item = _routed_item("wk-1", "chad-agent")
    # naive ISO (no tz suffix) — should be treated as UTC
    naive_future = (datetime.now(timezone.utc) + timedelta(hours=1)).replace(
        tzinfo=None
    ).isoformat()
    bundle = {"paused_until": naive_future}
    with patch("week_intake.status.get_app_status_http", return_value=bundle):
        d = per_item_captain_detail(item)
    assert d.pause_active is True


def test_detail_pause_z_suffix_parsed() -> None:
    item = _routed_item("wk-1", "chad-agent")
    z_future = (
        (datetime.now(timezone.utc) + timedelta(hours=1))
        .strftime("%Y-%m-%dT%H:%M:%S")
        + "Z"
    )
    bundle = {"paused_until": z_future}
    with patch("week_intake.status.get_app_status_http", return_value=bundle):
        d = per_item_captain_detail(item)
    assert d.pause_active is True


# ----- log tail ordering + last action -------------------------------------


def test_detail_log_sorts_newest_first_by_ts() -> None:
    item = _routed_item("wk-1", "chad-agent")
    # Captain returns oldest-first; we should pick newest dispatch as the
    # latest action even if it's at end of list.
    bundle = {
        "captain_log_tail": [
            {"ts": _ts(60), "kind": "validate", "rationale": "old"},
            {"ts": _ts(10), "kind": "dispatch", "rationale": "newer"},
        ],
    }
    with patch("week_intake.status.get_app_status_http", return_value=bundle):
        d = per_item_captain_detail(item)
    assert d.last_captain_action == "dispatch"
    assert d.last_meaningful_action == "dispatch"
    assert d.last_action_rationale == "newer"


def test_detail_log_already_newest_first_still_correct() -> None:
    item = _routed_item("wk-1", "chad-agent")
    bundle = {
        "captain_log_tail": [
            {"ts": _ts(5), "kind": "dispatch", "rationale": "fresh"},
            {"ts": _ts(60), "kind": "validate", "rationale": "old"},
        ],
    }
    with patch("week_intake.status.get_app_status_http", return_value=bundle):
        d = per_item_captain_detail(item)
    assert d.last_captain_action == "dispatch"


def test_detail_meaningful_skips_note_received() -> None:
    item = _routed_item("wk-1", "chad-agent")
    bundle = {
        "captain_log_tail": [
            {"ts": _ts(60), "kind": "dispatch", "rationale": "real work"},
            {"ts": _ts(5), "kind": "note_received", "rationale": "noise"},
        ],
    }
    with patch("week_intake.status.get_app_status_http", return_value=bundle):
        d = per_item_captain_detail(item)
    assert d.last_captain_action == "note_received"
    assert d.last_meaningful_action == "dispatch"
    assert d.last_action_rationale == "real work"


def test_detail_meaningful_none_when_only_noise() -> None:
    item = _routed_item("wk-1", "chad-agent")
    bundle = {
        "captain_log_tail": [
            {"ts": _ts(10), "kind": "note_received"},
            {"ts": _ts(20), "kind": "note_received"},
        ],
    }
    with patch("week_intake.status.get_app_status_http", return_value=bundle):
        d = per_item_captain_detail(item)
    assert d.last_captain_action == "note_received"
    assert d.last_meaningful_action is None


def test_detail_log_invalid_ts_appended_after_valid() -> None:
    """Valid-ts entries sorted newest-first; invalid-ts appended (treated
    as older). last_captain_action picks dispatch (newest valid). The
    older invalid-ts escalation still activates because only an explicit
    escalation_resolved clears it (dispatch doesn't)."""
    item = _routed_item("wk-1", "chad-agent")
    bundle = {
        "captain_log_tail": [
            {"ts": "bogus", "kind": "escalation_raised"},
            {"ts": _ts(10), "kind": "dispatch"},
        ],
    }
    with patch("week_intake.status.get_app_status_http", return_value=bundle):
        d = per_item_captain_detail(item)
    assert d.last_captain_action == "dispatch"
    # Escalation still raised — nothing resolved it.
    assert d.latest_meaningful_is_escalate is True


def test_detail_log_invalid_ts_escalation_cleared_by_valid_resolved() -> None:
    """A newer (valid-ts) escalation_resolved DOES clear the older
    invalid-ts escalation — invalid-ts entries are treated as older."""
    item = _routed_item("wk-1", "chad-agent")
    bundle = {
        "captain_log_tail": [
            {"ts": "bogus", "kind": "escalation_raised"},
            {"ts": _ts(10), "kind": "escalation_resolved"},
        ],
    }
    with patch("week_intake.status.get_app_status_http", return_value=bundle):
        d = per_item_captain_detail(item)
    assert d.latest_meaningful_is_escalate is False


def test_detail_log_all_invalid_ts_falls_back_to_original_order() -> None:
    item = _routed_item("wk-1", "chad-agent")
    bundle = {
        "captain_log_tail": [
            {"ts": "bogus", "kind": "dispatch"},
            {"ts": "also-bogus", "kind": "validate"},
        ],
    }
    with patch("week_intake.status.get_app_status_http", return_value=bundle):
        d = per_item_captain_detail(item)
    # First entry treated as newest.
    assert d.last_captain_action == "dispatch"


# ----- escalation detection ------------------------------------------------


def test_detail_escalation_via_escalation_raised_kind() -> None:
    item = _routed_item("wk-1", "chad-agent")
    bundle = {
        "captain_log_tail": [
            {"ts": _ts(60), "kind": "dispatch"},
            {"ts": _ts(5), "kind": "escalation_raised", "rationale": "stuck"},
        ],
    }
    with patch("week_intake.status.get_app_status_http", return_value=bundle):
        d = per_item_captain_detail(item)
    assert d.latest_meaningful_is_escalate is True
    assert d.needs_attention is True
    assert d.attention_reason == "escalation"


def test_detail_escalation_via_validate_verdict_escalate() -> None:
    item = _routed_item("wk-1", "chad-agent")
    bundle = {
        "captain_log_tail": [
            {"ts": _ts(5), "kind": "validate", "verdict": "escalate"},
        ],
    }
    with patch("week_intake.status.get_app_status_http", return_value=bundle):
        d = per_item_captain_detail(item)
    assert d.latest_meaningful_is_escalate is True
    assert d.attention_reason == "escalation"


def test_detail_escalation_resolved_clears_attention() -> None:
    item = _routed_item("wk-1", "chad-agent")
    bundle = {
        "captain_log_tail": [
            {"ts": _ts(60), "kind": "escalation_raised"},
            {"ts": _ts(5), "kind": "escalation_resolved"},
        ],
    }
    with patch("week_intake.status.get_app_status_http", return_value=bundle):
        d = per_item_captain_detail(item)
    assert d.latest_meaningful_is_escalate is False
    assert d.needs_attention is False


def test_detail_validate_pass_after_escalation_does_not_clear() -> None:
    """A normal validate after an escalation does NOT auto-clear it; only
    explicit escalation_resolved clears."""
    item = _routed_item("wk-1", "chad-agent")
    bundle = {
        "captain_log_tail": [
            {"ts": _ts(60), "kind": "escalation_raised"},
            {"ts": _ts(5), "kind": "validate", "verdict": "pass"},
        ],
    }
    with patch("week_intake.status.get_app_status_http", return_value=bundle):
        d = per_item_captain_detail(item)
    assert d.latest_meaningful_is_escalate is True


def test_detail_unknown_kind_does_not_crash_or_escalate() -> None:
    item = _routed_item("wk-1", "chad-agent")
    bundle = {
        "captain_log_tail": [
            {"ts": _ts(5), "kind": "future_unknown_kind", "rationale": "?"},
        ],
    }
    with patch("week_intake.status.get_app_status_http", return_value=bundle):
        d = per_item_captain_detail(item)
    assert d.last_captain_action == "future_unknown_kind"
    assert d.last_meaningful_action == "future_unknown_kind"
    assert d.latest_meaningful_is_escalate is False


# ----- attention precedence -----------------------------------------------


def test_detail_escalation_beats_active_pause() -> None:
    item = _routed_item("wk-1", "chad-agent")
    bundle = {
        "paused_until": _future_iso(60),
        "captain_log_tail": [{"ts": _ts(5), "kind": "escalation_raised"}],
    }
    with patch("week_intake.status.get_app_status_http", return_value=bundle):
        d = per_item_captain_detail(item)
    assert d.needs_attention is True
    assert d.attention_reason == "escalation"
    assert d.pause_active is True  # underlying field still set


def test_detail_pause_beats_pause_parse_error() -> None:
    # Logically can't both be set in one bundle, but precedence is well-defined.
    item = _routed_item("wk-1", "chad-agent")
    bundle = {"paused_until": _future_iso(30)}
    with patch("week_intake.status.get_app_status_http", return_value=bundle):
        d = per_item_captain_detail(item)
    assert d.attention_reason == "pause"


# ----- malformed-container tolerance --------------------------------------


def test_detail_log_tail_string_does_not_crash() -> None:
    item = _routed_item("wk-1", "chad-agent")
    bundle = {"captain_log_tail": "not-a-list"}
    with patch("week_intake.status.get_app_status_http", return_value=bundle):
        d = per_item_captain_detail(item)
    assert d.last_captain_action is None
    assert d.last_meaningful_action is None
    assert d.latest_meaningful_is_escalate is False


def test_detail_log_tail_dict_does_not_crash() -> None:
    item = _routed_item("wk-1", "chad-agent")
    bundle = {"captain_log_tail": {"oops": "wrong shape"}}
    with patch("week_intake.status.get_app_status_http", return_value=bundle):
        d = per_item_captain_detail(item)
    assert d.last_captain_action is None


def test_detail_log_non_dict_entries_dropped() -> None:
    item = _routed_item("wk-1", "chad-agent")
    bundle = {
        "captain_log_tail": [
            "scalar-row-ignored",
            {"ts": _ts(5), "kind": "dispatch"},
            42,
        ],
    }
    with patch("week_intake.status.get_app_status_http", return_value=bundle):
        d = per_item_captain_detail(item)
    assert d.last_captain_action == "dispatch"


def test_detail_non_string_kind_filtered() -> None:
    item = _routed_item("wk-1", "chad-agent")
    bundle = {
        "captain_log_tail": [
            {"ts": _ts(5), "kind": 12345, "rationale": "garbage kind"},
        ],
    }
    with patch("week_intake.status.get_app_status_http", return_value=bundle):
        d = per_item_captain_detail(item)
    # Non-string kind → no captain_action label, no meaningful_action.
    assert d.last_captain_action is None
    assert d.last_meaningful_action is None


def test_detail_non_string_rationale_dropped() -> None:
    item = _routed_item("wk-1", "chad-agent")
    bundle = {
        "captain_log_tail": [
            {"ts": _ts(5), "kind": "dispatch", "rationale": 42},
        ],
    }
    with patch("week_intake.status.get_app_status_http", return_value=bundle):
        d = per_item_captain_detail(item)
    assert d.last_meaningful_action == "dispatch"
    assert d.last_action_rationale is None


def test_detail_admiral_notes_non_list_treated_as_no_note() -> None:
    item = _routed_item("wk-1", "chad-agent", note_id="note-X")
    bundle = {
        "admiral_notes_queued": "should-be-a-list",
        "admiral_notes_consumed": {"also": "wrong"},
    }
    with patch("week_intake.status.get_app_status_http", return_value=bundle):
        d = per_item_captain_detail(item)
    assert d.note_status == "no_note"


def test_detail_admiral_notes_entries_without_note_id() -> None:
    item = _routed_item("wk-1", "chad-agent", note_id="note-X")
    bundle = {
        "admiral_notes_queued": [{"foo": "bar"}, {"note_id": 42}, "string-row"],
        "admiral_notes_consumed": [],
    }
    with patch("week_intake.status.get_app_status_http", return_value=bundle):
        d = per_item_captain_detail(item)
    assert d.note_status == "no_note"


# ----- bundle cache --------------------------------------------------------


def test_rollup_caches_bundle_for_same_app() -> None:
    """Two routed items pointing at the same app → 1 GET total."""
    items = [
        _routed_item("wk-1", "chad-agent", note_id="note-A"),
        _routed_item("wk-2", "chad-agent", note_id="note-B"),
    ]
    bundle = {
        "admiral_notes_queued": [{"note_id": "note-A"}, {"note_id": "note-B"}],
    }
    with patch(
        "week_intake.status.get_app_status_http", return_value=bundle
    ) as m:
        report = rollup(items)
    assert m.call_count == 1
    rows = {r["item_id"]: r for r in report["items"]}
    assert rows["wk-1"]["captain_note_status"] == "queued"
    assert rows["wk-2"]["captain_note_status"] == "queued"


def test_rollup_caches_unreachable_sentinel() -> None:
    """Unreachable captain → second item with same app does not retry."""
    from week_intake.captain_client import CaptainError

    items = [
        _routed_item("wk-1", "chad-agent"),
        _routed_item("wk-2", "chad-agent"),
    ]
    with patch(
        "week_intake.status.get_app_status_http", side_effect=CaptainError("dead")
    ) as m:
        report = rollup(items)
    assert m.call_count == 1
    for r in report["items"]:
        assert r["captain_note_status"] == "unreachable"
    # captain_unreachable counts items pointing at unreachable apps.
    assert report["totals"]["captain_unreachable"] == 2


def test_rollup_passes_short_timeout_to_captain() -> None:
    item = _routed_item("wk-1", "chad-agent", note_id="note-A")
    bundle = {"admiral_notes_queued": [{"note_id": "note-A"}]}
    with patch(
        "week_intake.status.get_app_status_http", return_value=bundle
    ) as m:
        rollup([item])
    # Timeout kwarg must be 3.0 (cycle 2 ceiling), not the default 5.0.
    assert m.call_count == 1
    _args, kwargs = m.call_args
    assert kwargs.get("timeout") == 3.0


# ----- rollup totals + JSON shape stability -------------------------------


def test_rollup_totals_needs_attention_present_even_when_zero() -> None:
    item = WeekItem(item_id="wk-1", week="2026-W19", raw_text="x", state="parsed")
    report = rollup([item])
    assert report["totals"]["needs_attention"] == 0
    # Existing keys preserved.
    assert "items" in report["totals"]
    assert "routed" in report["totals"]
    assert "captain_unreachable" in report["totals"]


def test_rollup_row_has_all_cycle2_fields_with_defaults() -> None:
    item = WeekItem(item_id="wk-1", week="2026-W19", raw_text="x", state="parsed")
    report = rollup([item])
    row = report["items"][0]
    expected_keys = {
        "slice_in_flight",
        "pause_active",
        "pause_reason",
        "pause_parse_error",
        "last_captain_action",
        "last_meaningful_action",
        "last_action_ts",
        "last_action_rationale",
        "latest_meaningful_is_escalate",
        "needs_attention",
        "attention_reason",
    }
    for k in expected_keys:
        assert k in row, f"missing cycle-2 key: {k}"
    assert row["pause_active"] is False
    assert row["needs_attention"] is False
    assert row["attention_reason"] is None


def test_rollup_needs_attention_count_matches_rows() -> None:
    items = [
        _routed_item("wk-1", "app-1", note_id="n-1"),
        _routed_item("wk-2", "app-2", note_id="n-2"),
        _routed_item("wk-3", "app-3", note_id="n-3"),
    ]

    def fake_get(app_id: str, **_kwargs):
        if app_id == "app-1":
            return {"paused_until": _future_iso(30)}
        if app_id == "app-2":
            return {
                "captain_log_tail": [{"ts": _ts(5), "kind": "escalation_raised"}],
            }
        return {}  # app-3 quiet

    with patch("week_intake.status.get_app_status_http", side_effect=fake_get):
        report = rollup(items)
    assert report["totals"]["needs_attention"] == 2


def test_rollup_full_rationale_preserved_in_json() -> None:
    item = _routed_item("wk-1", "chad-agent")
    long_rationale = "x" * 500
    bundle = {
        "captain_log_tail": [
            {"ts": _ts(5), "kind": "dispatch", "rationale": long_rationale},
        ],
    }
    with patch("week_intake.status.get_app_status_http", return_value=bundle):
        report = rollup([item])
    assert report["items"][0]["last_action_rationale"] == long_rationale
