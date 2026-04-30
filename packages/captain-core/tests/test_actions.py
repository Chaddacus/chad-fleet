"""Tests for actions.py: next-action generation."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from state_aggregator import FleetState

from captain_core.actions import next_actions
from captain_core.playbooks import load_playbooks_dir
from captain_core.types import NextAction
from tests.conftest import make_app


def _fleet(*apps) -> FleetState:
    return FleetState(
        generated_at=datetime.now(UTC),
        apps=list(apps),
        inbox_recent=[],
        summary={"total_apps": len(apps), "by_state": {}, "blocked_count": 0},
    )


# ---------------------------------------------------------------------------
# Basic generation
# ---------------------------------------------------------------------------

def test_empty_fleet_returns_empty(tmp_playbooks_dir) -> None:
    pbs = load_playbooks_dir(tmp_playbooks_dir)
    actions = next_actions(_fleet(), pbs)
    assert actions == []


def test_returns_list_of_next_action(tmp_playbooks_dir, fleet_with_playbook_match) -> None:
    pbs = load_playbooks_dir(tmp_playbooks_dir)
    # Use explicit slugs that exist in tmp_playbooks_dir
    from tests.conftest import make_app
    app = make_app(metadata={"playbook_slugs": ["test-playbook"]})
    state = _fleet(app)
    actions = next_actions(state, pbs)
    assert all(isinstance(a, NextAction) for a in actions)


def test_cap_enforced(real_playbooks_dir) -> None:
    pbs = load_playbooks_dir(real_playbooks_dir)
    # Give the app explicit slugs pointing at all 6 playbooks to generate many candidates
    app = make_app(
        metadata={
            "playbook_slugs": [
                "indie-author-launch", "oss-marketing", "federal-contracting",
                "linkedin-algorithm", "b2b-saas-gtm", "sdvosb-paperwork",
            ]
        }
    )
    state = _fleet(app)
    actions = next_actions(state, pbs, cap=7)
    assert len(actions) <= 7


def test_cap_custom(real_playbooks_dir) -> None:
    pbs = load_playbooks_dir(real_playbooks_dir)
    app = make_app(
        metadata={"playbook_slugs": ["indie-author-launch", "linkedin-algorithm", "b2b-saas-gtm"]}
    )
    state = _fleet(app)
    actions = next_actions(state, pbs, cap=3)
    assert len(actions) <= 3


# ---------------------------------------------------------------------------
# Stall promotion
# ---------------------------------------------------------------------------

def test_critical_stall_produces_priority_1_action(real_playbooks_dir) -> None:
    pbs = load_playbooks_dir(real_playbooks_dir)
    # 8 days stalled on continuous mode → critical
    app = make_app("stalled-app", "Stalled App", mode="continuous", days_since_progress=8)
    state = _fleet(app)
    actions = next_actions(state, pbs)
    priorities = [a.priority for a in actions]
    assert 1 in priorities


def test_critical_stall_action_has_no_playbook_slug(real_playbooks_dir) -> None:
    pbs = load_playbooks_dir(real_playbooks_dir)
    app = make_app("stalled", "Stalled", mode="continuous", days_since_progress=8)
    state = _fleet(app)
    actions = next_actions(state, pbs)
    stall_actions = [a for a in actions if a.priority == 1 and a.playbook_slug is None]
    assert len(stall_actions) >= 1


# ---------------------------------------------------------------------------
# Priority ordering
# ---------------------------------------------------------------------------

def test_priority_ordering_ascending(real_playbooks_dir) -> None:
    pbs = load_playbooks_dir(real_playbooks_dir)
    app = make_app(
        "book", "Book", mode="continuous", days_since_progress=8,
        metadata={"playbook_slugs": ["indie-author-launch"]},
    )
    state = _fleet(app)
    actions = next_actions(state, pbs)
    priorities = [a.priority for a in actions]
    assert priorities == sorted(priorities)


def test_actions_have_rationale(real_playbooks_dir) -> None:
    pbs = load_playbooks_dir(real_playbooks_dir)
    app = make_app(metadata={"playbook_slugs": ["indie-author-launch"]})
    state = _fleet(app)
    actions = next_actions(state, pbs)
    for a in actions:
        assert a.rationale, f"Action {a.title!r} has empty rationale"


def test_playbook_slug_set_on_playbook_actions(real_playbooks_dir) -> None:
    pbs = load_playbooks_dir(real_playbooks_dir)
    app = make_app(metadata={"playbook_slugs": ["indie-author-launch"]})
    state = _fleet(app)
    actions = next_actions(state, pbs)
    pb_actions = [a for a in actions if a.playbook_slug is not None]
    assert len(pb_actions) >= 1
    for a in pb_actions:
        assert a.playbook_slug == "indie-author-launch"
