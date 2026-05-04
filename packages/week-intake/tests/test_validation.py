"""Boundary-validation tests."""

from __future__ import annotations

import pytest

from week_intake.validation import (
    ValidationError,
    validate_repo_path,
    validate_scaffold_target,
    validate_slug,
)


def test_validate_slug_accepts_normal_slugs() -> None:
    for ok in ("chad-agent", "x", "abc-def-123", "a1", "1a"):
        assert validate_slug(ok) == ok


def test_validate_slug_rejects_path_traversal() -> None:
    for bad in ("../escape", "..", "x/y", "/abs", "x..y", ".hidden", "trailing-", "-leading"):
        with pytest.raises(ValidationError):
            validate_slug(bad)


def test_validate_slug_rejects_uppercase_and_unicode() -> None:
    for bad in ("ChadAgent", "café", "x_y", "x y", "x.y", "x@y"):
        with pytest.raises(ValidationError):
            validate_slug(bad)


def test_validate_slug_rejects_empty() -> None:
    for bad in ("", " ", None):
        with pytest.raises(ValidationError):
            validate_slug(bad)  # type: ignore[arg-type]


def test_validate_slug_enforces_length_cap() -> None:
    with pytest.raises(ValidationError):
        validate_slug("a" * 65)
    assert validate_slug("a" * 64) == "a" * 64


def test_validate_repo_path_existing_dir(tmp_path) -> None:
    p = validate_repo_path(str(tmp_path))
    assert p == tmp_path.resolve()


def test_validate_repo_path_rejects_nonexistent(tmp_path) -> None:
    with pytest.raises(ValidationError):
        validate_repo_path(str(tmp_path / "nope"))


def test_validate_repo_path_rejects_file(tmp_path) -> None:
    f = tmp_path / "f.txt"
    f.write_text("x")
    with pytest.raises(ValidationError):
        validate_repo_path(str(f))


def test_validate_repo_path_must_have_git(tmp_path) -> None:
    with pytest.raises(ValidationError):
        validate_repo_path(str(tmp_path), must_have_git=True)
    (tmp_path / ".git").mkdir()
    assert validate_repo_path(str(tmp_path), must_have_git=True) == tmp_path.resolve()


def test_validate_scaffold_target_accepts_missing(tmp_path) -> None:
    target = tmp_path / "fresh"
    assert validate_scaffold_target(str(target)) == target.resolve()


def test_validate_scaffold_target_accepts_empty_dir(tmp_path) -> None:
    target = tmp_path / "empty"
    target.mkdir()
    assert validate_scaffold_target(str(target)) == target.resolve()


def test_validate_scaffold_target_rejects_non_empty_dir(tmp_path) -> None:
    target = tmp_path / "non-empty"
    target.mkdir()
    (target / "x.txt").write_text("hi")
    with pytest.raises(ValidationError):
        validate_scaffold_target(str(target))


def test_validate_scaffold_target_rejects_file(tmp_path) -> None:
    f = tmp_path / "f.txt"
    f.write_text("hi")
    with pytest.raises(ValidationError):
        validate_scaffold_target(str(f))
