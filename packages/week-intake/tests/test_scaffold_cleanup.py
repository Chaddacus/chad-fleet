"""ScaffoldResult.cleanup() preserves user-added files; tracks intermediate parents."""

from __future__ import annotations

import shutil

import pytest

from week_intake.scaffold import ScaffoldResult, scaffold_greenfield


def _git_available() -> bool:
    return shutil.which("git") is not None


@pytest.mark.skipif(not _git_available(), reason="git not on PATH")
def test_cleanup_does_not_delete_user_added_files(tmp_path) -> None:
    """If user adds a file inside a scaffolded dir BEFORE cleanup, leave it alone."""
    target = tmp_path / "fresh"
    result = scaffold_greenfield(
        path=target,
        name="fresh-thing",
        description="x",
        ts="2026-05-04",
    )

    # Simulate the user (or some external step) dropping a file in our dir
    # AFTER scaffold succeeded but BEFORE downstream failure triggers cleanup.
    # Importantly, place it in the target root (not in .git, which we own).
    user_file = target / "user-data.txt"
    user_file.write_text("important user data", encoding="utf-8")

    result.cleanup()

    # Our tracked files should be gone; the user's file must still be there.
    assert not (target / "pyproject.toml").exists()
    assert not (target / "README.md").exists()
    # The user file survives — and so does the dir holding it (rmdir-only).
    assert user_file.exists()
    assert target.exists()
    assert user_file.read_text() == "important user data"


def test_cleanup_idempotent(tmp_path) -> None:
    """cleanup() can be called twice without raising."""
    result = ScaffoldResult(
        path=tmp_path / "x",
        created_files=[],
        created_dirs=[],
    )
    result.cleanup()
    result.cleanup()  # second call must be a no-op


@pytest.mark.skipif(not _git_available(), reason="git not on PATH")
def test_cleanup_removes_intermediate_parents(tmp_path) -> None:
    """If --repo /tmp/nope/sub/sub2 was given, cleanup removes the whole new tree."""
    target = tmp_path / "newly" / "nested" / "fresh"
    result = scaffold_greenfield(
        path=target,
        name="fresh-thing",
        description="x",
        ts="2026-05-04",
    )
    assert target.exists()
    assert (tmp_path / "newly").exists()

    result.cleanup()

    # The fresh dir AND its parents we created are gone.
    assert not target.exists()
    assert not (tmp_path / "newly" / "nested").exists()
    assert not (tmp_path / "newly").exists()
    # tmp_path itself was preexisting → preserved.
    assert tmp_path.exists()


@pytest.mark.skipif(not _git_available(), reason="git not on PATH")
def test_cleanup_preserves_preexisting_intermediate(tmp_path) -> None:
    """A preexisting intermediate dir must NOT be removed by cleanup."""
    preexisting = tmp_path / "user-stuff"
    preexisting.mkdir()
    target = preexisting / "newly" / "fresh"

    result = scaffold_greenfield(
        path=target,
        name="fresh-thing",
        description="x",
        ts="2026-05-04",
    )
    result.cleanup()

    assert not target.exists()
    assert not (preexisting / "newly").exists()
    # Preexisting dir is preserved.
    assert preexisting.exists()
