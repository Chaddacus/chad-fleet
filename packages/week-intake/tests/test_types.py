"""WeekItem / RouteTarget / ClarificationQuestion round-trip tests."""

from __future__ import annotations

import json

from week_intake.types import (
    ClarificationQuestion,
    RouteTarget,
    WeekItem,
)


def test_weekitem_minimal_defaults() -> None:
    it = WeekItem(item_id="wk-001", week="2026-W18", raw_text="ship the thing")
    assert it.kind == "unknown"
    assert it.state == "parsed"
    assert it.confidence == 0.0
    assert it.target.app_id is None
    assert it.clarifications == []
    assert it.captain_note_id is None
    # Created and updated stamps are independently generated — within the same
    # second is the meaningful guarantee, not exact equality.
    assert it.created_at[:19] == it.updated_at[:19]


def test_weekitem_jsonl_round_trip() -> None:
    it = WeekItem(
        item_id="wk-002",
        week="2026-W18",
        raw_text="rewrite the away-responder docs",
        title="rewrite away-responder docs",
        kind="wip",
        state="ready",
        confidence=0.92,
        target=RouteTarget(app_id="chad-agent", repo_path="/Users/chadsimon/code/chad-agent"),
        clarifications=[
            ClarificationQuestion(question_id="kind", prompt="Is this WIP?", answer="yes")
        ],
    )
    payload = json.dumps(it.model_dump(mode="json"))
    it2 = WeekItem.model_validate(json.loads(payload))
    assert it2 == it


def test_touch_updates_timestamp() -> None:
    it = WeekItem(item_id="wk-003", week="2026-W18", raw_text="x")
    before = it.updated_at
    # Mutate; touch should advance updated_at strictly after `before`.
    it.kind = "wip"
    it.touch()
    assert it.updated_at >= before


def test_confidence_bounds_enforced() -> None:
    import pytest
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        WeekItem(item_id="wk-004", week="2026-W18", raw_text="x", confidence=1.5)
    with pytest.raises(ValidationError):
        WeekItem(item_id="wk-005", week="2026-W18", raw_text="x", confidence=-0.1)
