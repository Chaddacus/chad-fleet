"""Scaffold validation tests — name/path safety, idempotent rollback."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from week_intake.scaffold import ScaffoldError, scaffold_greenfield


def _git_available() -> bool:
    return shutil.which("git") is not None


def test_scaffold_rejects_path_traversal_name(tmp_path) -> None:
    target = tmp_path / "fresh"
    for bad in ("../escape", "x/y", "..", "/abs", "x.y", "Café"):
        with pytest.raises(ScaffoldError):
            scaffold_greenfield(path=target, name=bad, description="x", ts="2026-05-04")


def test_scaffold_rejects_overlong_name(tmp_path) -> None:
    with pytest.raises(ScaffoldError):
        scaffold_greenfield(path=tmp_path / "fresh", name="a" * 100, description="x", ts="x")


def test_scaffold_rejects_non_empty_path(tmp_path) -> None:
    target = tmp_path / "non-empty"
    target.mkdir()
    (target / "existing.txt").write_text("hi")
    with pytest.raises(ScaffoldError):
        scaffold_greenfield(path=target, name="fresh", description="x", ts="2026-05-04")


@pytest.mark.skipif(not _git_available(), reason="git not on PATH")
def test_scaffold_escapes_quotes_in_description(tmp_path) -> None:
    """A description containing quotes must produce valid TOML."""
    import tomllib

    target = tmp_path / "fresh"
    result = scaffold_greenfield(
        path=target,
        name="fresh-thing",
        description='He said "hello" and broke\nthe file',
        ts="2026-05-04",
    )
    content = (result.path / "pyproject.toml").read_text(encoding="utf-8")
    # tomllib must parse this without error; otherwise our escape is wrong.
    parsed = tomllib.loads(content)
    assert parsed["project"]["name"] == "fresh-thing"
    assert "hello" in parsed["project"]["description"]


@pytest.mark.skipif(not _git_available(), reason="git not on PATH")
def test_scaffold_rolls_back_on_git_failure(tmp_path) -> None:
    """If git fails mid-scaffold, the partial directory is removed."""
    target = tmp_path / "fresh"

    real_run = subprocess.run

    def fail_on_commit(cmd, **kwargs):
        if isinstance(cmd, list) and "commit" in cmd:
            class FakeProc:
                returncode = 1
                stderr = "fake commit failure"
                stdout = ""
            return FakeProc()
        return real_run(cmd, **kwargs)

    with patch("week_intake.scaffold.subprocess.run", side_effect=fail_on_commit):
        with pytest.raises(ScaffoldError):
            scaffold_greenfield(path=target, name="fresh-thing", description="x", ts="x")

    assert not target.exists(), "scaffold dir should be removed on failure"


@pytest.mark.skipif(not _git_available(), reason="git not on PATH")
def test_scaffold_disables_hooks_and_signing(tmp_path) -> None:
    """Verify the safe-flag invocation makes it into git calls."""
    target = tmp_path / "fresh"
    captured = []

    real_run = subprocess.run

    def capture(cmd, **kwargs):
        captured.append(cmd)
        return real_run(cmd, **kwargs)

    with patch("week_intake.scaffold.subprocess.run", side_effect=capture):
        scaffold_greenfield(path=target, name="fresh-thing", description="x", ts="2026-05-04")

    for cmd in captured:
        assert "core.hooksPath=/dev/null" in cmd, f"missing hooks-disable in: {cmd}"
        assert "commit.gpgsign=false" in cmd, f"missing signing-disable in: {cmd}"


def test_scaffold_git_timeout_translates_to_scaffold_error(tmp_path) -> None:
    target = tmp_path / "fresh"
    with patch(
        "week_intake.scaffold.subprocess.run",
        side_effect=subprocess.TimeoutExpired(cmd="git", timeout=30),
    ):
        with pytest.raises(ScaffoldError) as exc_info:
            scaffold_greenfield(path=target, name="fresh-thing", description="x", ts="x")
    assert "timed out" in str(exc_info.value)
