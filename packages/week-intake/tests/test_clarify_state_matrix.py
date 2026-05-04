"""State transition matrix: clarify behavior per WeekItemState."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from week_intake.classification import ReclassifyOutput
from week_intake.clarifier import ClarifyError, clarify_with_answer
from week_intake.protocol import WeekFolder
from week_intake.types import ClarificationQuestion, WeekItem, WeekItemState


@pytest.fixture
def folder(tmp_path, monkeypatch):
    monkeypatch.setenv("CHAD_WEEK_DIR", str(tmp_path / "week"))
    monkeypatch.setenv("CHAD_FLEET_APPS_DIR", str(tmp_path / "fleet"))
    return WeekFolder(week="2026-W19")


def _item(state: WeekItemState, *, with_question: bool = True) -> WeekItem:
    clars = []
    if with_question:
        clars.append(ClarificationQuestion(question_id="q001", prompt="?"))
    return WeekItem(
        item_id="wk-001",
        week="2026-W19",
        raw_text="x",
        state=state,
        clarifications=clars,
    )


def _ok_refresh(tmp_path) -> tuple[ReclassifyOutput, list[str]]:
    return (
        ReclassifyOutput(
            kind="greenfield",
            confidence=0.9,
            candidate_app_id="x",
            greenfield_name="x",
            repo_path_hint=str(tmp_path / "fresh"),
            next_question=None,
            resolution_status="ready",
            rationale="r",
        ),
        [],
    )


# Allowed states: parsed (with question), needs_clarification.
# Refused states: routed, in_progress, blocked, done, abandoned, ready.


def test_parsed_with_question_is_allowed(folder, tmp_path) -> None:
    folder.upsert_item(_item("parsed", with_question=True))
    with patch("week_intake.clarifier.classify_item", return_value=_ok_refresh(tmp_path)):
        result = clarify_with_answer(folder, item_id="wk-001", answer="ok")
    assert result.item.state == "ready"


def test_parsed_without_question_refused(folder) -> None:
    folder.upsert_item(_item("parsed", with_question=False))
    with pytest.raises(ClarifyError) as exc:
        clarify_with_answer(folder, item_id="wk-001", answer="ok")
    assert "no open question" in str(exc.value)


def test_needs_clarification_is_allowed(folder, tmp_path) -> None:
    folder.upsert_item(_item("needs_clarification"))
    with patch("week_intake.clarifier.classify_item", return_value=_ok_refresh(tmp_path)):
        result = clarify_with_answer(folder, item_id="wk-001", answer="ok")
    assert result.item.state == "ready"


def test_ready_refused(folder) -> None:
    folder.upsert_item(_item("ready"))
    with pytest.raises(ClarifyError) as exc:
        clarify_with_answer(folder, item_id="wk-001", answer="ok")
    assert "ready" in str(exc.value)


def test_routed_refused(folder) -> None:
    folder.upsert_item(_item("routed"))
    with pytest.raises(ClarifyError) as exc:
        clarify_with_answer(folder, item_id="wk-001", answer="ok")
    assert "terminal" in str(exc.value)


def test_in_progress_refused(folder) -> None:
    folder.upsert_item(_item("in_progress"))
    with pytest.raises(ClarifyError):
        clarify_with_answer(folder, item_id="wk-001", answer="ok")


def test_blocked_refused(folder) -> None:
    folder.upsert_item(_item("blocked"))
    with pytest.raises(ClarifyError):
        clarify_with_answer(folder, item_id="wk-001", answer="ok")


def test_done_refused(folder) -> None:
    folder.upsert_item(_item("done"))
    with pytest.raises(ClarifyError):
        clarify_with_answer(folder, item_id="wk-001", answer="ok")


def test_abandoned_refused(folder) -> None:
    folder.upsert_item(_item("abandoned"))
    with pytest.raises(ClarifyError):
        clarify_with_answer(folder, item_id="wk-001", answer="ok")
