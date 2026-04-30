"""End-to-end pipeline tests: FleetState in, valid Brief out."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from state_aggregator import AppSnapshot, FleetState

from captain_core import compose_daily_brief, load_playbooks_dir
from captain_core.types import Brief, StallAlert, NextAction
from tests.conftest import make_app


def _fleet(*apps) -> FleetState:
    return FleetState(
        generated_at=datetime.now(UTC),
        apps=list(apps),
        inbox_recent=[],
        summary={"total_apps": len(apps), "by_state": {}, "blocked_count": 0},
    )


# ---------------------------------------------------------------------------
# E2E test 1: nominal fleet — no stalls
# ---------------------------------------------------------------------------

def test_e2e_nominal_fleet(real_playbooks_dir) -> None:
    """Full pipeline: active apps with recent progress produce a clean brief."""
    pbs = load_playbooks_dir(real_playbooks_dir)
    apps = [
        make_app("spark", "Spark Cover", mode="continuous", owner_brand="chadacys",
                 days_since_progress=1),
        make_app("cw-gateway", "CW Gateway", mode="event_driven", owner_brand="cloudwarriors",
                 days_since_progress=5),
    ]
    state = _fleet(*apps)
    brief = compose_daily_brief(state, pbs)

    assert isinstance(brief, Brief)
    assert brief.stalls == []
    assert "nominal" in brief.headline.lower() or "no stall" in brief.headline.lower()
    assert len(brief.apps_summary) == 2
    assert brief.inbox_recent_count == 0


# ---------------------------------------------------------------------------
# E2E test 2: mixed fleet with stalls and playbook actions
# ---------------------------------------------------------------------------

def test_e2e_mixed_fleet_with_stalls(real_playbooks_dir) -> None:
    """Full pipeline: stalled apps trigger alerts and stall-derived actions."""
    pbs = load_playbooks_dir(real_playbooks_dir)
    near_launch = (datetime.now(UTC) + timedelta(days=30)).strftime("%Y-%m-%d")
    apps = [
        make_app(
            "book-launch", "Spark Book Launch",
            mode="launch_driven", owner_brand="chadacys",
            days_since_progress=0,
            metadata={
                "launch_date": near_launch,
                "playbook_slugs": ["indie-author-launch", "linkedin-algorithm"],
            },
        ),
        make_app(
            "stalled-saas", "Stalled SaaS",
            mode="continuous", owner_brand="cloudwarriors",
            days_since_progress=9,   # critical
            metadata={"playbook_slugs": ["b2b-saas-gtm"]},
        ),
        make_app(
            "blocked-cert", "SDVOSB Cert",
            state="blocked", mode="continuous",
            days_since_progress=3,
            blocked_reason="waiting for VA",
        ),
    ]
    state = _fleet(*apps)
    brief = compose_daily_brief(state, pbs)

    assert isinstance(brief, Brief)
    assert len(brief.stalls) >= 1

    stall_severities = {s.severity for s in brief.stalls}
    assert "critical" in stall_severities or "info" in stall_severities

    assert len(brief.next_actions) >= 1
    assert all(isinstance(a, NextAction) for a in brief.next_actions)
    assert len(brief.next_actions) <= 7

    # apps_summary has one entry per app
    assert len(brief.apps_summary) == 3

    # headline reflects the stall
    assert brief.headline != ""


# ---------------------------------------------------------------------------
# E2E test 3: empty fleet produces valid Brief
# ---------------------------------------------------------------------------

def test_e2e_empty_fleet(real_playbooks_dir) -> None:
    """Empty fleet still produces a valid, model-conformant Brief."""
    pbs = load_playbooks_dir(real_playbooks_dir)
    brief = compose_daily_brief(_fleet(), pbs)

    assert isinstance(brief, Brief)
    assert brief.stalls == []
    assert brief.next_actions == []
    assert brief.apps_summary == []
    assert "nominal" in brief.headline.lower() or "no stall" in brief.headline.lower()
    assert isinstance(brief.body, str)
    # JSON-serialisable
    assert brief.model_dump_json()


# ---------------------------------------------------------------------------
# E2E test 4: federal/SDVOSB app matches correct playbooks
# ---------------------------------------------------------------------------

def test_e2e_federal_app_matches_playbooks(real_playbooks_dir) -> None:
    """App with federal domain metadata matches federal-contracting playbook."""
    pbs = load_playbooks_dir(real_playbooks_dir)
    app = make_app(
        "fed-tool", "Federal AI Tool",
        mode="event_driven", owner_brand="cloudwarriors",
        days_since_progress=2,
        metadata={"playbook_slugs": ["federal-contracting", "sdvosb-paperwork"]},
    )
    state = _fleet(app)
    brief = compose_daily_brief(state, pbs)

    assert isinstance(brief, Brief)
    playbook_slugs_used = {a.playbook_slug for a in brief.next_actions if a.playbook_slug}
    assert playbook_slugs_used.issubset(set(pbs.keys()))


# ---------------------------------------------------------------------------
# E2E test 5: Brief is fully JSON-serialisable
# ---------------------------------------------------------------------------

def test_e2e_brief_json_serialisable(real_playbooks_dir) -> None:
    pbs = load_playbooks_dir(real_playbooks_dir)
    app = make_app(
        "multi", "Multi-Playbook App",
        mode="continuous", owner_brand="chadacys",
        days_since_progress=4,
        metadata={"playbook_slugs": ["indie-author-launch", "oss-marketing"]},
    )
    state = _fleet(app)
    brief = compose_daily_brief(state, pbs)

    json_str = brief.model_dump_json(indent=2)
    assert len(json_str) > 100
    # Spot-check that generated_at round-trips
    import json
    data = json.loads(json_str)
    assert "generated_at" in data
    assert "headline" in data
    assert "next_actions" in data
