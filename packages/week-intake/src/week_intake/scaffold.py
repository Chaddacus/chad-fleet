"""Greenfield scaffold: create a minimal repo + initial commit.

Used by ``chad-week route`` when an item is classified as greenfield. We
keep this deliberately tiny — a real project will diverge from the
template within an hour. The point is to give chad-captain something
to register and run a scorecard against.

Layout produced::

    <path>/
        .git/
        pyproject.toml
        README.md
        src/<module>/__init__.py

Safety contract:
- ``name`` MUST validate as a slug (lowercase a-z/0-9/dash, ≤64 chars).
- ``path`` MUST be empty or non-existent (refuses to overwrite user data).
- All file writes are atomic (``atomic_write``).
- Git invocations run with hooks disabled, signing disabled, and a hard timeout.
- On any failure after the target dir was created, the partial scaffold is
  removed so retry is safe.
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from tracked_app_registry.storage import atomic_write

from week_intake.validation import (
    ValidationError,
    validate_scaffold_target,
    validate_slug,
)


@dataclass
class ScaffoldResult:
    """What ``scaffold_greenfield`` produced.

    ``cleanup()`` removes ONLY paths created by the call that produced
    this result. Files and directories that pre-existed are left alone.
    Idempotent: safe to call multiple times.

    Cleanup policy for directories:
      - ``.git``-style dirs (we created and own everything inside them)
        are removed via ``shutil.rmtree``.
      - All other dirs are removed via ``rmdir`` only — this fails (and
        is silently skipped) if the user dropped extra files into our
        scaffolded dir between scaffold-success and downstream-failure.
        Better to leave the user's data alone than to nuke it.
    """

    path: Path
    created_files: list[Path] = field(default_factory=list)
    created_dirs: list[Path] = field(default_factory=list)
    # Subset of created_dirs where we own ALL contents (currently just .git).
    # rmtree-safe; everything else goes through rmdir.
    owned_dirs: list[Path] = field(default_factory=list)

    def cleanup(self) -> None:
        for fp in reversed(self.created_files):
            try:
                if fp.exists():
                    fp.unlink()
            except OSError:
                pass
        # Remove fully-owned dirs first (rmtree-safe).
        for dp in reversed(self.owned_dirs):
            try:
                if dp.exists():
                    shutil.rmtree(dp)
            except OSError:
                pass
        # Remove remaining tracked dirs only if they're now empty (rmdir).
        # If the user added other files to a dir we created, leave it alone.
        for dp in reversed(self.created_dirs):
            try:
                if dp.exists() and not any(dp.iterdir()):
                    dp.rmdir()
            except OSError:
                pass
        self.created_files.clear()
        self.created_dirs.clear()
        self.owned_dirs.clear()

PYPROJECT_TEMPLATE = """\
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[project]
name = "{name}"
version = "0.0.0"
description = "{description}"
requires-python = ">=3.11"

[tool.hatch.build.targets.wheel]
packages = ["src/{module}"]
"""

README_TEMPLATE = """\
# {name}

{description}

Scaffolded by `chad-week route` on {ts}. Tracked by chad-captain.
"""

# Git invocation flags that suppress every common source of hangs:
#  - core.hooksPath=/dev/null disables all repo + global hooks
#  - commit.gpgsign=false skips signing prompts
#  - gc.auto=0 prevents background gc spawning
GIT_SAFE_FLAGS = (
    "-c", "core.hooksPath=/dev/null",
    "-c", "commit.gpgsign=false",
    "-c", "gc.auto=0",
)
GIT_TIMEOUT_SECONDS = 30


class ScaffoldError(RuntimeError):
    pass


def _slug_to_module(slug: str) -> str:
    return slug.replace("-", "_")


def _toml_escape(s: str) -> str:
    """Escape a string for inclusion inside a TOML basic string literal.

    Replaces backslashes, double-quotes, and control chars (newline, CR, tab)
    with their TOML-safe escape sequences. Sufficient for the template
    fields ``name`` and ``description``.
    """
    out = []
    for ch in s:
        code = ord(ch)
        if ch == "\\":
            out.append("\\\\")
        elif ch == '"':
            out.append('\\"')
        elif ch == "\n":
            out.append("\\n")
        elif ch == "\r":
            out.append("\\r")
        elif ch == "\t":
            out.append("\\t")
        elif code < 0x20 or code == 0x7F:
            out.append(f"\\u{code:04X}")
        else:
            out.append(ch)
    return "".join(out)


def scaffold_greenfield(
    *,
    path: Path | str,
    name: str,
    description: str = "",
    ts: str = "",
) -> ScaffoldResult:
    """Create a minimal repo at ``path`` and make an initial commit.

    Returns a ``ScaffoldResult`` whose ``path`` attribute is the resolved
    target dir and whose ``cleanup()`` method removes ONLY paths this
    call created (pre-existing empty target dirs are preserved). On
    failure inside this function the cleanup runs automatically before
    re-raising — callers don't need to clean up scaffold-internal
    failures.

    The caller IS responsible for calling ``cleanup()`` if a downstream
    step (e.g. registration or admiral-note write) fails after scaffold
    succeeded.
    """
    # ---- validate inputs (boundary guards) ---------------------------
    try:
        slug = validate_slug(name, field="greenfield name")
    except ValidationError as e:
        raise ScaffoldError(str(e)) from e
    try:
        target = validate_scaffold_target(str(path))
    except ValidationError as e:
        raise ScaffoldError(str(e)) from e

    module = _slug_to_module(slug)
    # Sanity: the derived module must also be a safe path component.
    if "/" in module or "\\" in module or ".." in module:
        raise ScaffoldError(f"derived module name is unsafe: {module!r}")

    # Track everything we create so rollback removes only our artifacts —
    # never a directory the user had before us. Walk up before mkdir to
    # catch intermediate parent dirs that we'll create with parents=True.
    result = ScaffoldResult(path=target)
    created_files = result.created_files
    created_dirs = result.created_dirs
    try:
        # Identify which ancestor dirs DON'T exist yet so we can track
        # exactly which intermediate parents we're about to create.
        missing_ancestors: list[Path] = []
        cur = target
        while not cur.exists():
            missing_ancestors.append(cur)
            if cur.parent == cur:  # filesystem root
                break
            cur = cur.parent

        if missing_ancestors:
            target.mkdir(parents=True, exist_ok=True)
            # Record from outermost ancestor inward; cleanup walks reversed
            # (innermost first), which is the correct rmdir order.
            for p in reversed(missing_ancestors):
                created_dirs.append(p)

        src_parent = target / "src"
        if not src_parent.exists():
            src_parent.mkdir(parents=True, exist_ok=True)
            created_dirs.append(src_parent)
        src_dir = src_parent / module
        if not src_dir.exists():
            src_dir.mkdir(parents=True, exist_ok=True)
            created_dirs.append(src_dir)

        init_path = src_dir / "__init__.py"
        atomic_write(init_path, "")
        created_files.append(init_path)

        pyproject_path = target / "pyproject.toml"
        atomic_write(
            pyproject_path,
            PYPROJECT_TEMPLATE.format(
                name=_toml_escape(slug),  # safe by construction, but escape anyway
                description=_toml_escape(description or slug),
                module=module,
            ),
        )
        created_files.append(pyproject_path)

        readme_path = target / "README.md"
        atomic_write(
            readme_path,
            README_TEMPLATE.format(name=slug, description=description or slug, ts=ts),
        )
        created_files.append(readme_path)

        _git(target, ["init", "-q", "-b", "main"])
        # `git init` creates .git inside target — track as an OWNED dir
        # (we created it and own all its contents, so rmtree is safe).
        git_dir = target / ".git"
        if git_dir.exists():
            result.owned_dirs.append(git_dir)

        _git(target, ["add", "."])
        _git(
            target,
            [
                "-c", "user.name=chad-week",
                "-c", "user.email=chad-week@local",
                "commit", "-q", "-m", f"chore: scaffold {slug} via chad-week",
            ],
        )
    except Exception:
        # Surgical rollback before re-raising. Caller doesn't need to
        # clean up scaffold-internal failures.
        result.cleanup()
        raise
    return result


def _git(cwd: Path, args: list[str]) -> None:
    cmd = ["git", *GIT_SAFE_FLAGS, *args]
    try:
        proc = subprocess.run(
            cmd,
            cwd=cwd,
            capture_output=True,
            text=True,
            check=False,
            timeout=GIT_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as e:
        raise ScaffoldError(
            f"git {' '.join(args)} timed out after {GIT_TIMEOUT_SECONDS}s in {cwd}"
        ) from e
    except OSError as e:
        raise ScaffoldError(f"git not available or unreadable: {e}") from e

    if proc.returncode != 0:
        raise ScaffoldError(
            f"git {' '.join(args)} failed in {cwd} (exit {proc.returncode}): "
            f"{(proc.stderr or proc.stdout or '').strip()[:300]}"
        )


__all__ = ["ScaffoldError", "ScaffoldResult", "scaffold_greenfield"]
