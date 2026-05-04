"""Scaffold tests — actually invokes git, but only against tmp_path."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from week_intake.scaffold import ScaffoldError, scaffold_greenfield


def _git_available() -> bool:
    return shutil.which("git") is not None


@pytest.mark.skipif(not _git_available(), reason="git not on PATH")
def test_scaffold_creates_repo_with_initial_commit(tmp_path) -> None:
    target = tmp_path / "fresh"
    result = scaffold_greenfield(
        path=target,
        name="fresh-thing",
        description="test scaffold",
        ts="2026-05-04",
    )
    out = result.path
    assert out.exists()
    assert (out / "pyproject.toml").exists()
    assert (out / "README.md").exists()
    assert (out / "src" / "fresh_thing" / "__init__.py").exists()
    assert (out / ".git").exists()

    # Confirm initial commit landed.
    log = subprocess.run(
        ["git", "log", "--oneline"], cwd=out, capture_output=True, text=True, check=True
    )
    assert "scaffold fresh-thing" in log.stdout

    # ScaffoldResult tracked the artifacts.
    assert any("pyproject.toml" in str(p) for p in result.created_files)
    assert any("README.md" in str(p) for p in result.created_files)
    # `.git` is in `owned_dirs` (rmtree-safe), not `created_dirs` (rmdir-only).
    assert any(".git" in str(p) for p in result.owned_dirs)


def test_scaffold_refuses_non_empty_path(tmp_path) -> None:
    target = tmp_path / "non-empty"
    target.mkdir()
    (target / "existing.txt").write_text("hi")
    with pytest.raises(ScaffoldError):
        scaffold_greenfield(path=target, name="fresh", description="x", ts="2026-05-04")
