"""Tests for chad_captain.validators.t3_marketing.

The T3 validator is FAIL-CLOSED: every config error path must produce
``escalate`` and write an admiral note. Every config-OK path must
either delegate to the engine's default chain or run the fixture FK
gate against matching files.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from chad_captain.fixture_validator import FixtureValidation
from chad_captain.protocol import (
    AppWorkspace,
    CurrentSlice,
    SliceComplete,
)
from chad_captain.validator import ValidationResult
from chad_captain.validators import t3_marketing as t3


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def workspace(tmp_path: Path) -> AppWorkspace:
    ws = AppWorkspace("t3-chadacys-marketing", base=tmp_path / "fleet")
    ws.ensure()
    return ws


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    r = tmp_path / "repo"
    r.mkdir()
    return r


def _slice(slice_id: str = "s1", parent: str | None = None) -> CurrentSlice:
    return CurrentSlice(
        slice_id=slice_id,
        app_id="t3-chadacys-marketing",
        objective="o",
        system_prompt="s",
        user_prompt="u",
        repo_path="/tmp/r",
        parent_slice_id=parent,
    )


def _complete(
    *,
    slice_id: str = "s1",
    files: list[str] | None = None,
    exit_code: int = 0,
) -> SliceComplete:
    return SliceComplete(
        slice_id=slice_id,
        app_id="t3-chadacys-marketing",
        duration_seconds=1.0,
        goose_exit_code=exit_code,
        summary="ok",
        files_changed=files if files is not None else ["README.md"],
    )


def _write_config(
    repo: Path,
    *,
    settings_module: str = "config.settings.test",
    fixtures_glob: str = "apps/marketing/fixtures/marketing_posts_*.json",
    python_bin: str | None = None,
    overrides: dict | None = None,
) -> Path:
    cfg: dict = {
        "settings_module": settings_module,
        "fixtures_glob": fixtures_glob,
    }
    if python_bin is not None:
        cfg["python_bin"] = python_bin
    if overrides is not None:
        cfg.update(overrides)
    p = repo / t3.CONFIG_FILENAME
    p.write_text(json.dumps(cfg))
    return p


def _call(
    *,
    ws: AppWorkspace,
    repo: Path,
    files: list[str] | None = None,
    was_retry: bool = False,
    slice_id: str = "s1",
) -> ValidationResult:
    return t3.validate_app_completion(
        ws=ws,
        complete=_complete(slice_id=slice_id, files=files),
        dispatched_slice=_slice(slice_id=slice_id),
        repo_path=str(repo),
        reg_app=None,
        score_delta=None,
        was_retry=was_retry,
        use_baseline_scorecard=False,
    )


# ---------------------------------------------------------------------------
# Config-error escalations (FAIL-CLOSED)
# ---------------------------------------------------------------------------


def test_missing_config_escalates_and_writes_admiral_note(
    workspace: AppWorkspace, repo: Path
) -> None:
    result = _call(ws=workspace, repo=repo)
    assert result.verdict == "escalate"
    assert "missing .chad-captain.t3.json" in result.rationale
    notes = list(workspace.admiral_notes_dir.glob("t3-config-error-*.json"))
    assert len(notes) == 1
    note_data = json.loads(notes[0].read_text())
    assert note_data["app_id"] == "t3-chadacys-marketing"
    assert "Add a `.chad-captain.t3.json`" in note_data["body"]


def test_malformed_json_escalates(workspace: AppWorkspace, repo: Path) -> None:
    (repo / t3.CONFIG_FILENAME).write_text("not-json{{")
    result = _call(ws=workspace, repo=repo)
    assert result.verdict == "escalate"
    assert "malformed" in result.rationale


def test_config_must_be_object(workspace: AppWorkspace, repo: Path) -> None:
    (repo / t3.CONFIG_FILENAME).write_text(json.dumps(["a", "b"]))
    result = _call(ws=workspace, repo=repo)
    assert result.verdict == "escalate"
    assert "must be a JSON object" in result.rationale


def test_missing_required_keys_escalates(
    workspace: AppWorkspace, repo: Path
) -> None:
    (repo / t3.CONFIG_FILENAME).write_text(json.dumps({"settings_module": "x"}))
    result = _call(ws=workspace, repo=repo)
    assert result.verdict == "escalate"
    assert "missing required keys" in result.rationale
    assert "fixtures_glob" in result.rationale


def test_empty_string_value_escalates(
    workspace: AppWorkspace, repo: Path
) -> None:
    _write_config(repo, settings_module="")
    result = _call(ws=workspace, repo=repo)
    assert result.verdict == "escalate"
    assert "must be a non-empty string" in result.rationale


# ---------------------------------------------------------------------------
# Default-chain delegation
# ---------------------------------------------------------------------------


def test_no_fixtures_in_diff_delegates_to_default(
    workspace: AppWorkspace, repo: Path
) -> None:
    _write_config(repo)
    sentinel = ValidationResult(verdict="accept", rationale="default chain ok")
    with patch.object(t3, "_default_validate", return_value=sentinel) as m:
        result = _call(ws=workspace, repo=repo, files=["README.md", "src/x.py"])
    assert result is sentinel
    m.assert_called_once()


def test_passing_fixtures_delegate_to_default(
    workspace: AppWorkspace, repo: Path
) -> None:
    _write_config(repo)
    sentinel = ValidationResult(verdict="accept", rationale="default chain ok")
    fv_ok = FixtureValidation(ok=True, summary="loaded", exit_code=0)
    with patch.object(t3, "validate_django_fixtures", return_value=fv_ok), \
         patch.object(t3, "_default_validate", return_value=sentinel) as m:
        result = _call(
            ws=workspace,
            repo=repo,
            files=["apps/marketing/fixtures/marketing_posts_001.json"],
        )
    assert result is sentinel
    m.assert_called_once()


# ---------------------------------------------------------------------------
# Fixture FK gate
# ---------------------------------------------------------------------------


def test_failing_fixtures_first_attempt_reject_retry(
    workspace: AppWorkspace, repo: Path
) -> None:
    _write_config(repo)
    fv_bad = FixtureValidation(
        ok=False, summary="FK violation on Post.author_id", exit_code=2
    )
    with patch.object(t3, "validate_django_fixtures", return_value=fv_bad), \
         patch.object(t3, "_default_validate") as default_m:
        result = _call(
            ws=workspace,
            repo=repo,
            files=["apps/marketing/fixtures/marketing_posts_001.json"],
            was_retry=False,
        )
    assert result.verdict == "reject_retry"
    assert "fixture validation failed" in result.rationale
    assert "FK violation" in result.rationale
    assert "marketing_posts_001.json" in result.retry_context
    default_m.assert_not_called()


def test_failing_fixtures_after_retry_reject_hard(
    workspace: AppWorkspace, repo: Path
) -> None:
    _write_config(repo)
    fv_bad = FixtureValidation(ok=False, summary="FK violation", exit_code=2)
    with patch.object(t3, "validate_django_fixtures", return_value=fv_bad), \
         patch.object(t3, "_default_validate") as default_m:
        result = _call(
            ws=workspace,
            repo=repo,
            files=["apps/marketing/fixtures/marketing_posts_001.json"],
            was_retry=True,
        )
    assert result.verdict == "reject_hard"
    default_m.assert_not_called()


def test_python_bin_omitted_uses_sys_executable(
    workspace: AppWorkspace, repo: Path
) -> None:
    _write_config(repo, python_bin=None)
    sentinel = ValidationResult(verdict="accept", rationale="ok")
    fv_ok = FixtureValidation(ok=True, summary="loaded", exit_code=0)
    with patch.object(t3, "validate_django_fixtures", return_value=fv_ok) as fv_m, \
         patch.object(t3, "_default_validate", return_value=sentinel):
        _call(
            ws=workspace,
            repo=repo,
            files=["apps/marketing/fixtures/marketing_posts_001.json"],
        )
    kwargs = fv_m.call_args.kwargs
    assert kwargs["python"] is None  # validate_django_fixtures defaults to sys.executable


def test_python_bin_passed_through(workspace: AppWorkspace, repo: Path) -> None:
    _write_config(repo, python_bin="/custom/.venv/bin/python")
    sentinel = ValidationResult(verdict="accept", rationale="ok")
    fv_ok = FixtureValidation(ok=True, summary="loaded", exit_code=0)
    with patch.object(t3, "validate_django_fixtures", return_value=fv_ok) as fv_m, \
         patch.object(t3, "_default_validate", return_value=sentinel):
        _call(
            ws=workspace,
            repo=repo,
            files=["apps/marketing/fixtures/marketing_posts_001.json"],
        )
    assert fv_m.call_args.kwargs["python"] == "/custom/.venv/bin/python"
