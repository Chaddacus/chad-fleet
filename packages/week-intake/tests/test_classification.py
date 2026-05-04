"""classify_item: shared LLM classifier used by both intake and clarify."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from week_intake.classification import (
    ALLOWED_KINDS,
    ReclassifyOutput,
    classify_item,
)
from week_intake.llm import LLMError


def _ok_payload(**overrides) -> dict:
    base = {
        "kind": "wip",
        "confidence": 0.9,
        "candidate_app_id": "chad-agent",
        "greenfield_name": None,
        "repo_path_hint": None,
        "next_question": None,
        "resolution_status": "ready",
        "rationale": "clear wip on tracked app",
    }
    base.update(overrides)
    return base


def test_classify_item_happy_path() -> None:
    with patch("week_intake.classification.claude_json", return_value=_ok_payload()):
        out, warnings = classify_item("rewrite docs in chad-agent", [])
    assert out.kind == "wip"
    assert out.confidence == 0.9
    assert out.candidate_app_id == "chad-agent"
    assert out.resolution_status == "ready"
    assert warnings == []


def test_classify_item_uses_q_and_a() -> None:
    """Q&A pairs flow into the prompt body."""
    captured = {}

    def fake_json(*, prompt, schema, system, timeout):
        captured["prompt"] = prompt
        return _ok_payload(kind="greenfield", greenfield_name="new-thing", candidate_app_id="new-thing")

    with patch("week_intake.classification.claude_json", side_effect=fake_json):
        classify_item("vague task", [("Is this new or wip?", "new project")])
    assert "Is this new or wip?" in captured["prompt"]
    assert "new project" in captured["prompt"]


def test_classify_item_pydantic_rejects_bad_confidence() -> None:
    with patch("week_intake.classification.claude_json", return_value=_ok_payload(confidence="high")):
        with pytest.raises(LLMError) as exc:
            classify_item("x", [])
    assert "validation" in str(exc.value).lower()


def test_classify_item_pydantic_rejects_bad_kind() -> None:
    with patch("week_intake.classification.claude_json", return_value=_ok_payload(kind="not_a_kind")):
        with pytest.raises(LLMError):
            classify_item("x", [])


def test_classify_item_pydantic_rejects_missing_required() -> None:
    bad = {"kind": "wip"}  # missing confidence + resolution_status
    with patch("week_intake.classification.claude_json", return_value=bad):
        with pytest.raises(LLMError):
            classify_item("x", [])


def test_classify_item_demotes_invalid_app_slug() -> None:
    with patch(
        "week_intake.classification.claude_json",
        return_value=_ok_payload(candidate_app_id="../escape"),
    ):
        out, warnings = classify_item("x", [])
    assert out.candidate_app_id is None
    assert any("invalid candidate_app_id" in w for w in warnings)


def test_classify_item_demotes_invalid_greenfield_slug() -> None:
    with patch(
        "week_intake.classification.claude_json",
        return_value=_ok_payload(
            kind="greenfield",
            greenfield_name="../pwn",
            candidate_app_id=None,
        ),
    ):
        out, warnings = classify_item("x", [])
    assert out.greenfield_name is None
    assert any("invalid greenfield_name" in w for w in warnings)


def test_classify_item_demotes_invalid_repo_path_hint(tmp_path) -> None:
    """A repo_path_hint that doesn't exist is dropped + warned."""
    bad_path = str(tmp_path / "no-such-dir")
    with patch(
        "week_intake.classification.claude_json",
        return_value=_ok_payload(repo_path_hint=bad_path),
    ):
        out, warnings = classify_item("x", [])
    assert out.repo_path_hint is None
    assert any("repo_path_hint" in w for w in warnings)


def test_classify_item_demotes_repo_hint_without_git(tmp_path) -> None:
    """A real dir with no .git fails must_have_git=True for non-greenfield kinds."""
    plain = tmp_path / "plain-dir"
    plain.mkdir()
    with patch(
        "week_intake.classification.claude_json",
        return_value=_ok_payload(kind="wip", repo_path_hint=str(plain)),
    ):
        out, warnings = classify_item("x", [])
    assert out.repo_path_hint is None
    assert any("git worktree" in w for w in warnings)


def test_classify_item_accepts_valid_repo_hint_with_git(tmp_path) -> None:
    repo = tmp_path / "real-repo"
    repo.mkdir()
    (repo / ".git").mkdir()
    with patch(
        "week_intake.classification.claude_json",
        return_value=_ok_payload(kind="wip", repo_path_hint=str(repo)),
    ):
        out, warnings = classify_item("x", [])
    assert out.repo_path_hint == str(repo)
    assert warnings == []


def test_classify_item_accepts_valid_greenfield_target(tmp_path) -> None:
    target = tmp_path / "fresh"
    with patch(
        "week_intake.classification.claude_json",
        return_value=_ok_payload(
            kind="greenfield",
            greenfield_name="fresh",
            candidate_app_id="fresh",
            repo_path_hint=str(target),
        ),
    ):
        out, warnings = classify_item("x", [])
    assert out.repo_path_hint == str(target)
    assert warnings == []


def test_allowed_kinds_matches_pydantic_literal() -> None:
    """ALLOWED_KINDS and ReclassifyOutput.kind must stay in sync."""
    # Smoke check: every allowed kind is acceptable to the Pydantic model.
    for kind in ALLOWED_KINDS:
        with patch(
            "week_intake.classification.claude_json",
            return_value=_ok_payload(kind=kind),
        ):
            out, _ = classify_item("x", [])
        assert out.kind == kind
