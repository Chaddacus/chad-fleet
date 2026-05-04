"""Clarifier: two-phase commit, conflict detection, --continue retry."""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from week_intake.classification import ReclassifyOutput
from week_intake.clarifier import (
    ClarifyConflict,
    ClarifyError,
    clarify_continue,
    clarify_with_answer,
)
from week_intake.llm import LLMError
from week_intake.protocol import WeekFolder
from week_intake.types import ClarificationQuestion, RouteTarget, WeekItem


@pytest.fixture
def folder(tmp_path, monkeypatch):
    monkeypatch.setenv("CHAD_WEEK_DIR", str(tmp_path / "week"))
    monkeypatch.setenv("CHAD_FLEET_APPS_DIR", str(tmp_path / "fleet"))
    f = WeekFolder(week="2026-W19")
    f.ensure()
    return f


def _ambiguous_item(item_id: str = "wk-001") -> WeekItem:
    return WeekItem(
        item_id=item_id,
        week="2026-W19",
        raw_text="set up marketing site",
        title="set up marketing site",
        kind="unknown",
        state="needs_clarification",
        confidence=0.5,
        clarifications=[
            ClarificationQuestion(question_id="q001", prompt="new repo or wip?"),
        ],
    )


def _refresh(**overrides) -> tuple[ReclassifyOutput, list[str]]:
    base = ReclassifyOutput(
        kind="greenfield",
        confidence=0.9,
        candidate_app_id="spark-marketing",
        greenfield_name="spark-marketing",
        repo_path_hint=None,
        next_question=None,
        resolution_status="ready",
        rationale="answered as greenfield",
    )
    for k, v in overrides.items():
        setattr(base, k, v)
    return base, []


def test_clarify_records_answer_and_advances(folder, tmp_path) -> None:
    item = _ambiguous_item()
    folder.upsert_item(item)

    # Pre-create scaffold target so resolution_status=ready stays valid.
    scaffold_target = tmp_path / "fresh-marketing"  # nonexistent → valid scaffold target
    refresh, warnings = _refresh(
        repo_path_hint=str(scaffold_target),
        candidate_app_id="spark-marketing",
        greenfield_name="spark-marketing",
    )

    with patch("week_intake.clarifier.classify_item", return_value=(refresh, warnings)):
        result = clarify_with_answer(folder, item_id="wk-001", answer="new repo")

    assert result.item.state == "ready"
    assert result.item.kind == "greenfield"
    assert result.item.target.greenfield_name == "spark-marketing"
    assert result.item.pending_refresh_question_id is None
    assert result.item.revision == 2  # phase 1 +1, phase 3 +1
    assert result.warnings == []

    # Persisted to disk.
    on_disk = folder.get_item("wk-001")
    assert on_disk.state == "ready"
    assert on_disk.clarifications[0].answer == "new repo"
    assert on_disk.clarifications[0].answered_at is not None


def test_clarify_phase2_failure_persists_answer_and_pending(folder, tmp_path) -> None:
    """LLM error in phase 2 must leave the answer + pending marker on disk."""
    item = _ambiguous_item()
    folder.upsert_item(item)

    with patch("week_intake.clarifier.classify_item", side_effect=LLMError("api boom")):
        with pytest.raises(LLMError):
            clarify_with_answer(folder, item_id="wk-001", answer="new repo")

    on_disk = folder.get_item("wk-001")
    assert on_disk.clarifications[0].answer == "new repo"
    assert on_disk.pending_refresh_question_id == "q001"
    assert on_disk.revision == 1  # only phase 1 ran


def test_clarify_continue_resumes_after_phase2_failure(folder, tmp_path) -> None:
    """--continue picks up where phase 2 failed, without re-recording."""
    item = _ambiguous_item()
    folder.upsert_item(item)

    # Simulate prior phase-2 failure: answer + pending set, revision=1.
    with patch("week_intake.clarifier.classify_item", side_effect=LLMError("boom")):
        with pytest.raises(LLMError):
            clarify_with_answer(folder, item_id="wk-001", answer="greenfield")

    refresh, warnings = _refresh(
        kind="greenfield",
        candidate_app_id="spark-marketing",
        greenfield_name="spark-marketing",
        repo_path_hint=str(tmp_path / "newly"),
    )
    with patch("week_intake.clarifier.classify_item", return_value=(refresh, warnings)):
        result = clarify_continue(folder, item_id="wk-001")

    assert result.item.state == "ready"
    assert result.item.pending_refresh_question_id is None
    assert result.item.revision == 2


def test_clarify_continue_refuses_no_pending(folder) -> None:
    """--continue with no pending refresh fails clearly."""
    item = _ambiguous_item()
    folder.upsert_item(item)
    with pytest.raises(ClarifyError) as exc:
        clarify_continue(folder, item_id="wk-001")
    assert "no pending refresh" in str(exc.value)


def test_clarify_continue_refuses_pending_unanswered_question(folder) -> None:
    """If pending points to an unanswered question, --continue refuses."""
    item = _ambiguous_item()
    item.pending_refresh_question_id = "q001"  # but q001.answer is still None
    folder.upsert_item(item)
    with pytest.raises(ClarifyError) as exc:
        clarify_continue(folder, item_id="wk-001")
    assert "unanswered" in str(exc.value)


def test_clarify_refuses_terminal_states(folder) -> None:
    for state in ("routed", "in_progress", "blocked", "done", "abandoned"):
        item = _ambiguous_item(item_id=f"wk-{state[:3]}-001")
        item.state = state
        folder.upsert_item(item)
        with pytest.raises(ClarifyError) as exc:
            clarify_with_answer(folder, item_id=item.item_id, answer="x")
        assert "terminal" in str(exc.value)


def test_clarify_refuses_already_ready(folder) -> None:
    item = _ambiguous_item()
    item.state = "ready"
    folder.upsert_item(item)
    with pytest.raises(ClarifyError) as exc:
        clarify_with_answer(folder, item_id="wk-001", answer="x")
    assert "ready" in str(exc.value)


def test_clarify_refuses_parsed_with_no_question(folder) -> None:
    item = _ambiguous_item()
    item.state = "parsed"
    item.clarifications = []
    folder.upsert_item(item)
    with pytest.raises(ClarifyError) as exc:
        clarify_with_answer(folder, item_id="wk-001", answer="x")
    assert "no open question" in str(exc.value)


def test_clarify_refuses_already_answered_question(folder) -> None:
    item = _ambiguous_item()
    item.clarifications[0].answer = "earlier answer"
    folder.upsert_item(item)
    with pytest.raises(ClarifyError) as exc:
        clarify_with_answer(folder, item_id="wk-001", answer="new answer", question_id="q001")
    assert "already answered" in str(exc.value)


def test_clarify_demotes_unregistered_app(folder, tmp_path) -> None:
    """LLM proposes valid slug for app not registered → demoted + warning."""
    item = _ambiguous_item()
    folder.upsert_item(item)

    refresh, warnings = _refresh(
        kind="wip",
        candidate_app_id="ghost-app",  # no workspace
        greenfield_name=None,
        resolution_status="ready",
    )
    with patch("week_intake.clarifier.classify_item", return_value=(refresh, warnings)):
        result = clarify_with_answer(folder, item_id="wk-001", answer="wip on ghost-app")

    assert result.item.target.app_id is None  # demoted
    assert any("ghost-app" in w for w in result.item.refresh_warnings)
    # State stays needs_clarification because the demoted candidate makes target invalid.
    assert result.item.state == "needs_clarification"


def test_clarify_appends_next_question_when_unresolved(folder) -> None:
    """LLM returns next_question + ask_next → state=needs_clarification with q002."""
    item = _ambiguous_item()
    folder.upsert_item(item)

    refresh, warnings = _refresh(
        confidence=0.5,
        candidate_app_id=None,
        greenfield_name=None,
        next_question="What's the local repo path?",
        resolution_status="ask_next",
    )
    with patch("week_intake.clarifier.classify_item", return_value=(refresh, warnings)):
        result = clarify_with_answer(folder, item_id="wk-001", answer="vague")

    assert result.item.state == "needs_clarification"
    assert result.next_question_id == "q002"
    new_q = next(c for c in result.item.clarifications if c.question_id == "q002")
    assert "repo path" in new_q.prompt


def test_clarify_warnings_replace_not_append(folder, tmp_path) -> None:
    """A second clarify replaces the first round's warnings."""
    item = _ambiguous_item()
    item.refresh_warnings = ["stale warning from prior clarify"]
    folder.upsert_item(item)

    refresh, _ = _refresh(
        repo_path_hint=str(tmp_path / "fresh"),
        candidate_app_id="x",
        greenfield_name="x",
    )
    with patch("week_intake.clarifier.classify_item", return_value=(refresh, [])):
        result = clarify_with_answer(folder, item_id="wk-001", answer="ok")
    # Stale warning gone; new clean refresh leaves warnings empty.
    assert result.item.refresh_warnings == []


def test_clarify_invalid_answer_raises(folder) -> None:
    item = _ambiguous_item()
    folder.upsert_item(item)
    with pytest.raises(ClarifyError):
        clarify_with_answer(folder, item_id="wk-001", answer="")
    with pytest.raises(ClarifyError):
        clarify_with_answer(folder, item_id="wk-001", answer="x" * 10000)


def test_clarify_unknown_question_id(folder) -> None:
    item = _ambiguous_item()
    folder.upsert_item(item)
    with pytest.raises(ClarifyError):
        clarify_with_answer(folder, item_id="wk-001", answer="ok", question_id="q999")


def test_clarify_invalid_question_id_format(folder) -> None:
    item = _ambiguous_item()
    folder.upsert_item(item)
    with pytest.raises(ClarifyError):
        clarify_with_answer(folder, item_id="wk-001", answer="ok", question_id="not-valid")


def test_clarify_conflict_on_concurrent_modification(folder, tmp_path) -> None:
    """If item.revision changes between phase 1 and phase 3, raise ClarifyConflict."""
    item = _ambiguous_item()
    folder.upsert_item(item)

    refresh, _ = _refresh(repo_path_hint=str(tmp_path / "fresh"))

    def fake_classify(*args, **kwargs):
        # Simulate another writer bumping the revision while we're in phase 2.
        item_now = folder.get_item("wk-001")
        item_now.revision += 100  # bump
        folder.upsert_item(item_now)
        return refresh, []

    with patch("week_intake.clarifier.classify_item", side_effect=fake_classify):
        with pytest.raises(ClarifyConflict):
            clarify_with_answer(folder, item_id="wk-001", answer="x")


def test_clarify_ready_downgrade_when_target_insufficient(folder) -> None:
    """LLM says ready but target is empty → downgrade + synthesized question."""
    item = _ambiguous_item()
    folder.upsert_item(item)

    refresh, _ = _refresh(
        candidate_app_id=None,
        greenfield_name=None,
        repo_path_hint=None,
        next_question=None,
        resolution_status="ready",
    )
    with patch("week_intake.clarifier.classify_item", return_value=(refresh, [])):
        result = clarify_with_answer(folder, item_id="wk-001", answer="vague")

    assert result.item.state == "needs_clarification"
    # Either a synthesized question OR a warning if no field could be inferred.
    assert (
        result.next_question_id is not None
        or any("manual" in w.lower() or "incomplete" in w.lower() for w in result.item.refresh_warnings)
    )
