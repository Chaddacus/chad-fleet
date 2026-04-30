"""Tests for brief.py: daily brief composition."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from state_aggregator import FleetState

from captain_core.brief import compose_daily_brief
from captain_core.playbooks import load_playbooks_dir
from captain_core.types import Brief
from tests.conftest import make_app


def _fleet(*apps) -> FleetState:
    return FleetState(
        generated_at=datetime.now(UTC),
        apps=list(apps),
        inbox_recent=[],
        summary={"total_apps": len(apps), "by_state": {}, "blocked_count": 0},
    )


# ---------------------------------------------------------------------------
# Structural tests
# ---------------------------------------------------------------------------

def test_compose_returns_brief(tmp_playbooks_dir) -> None:
    pbs = load_playbooks_dir(tmp_playbooks_dir)
    brief = compose_daily_brief(_fleet(), pbs)
    assert isinstance(brief, Brief)


def test_brief_generated_at_is_recent(tmp_playbooks_dir) -> None:
    pbs = load_playbooks_dir(tmp_playbooks_dir)
    brief = compose_daily_brief(_fleet(), pbs)
    delta = (datetime.now(UTC) - brief.generated_at).total_seconds()
    assert delta < 5


def test_brief_has_headline(tmp_playbooks_dir) -> None:
    pbs = load_playbooks_dir(tmp_playbooks_dir)
    brief = compose_daily_brief(_fleet(), pbs)
    assert isinstance(brief.headline, str)
    assert len(brief.headline) > 0


def test_brief_nominal_headline_no_stalls(tmp_playbooks_dir) -> None:
    pbs = load_playbooks_dir(tmp_playbooks_dir)
    app = make_app(mode="continuous", days_since_progress=1)
    brief = compose_daily_brief(_fleet(app), pbs)
    assert "nominal" in brief.headline.lower() or "no stall" in brief.headline.lower()


def test_brief_headline_includes_critical_count(real_playbooks_dir) -> None:
    pbs = load_playbooks_dir(real_playbooks_dir)
    app = make_app("spark", "Spark Cover", mode="continuous", days_since_progress=8)
    brief = compose_daily_brief(_fleet(app), pbs)
    assert "critical" in brief.headline.lower()


# ---------------------------------------------------------------------------
# apps_summary
# ---------------------------------------------------------------------------

def test_apps_summary_one_entry_per_app(tmp_playbooks_dir) -> None:
    pbs = load_playbooks_dir(tmp_playbooks_dir)
    apps = [
        make_app("a1", "App One"),
        make_app("a2", "App Two"),
        make_app("a3", "App Three"),
    ]
    brief = compose_daily_brief(_fleet(*apps), pbs)
    assert len(brief.apps_summary) == 3


def test_apps_summary_contains_required_keys(tmp_playbooks_dir) -> None:
    pbs = load_playbooks_dir(tmp_playbooks_dir)
    app = make_app("x", "X App")
    brief = compose_daily_brief(_fleet(app), pbs)
    entry = brief.apps_summary[0]
    for key in ("app_id", "app_name", "state", "last_progress_days"):
        assert key in entry, f"Missing key {key!r} in apps_summary entry"


def test_apps_summary_empty_on_empty_fleet(tmp_playbooks_dir) -> None:
    pbs = load_playbooks_dir(tmp_playbooks_dir)
    brief = compose_daily_brief(_fleet(), pbs)
    assert brief.apps_summary == []


# ---------------------------------------------------------------------------
# Stalls and next_actions
# ---------------------------------------------------------------------------

def test_brief_next_actions_capped_at_7(real_playbooks_dir) -> None:
    pbs = load_playbooks_dir(real_playbooks_dir)
    apps = [
        make_app(f"app-{i}", f"App {i}", metadata={"playbook_slugs": list(pbs.keys())})
        for i in range(3)
    ]
    brief = compose_daily_brief(_fleet(*apps), pbs)
    assert len(brief.next_actions) <= 7


def test_brief_recommended_slices_default_empty(tmp_playbooks_dir) -> None:
    pbs = load_playbooks_dir(tmp_playbooks_dir)
    brief = compose_daily_brief(_fleet(), pbs)
    assert brief.recommended_slices == []


def test_brief_inbox_recent_count_correct(tmp_playbooks_dir) -> None:
    from state_aggregator import InboxItem
    pbs = load_playbooks_dir(tmp_playbooks_dir)
    inbox = [
        InboxItem(ts=datetime.now(UTC), channel="zoom", severity="info", title="t", body="b")
        for _ in range(3)
    ]
    state = FleetState(
        generated_at=datetime.now(UTC),
        apps=[],
        inbox_recent=inbox,
        summary={"total_apps": 0, "by_state": {}, "blocked_count": 0},
    )
    brief = compose_daily_brief(state, pbs)
    assert brief.inbox_recent_count == 3


# ---------------------------------------------------------------------------
# use_llm=False path (default)
# ---------------------------------------------------------------------------

def test_use_llm_false_does_not_raise(real_playbooks_dir) -> None:
    pbs = load_playbooks_dir(real_playbooks_dir)
    app = make_app(metadata={"playbook_slugs": ["indie-author-launch"]})
    brief = compose_daily_brief(_fleet(app), pbs, use_llm=False)
    assert isinstance(brief.body, str)
    assert len(brief.body) > 0
