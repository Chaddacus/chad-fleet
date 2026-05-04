"""Input validation — slug shapes, path safety, repo presence checks.

These guard the boundaries where user input flows into filesystem paths
or HTTP payloads. Keep validators tight: reject early, with clear
messages, before anything writes to disk.
"""

from __future__ import annotations

import re
from pathlib import Path

# Conservative slug rule: lowercase letters, digits, dash. 1–64 chars.
# Matches what chad-captain uses for app_id workspace dir names and what
# Python packaging accepts after dash→underscore mapping.
_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}[a-z0-9]$|^[a-z0-9]$")


class ValidationError(ValueError):
    """Raised when an input fails a boundary check."""


def validate_slug(value: str, *, field: str = "slug") -> str:
    """Return ``value`` if it's a safe slug; else raise.

    A safe slug is lowercase ascii alphanumeric + dash, 1–64 chars,
    no leading/trailing dash. Rejects ``..``, ``/``, ``\\``, dots,
    spaces, and anything else that could escape a directory join.
    """
    if not isinstance(value, str) or not value:
        raise ValidationError(f"{field} must be a non-empty string")
    if not _SLUG_RE.match(value):
        raise ValidationError(
            f"{field}={value!r} is not a valid slug "
            "(lowercase a-z, 0-9, dash; 1-64 chars; no leading/trailing dash)"
        )
    return value


def validate_repo_path(value: str, *, must_have_git: bool = False) -> Path:
    """Return resolved Path if the repo exists; else raise."""
    if not isinstance(value, str) or not value.strip():
        raise ValidationError("repo_path must be a non-empty string")
    p = Path(value).expanduser().resolve()
    if not p.exists():
        raise ValidationError(f"repo_path does not exist: {p}")
    if not p.is_dir():
        raise ValidationError(f"repo_path is not a directory: {p}")
    if must_have_git and not (p / ".git").exists():
        raise ValidationError(f"repo_path is not a git worktree: {p} (no .git)")
    return p


def validate_scaffold_target(value: str) -> Path:
    """Return resolved Path for a NEW scaffold target.

    The target may not exist yet, OR may exist but be empty. Anything else
    is rejected so we never overwrite user data.
    """
    if not isinstance(value, str) or not value.strip():
        raise ValidationError("scaffold target must be a non-empty string")
    p = Path(value).expanduser().resolve()
    if p.exists():
        if not p.is_dir():
            raise ValidationError(f"scaffold target exists and is not a directory: {p}")
        if any(p.iterdir()):
            raise ValidationError(f"scaffold target is not empty: {p}")
    return p


__all__ = [
    "ValidationError",
    "validate_repo_path",
    "validate_scaffold_target",
    "validate_slug",
]
