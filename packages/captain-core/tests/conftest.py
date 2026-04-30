"""Shared fixtures for captain-core tests."""

from __future__ import annotations

import textwrap
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from state_aggregator import AppSnapshot, FleetState


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ago(days: int) -> datetime:
    return datetime.now(UTC) - timedelta(days=days)


def make_app(
    app_id: str = "app-a",
    name: str = "App A",
    state: str = "active",
    mode: str = "continuous",
    cadence: str = "daily",
    owner_brand: str = "chadacys",
    days_since_progress: int = 0,
    blocked_reason: str | None = None,
    metadata: dict | None = None,
) -> AppSnapshot:
    return AppSnapshot(
        id=app_id,
        name=name,
        state=state,
        mode=mode,
        cadence=cadence,
        owner_brand=owner_brand,
        last_progress_at=_ago(days_since_progress),
        blocked_reason=blocked_reason,
        metadata=metadata or {},
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def fleet_empty() -> FleetState:
    return FleetState(
        generated_at=datetime.now(UTC),
        apps=[],
        inbox_recent=[],
        summary={"total_apps": 0, "by_state": {}, "blocked_count": 0},
    )


@pytest.fixture()
def fleet_simple() -> FleetState:
    apps = [
        make_app("spark", "Spark Cover", state="active", mode="continuous", days_since_progress=1),
        make_app("cw-gateway", "CW Gateway", state="active", mode="event_driven", days_since_progress=5),
        make_app("sdvosb-cert", "SDVOSB Cert", state="blocked", mode="continuous",
                 days_since_progress=4, blocked_reason="waiting for VA response"),
    ]
    return FleetState(
        generated_at=datetime.now(UTC),
        apps=apps,
        inbox_recent=[],
        summary={
            "total_apps": len(apps),
            "by_state": {"active": 2, "blocked": 1},
            "blocked_count": 1,
        },
    )


@pytest.fixture()
def fleet_all_modes() -> FleetState:
    apps = [
        make_app("cont-fresh", "Continuous Fresh", mode="continuous", days_since_progress=1),
        make_app("cont-warn", "Continuous Warn", mode="continuous", days_since_progress=4),
        make_app("cont-crit", "Continuous Crit", mode="continuous", days_since_progress=8),
        make_app("event-fresh", "Event Fresh", mode="event_driven", days_since_progress=5),
        make_app("event-warn", "Event Warn", mode="event_driven", days_since_progress=15),
        make_app("event-crit", "Event Crit", mode="event_driven", days_since_progress=31),
        make_app("archived-app", "Archived App", mode="archived", state="archived", days_since_progress=60),
        make_app("shipped-app", "Shipped App", mode="shipped", state="shipped", days_since_progress=90),
        make_app("blocked-app", "Blocked App", state="blocked", mode="continuous",
                 days_since_progress=2, blocked_reason="dependency missing"),
    ]
    return FleetState(
        generated_at=datetime.now(UTC),
        apps=apps,
        inbox_recent=[],
        summary={"total_apps": len(apps), "by_state": {}, "blocked_count": 1},
    )


@pytest.fixture()
def fleet_launch_driven() -> FleetState:
    near_launch = (datetime.now(UTC) + timedelta(days=5)).strftime("%Y-%m-%d")
    far_launch = (datetime.now(UTC) + timedelta(days=30)).strftime("%Y-%m-%d")
    apps = [
        make_app("near-stall", "Near Launch Stall", mode="launch_driven", days_since_progress=2,
                 metadata={"launch_date": near_launch}),
        make_app("far-ok", "Far Launch OK", mode="launch_driven", days_since_progress=2,
                 metadata={"launch_date": far_launch}),
    ]
    return FleetState(
        generated_at=datetime.now(UTC),
        apps=apps,
        inbox_recent=[],
        summary={"total_apps": 2, "by_state": {}, "blocked_count": 0},
    )


@pytest.fixture()
def fleet_with_playbook_match() -> FleetState:
    apps = [
        make_app(
            "book-app",
            "Spark Book Launch",
            mode="launch_driven",
            owner_brand="chadacys",
            days_since_progress=0,
            metadata={
                "launch_date": (datetime.now(UTC) + timedelta(days=45)).strftime("%Y-%m-%d"),
                "playbook_slugs": ["indie-author-launch", "linkedin-algorithm"],
            },
        ),
        make_app(
            "saas-app",
            "CW Gateway SaaS",
            mode="continuous",
            owner_brand="cloudwarriors",
            days_since_progress=0,
            metadata={"playbook_slugs": ["b2b-saas-gtm"]},
        ),
    ]
    return FleetState(
        generated_at=datetime.now(UTC),
        apps=apps,
        inbox_recent=[],
        summary={"total_apps": 2, "by_state": {}, "blocked_count": 0},
    )


@pytest.fixture()
def tmp_playbooks_dir(tmp_path: Path) -> Path:
    """Write minimal fixture playbook files to a temp directory."""
    pb_dir = tmp_path / "playbooks"
    pb_dir.mkdir()

    (pb_dir / "test-playbook.md").write_text(
        textwrap.dedent("""\
        ---
        slug: test-playbook
        title: Test Playbook
        domain: testing
        applies_to:
          - unit-tests
          - integration-tests
        last_updated: 2026-04-01
        ---

        # Test Playbook

        ## Summary

        A playbook used only for unit testing captain-core.

        ## When to consult

        - The test suite is failing.
        - Coverage has dropped below 80%.
        - A new module has been added without tests.

        ## Recommendations

        1. **Run the full test suite first.**

           Before diving into implementation, always run the existing suite. Identify
           pre-existing failures so you do not conflate them with regressions you introduced.

        2. **Add a test for every new public function.**

           Each public function should have at least one happy-path test and one edge-case test.
           Untested public API is a maintenance liability.

        ## Anti-patterns

        - Skipping tests because they are "obvious."
        - Commenting out failing tests instead of fixing them.

        ## Decision rubric

        | Situation | Action |
        |---|---|
        | Coverage below 80% | Add missing tests before merging |

        ## Sources

        - https://docs.pytest.org/
        """),
        encoding="utf-8",
    )

    (pb_dir / "another-playbook.md").write_text(
        textwrap.dedent("""\
        ---
        slug: another-playbook
        title: Another Playbook
        domain: saas-commercial
        applies_to:
          - saas-product
          - freemium
        last_updated: 2026-04-01
        ---

        # Another Playbook

        ## Summary

        A second fixture playbook for multi-playbook matching tests.

        ## When to consult

        - Product growth has stalled.
        - MRR is flat for 60+ days.

        ## Recommendations

        1. **Define your ICP before anything else.**

           Ideal customer profile definition is the foundation of GTM. Without it, every
           channel decision is a guess.

        ## Anti-patterns

        - Running ads before ICP is defined.

        ## Sources

        - https://example.com/
        """),
        encoding="utf-8",
    )

    return pb_dir


@pytest.fixture()
def real_playbooks_dir() -> Path:
    """Path to the real captain-playbooks directory."""
    return Path(__file__).parent.parent.parent / "captain-playbooks" / "playbooks"
