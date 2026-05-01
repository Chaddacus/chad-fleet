"""Tests for the session summary aggregator."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from chad_captain.protocol import (
    AppWorkspace,
    CaptainLogEntry,
    FeatureBacklog,
    FeatureBacklogItem,
    append_captain_log,
    write_feature_backlog,
)
from chad_captain.summary import build_session_summary, _parse_window


@pytest.fixture()
def ws(tmp_path: Path) -> AppWorkspace:
    w = AppWorkspace("sum-test", base=tmp_path)
    w.ensure()
    return w


def _ts(hours_ago: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=hours_ago)).isoformat()


def test_parse_window_accepts_units() -> None:
    assert _parse_window("24h") == timedelta(hours=24)
    assert _parse_window("7d") == timedelta(days=7)
    assert _parse_window("30m") == timedelta(minutes=30)
    assert _parse_window("all") is None


def test_parse_window_rejects_unknown() -> None:
    with pytest.raises(ValueError):
        _parse_window("3y")


def test_empty_workspace_says_no_activity(ws: AppWorkspace) -> None:
    s = build_session_summary(ws, window="24h")
    assert s.slices_total == 0
    assert "No captain activity" in s.narrative
    assert "No activity" in s.headline


def test_saturation_only_says_awaiting_direction(ws: AppWorkspace) -> None:
    append_captain_log(ws, CaptainLogEntry(
        ts=_ts(0.5), app_id="sum-test", slice_id=None,
        kind="escalation_raised",
        rationale="backlog saturated — 14 features shipped, 0 queued",
        references={"event": "backlog_saturated", "shipped_count": "14"},
    ))
    s = build_session_summary(ws, window="24h")
    assert s.saturation_events == 1
    # No slices/PRs in window — narrative pivots to saturation guidance
    assert "awaiting direction" in s.headline.lower() or "awaiting direction" in s.narrative.lower()


def test_summary_aggregates_slices_and_features(ws: AppWorkspace) -> None:
    # Setup: 3 accepted slices, 1 soft, 1 rejected, all in last hour
    for v, dpp in [("accept", 1.2), ("accept", 0.8), ("accept", 0.0),
                    ("soft_accept", 0.0), ("reject_retry", -0.5)]:
        append_captain_log(ws, CaptainLogEntry(
            ts=_ts(0.5), app_id="sum-test", slice_id="s",
            kind="validate", verdict=v, rubric_delta_pp=dpp,
            rationale="x",
        ))
    # Two features shipped recently
    write_feature_backlog(ws, FeatureBacklog(
        app_id="sum-test",
        items=[
            FeatureBacklogItem(
                id="fb-001", title="Cover A/B testing", status="shipped",
                shipped_in="https://github.com/x/y/pull/100",
                shipped_at=_ts(0.5), priority=0.85,
            ),
            FeatureBacklogItem(
                id="fb-002", title="Email automation", status="shipped",
                shipped_in="https://github.com/x/y/pull/101",
                shipped_at=_ts(0.4), priority=0.7,
            ),
            FeatureBacklogItem(
                id="fb-003", title="Old shipped — outside window",
                status="shipped",
                shipped_in="https://github.com/x/y/pull/50",
                shipped_at=_ts(72), priority=0.5,
            ),
            FeatureBacklogItem(
                id="fb-004", title="Still queued", priority=0.5,
            ),
        ],
    ))
    s = build_session_summary(ws, window="24h")
    assert s.slices_total == 5
    assert s.slices_accepted == 3
    assert s.slices_soft_accepted == 1
    assert s.slices_rejected == 1
    assert s.rubric_delta_pp == 1.5  # 1.2 + 0.8 + 0 + 0 - 0.5
    # Two features shipped in window, one outside
    titles = {f.title for f in s.features_shipped}
    assert titles == {"Cover A/B testing", "Email automation"}
    assert "Cover A/B testing" in s.narrative
    assert "5 slices" in s.headline


def test_summary_pairs_pr_open_with_merge(ws: AppWorkspace) -> None:
    pr_url = "https://github.com/x/y/pull/200"
    append_captain_log(ws, CaptainLogEntry(
        ts=_ts(2), app_id="sum-test", slice_id=None,
        kind="pull_request_opened",
        rationale=f"PR opened: {pr_url}",
        references={"event": "roadmap_complete_pr", "pr_url": pr_url,
                     "pr_title": "Ship cover A/B testing"},
    ))
    append_captain_log(ws, CaptainLogEntry(
        ts=_ts(1.5), app_id="sum-test", slice_id=None,
        kind="pull_request_merged",
        rationale="merged",
        references={"pr_url": pr_url},
    ))
    s = build_session_summary(ws, window="24h")
    assert len(s.prs_merged) == 1
    assert s.prs_merged[0].pr_url == pr_url
    assert s.prs_merged[0].title == "Ship cover A/B testing"


def test_summary_window_filters_old_events(ws: AppWorkspace) -> None:
    append_captain_log(ws, CaptainLogEntry(
        ts=_ts(48), app_id="sum-test", slice_id="old",
        kind="validate", verdict="accept", rubric_delta_pp=2.0,
        rationale="too old",
    ))
    append_captain_log(ws, CaptainLogEntry(
        ts=_ts(0.1), app_id="sum-test", slice_id="new",
        kind="validate", verdict="accept", rubric_delta_pp=0.5,
        rationale="recent",
    ))
    s = build_session_summary(ws, window="24h")
    assert s.slices_total == 1
    assert s.rubric_delta_pp == 0.5

    s_all = build_session_summary(ws, window="all")
    assert s_all.slices_total == 2
    assert s_all.rubric_delta_pp == 2.5


def test_summary_counts_escalations_and_breaker(ws: AppWorkspace) -> None:
    append_captain_log(ws, CaptainLogEntry(
        ts=_ts(0.5), app_id="sum-test", kind="escalation_raised",
        rationale="circuit breaker tripped",
        references={"event": "circuit_breaker_tripped"},
    ))
    append_captain_log(ws, CaptainLogEntry(
        ts=_ts(0.3), app_id="sum-test", kind="escalation_raised",
        rationale="backlog saturated",
        references={"event": "backlog_saturated"},
    ))
    append_captain_log(ws, CaptainLogEntry(
        ts=_ts(0.2), app_id="sum-test", kind="note_received",
        rationale="admiral note",
    ))
    s = build_session_summary(ws, window="24h")
    assert s.escalations == 2
    assert s.circuit_breaker_trips == 1
    assert s.saturation_events == 1
    assert s.admiral_notes_received == 1


def test_pr_backlog_cross_link(ws: AppWorkspace) -> None:
    pr_url = "https://github.com/x/y/pull/300"
    append_captain_log(ws, CaptainLogEntry(
        ts=_ts(2), app_id="sum-test", kind="pull_request_opened",
        rationale=f"PR opened: {pr_url}",
        references={"pr_url": pr_url, "pr_title": "Ship features"},
    ))
    append_captain_log(ws, CaptainLogEntry(
        ts=_ts(1), app_id="sum-test", kind="pull_request_merged",
        rationale="merged",
        references={"pr_url": pr_url},
    ))
    write_feature_backlog(ws, FeatureBacklog(
        app_id="sum-test",
        items=[
            FeatureBacklogItem(id="fb-001", title="A", status="shipped",
                               shipped_in=pr_url, shipped_at=_ts(0.9),
                               priority=0.5),
            FeatureBacklogItem(id="fb-002", title="B", status="shipped",
                               shipped_in=pr_url, shipped_at=_ts(0.9),
                               priority=0.5),
        ],
    ))
    s = build_session_summary(ws, window="24h")
    assert len(s.prs_merged) == 1
    assert sorted(s.prs_merged[0].backlog_item_ids) == ["fb-001", "fb-002"]
