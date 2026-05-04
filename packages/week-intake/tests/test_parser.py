"""Parser tests with the LLM call mocked.

We don't want unit tests to invoke ``claude -p`` — that's a real
subprocess + network round-trip. Patch ``claude_json`` and verify
that ``parse_dump`` shapes the output correctly.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from week_intake.parser import parse_dump


def _fake_payload() -> dict:
    return {
        "items": [
            {
                "title": "rewrite away-responder docs",
                "raw_text": "rewrite the away-responder docs in chad-agent",
                "kind": "wip",
                "confidence": 0.9,
                "candidate_app_id": "chad-agent",
                "first_question": None,
            },
            {
                "title": "scaffold spark-marketing site",
                "raw_text": "set up a marketing site for spark-of-defiance",
                "kind": "greenfield",
                "confidence": 0.55,  # low → needs_clarification
                "candidate_app_id": None,
                "first_question": "Should this be a new repo, or part of spark-of-defiance?",
            },
            {
                "title": "decide pricing tier for chadacys",
                "raw_text": "settle on a pricing tier for the chadacys SaaS",
                "kind": "decision",
                "confidence": 0.8,
                "candidate_app_id": None,
                "first_question": None,
            },
        ]
    }


def test_parse_dump_maps_kinds_and_targets(tmp_path) -> None:
    with patch("week_intake.parser.claude_json", return_value=_fake_payload()):
        items = parse_dump("(brain dump)", week="2026-W19", base=tmp_path)

    assert [it.item_id for it in items] == ["wk-001", "wk-002", "wk-003"]

    a, b, c = items
    assert a.kind == "wip"
    assert a.state == "parsed"
    assert a.target.app_id == "chad-agent"
    assert a.clarifications == []

    assert b.kind == "greenfield"
    assert b.state == "needs_clarification"  # confidence < 0.65
    assert len(b.clarifications) == 1
    assert b.clarifications[0].prompt.startswith("Should this be")

    assert c.kind == "decision"
    assert c.state == "parsed"


def test_parse_dump_continues_id_sequence(tmp_path) -> None:
    """If the week folder already has items, parse_dump must not collide."""
    from week_intake.protocol import WeekFolder
    from week_intake.types import WeekItem

    folder = WeekFolder(week="2026-W19", base=tmp_path)
    folder.append_item(WeekItem(item_id="wk-007", week="2026-W19", raw_text="prior"))

    with patch("week_intake.parser.claude_json", return_value=_fake_payload()):
        items = parse_dump("(more)", week="2026-W19", base=tmp_path)

    assert [it.item_id for it in items] == ["wk-008", "wk-009", "wk-010"]


def test_parse_dump_rejects_non_numeric_confidence(tmp_path) -> None:
    """Hallucinated string confidence (\"high\") must raise LLMError, not crash."""
    from week_intake.llm import LLMError

    payload = {
        "items": [
            {
                "title": "x",
                "raw_text": "x",
                "kind": "wip",
                "confidence": "high",  # garbage shape
                "candidate_app_id": None,
                "first_question": None,
            }
        ]
    }
    with patch("week_intake.parser.claude_json", return_value=payload):
        with pytest.raises(LLMError) as exc_info:
            parse_dump("x", week="2026-W19", base=tmp_path)
    assert "items[0]" in str(exc_info.value)


def test_parse_dump_rejects_non_list_items(tmp_path) -> None:
    from week_intake.llm import LLMError

    payload = {"items": "not a list"}
    with patch("week_intake.parser.claude_json", return_value=payload):
        with pytest.raises(LLMError):
            parse_dump("x", week="2026-W19", base=tmp_path)


def test_parse_dump_rejects_non_dict_item(tmp_path) -> None:
    from week_intake.llm import LLMError

    payload = {"items": ["not a dict"]}
    with patch("week_intake.parser.claude_json", return_value=payload):
        with pytest.raises(LLMError):
            parse_dump("x", week="2026-W19", base=tmp_path)


def test_parse_dump_falls_back_on_unknown_kind(tmp_path) -> None:
    payload = {
        "items": [
            {
                "title": "weird thing",
                "raw_text": "something",
                "kind": "not_a_real_kind",
                "confidence": 0.99,
                "candidate_app_id": None,
                "first_question": None,
            }
        ]
    }
    with patch("week_intake.parser.claude_json", return_value=payload):
        items = parse_dump("x", week="2026-W19", base=tmp_path)
    assert items[0].kind == "unknown"
