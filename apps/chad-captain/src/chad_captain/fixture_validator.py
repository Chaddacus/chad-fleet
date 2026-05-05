"""Cycle H — Django fixture FK validator with loaddata rollback.

Utility for per-task captains (T3 marketing, others) that emit Django
fixtures as slice artifacts. Before the captain accepts the slice, it
calls ``validate_django_fixtures(...)`` which:

  1. Spawns a Python subprocess inside the target repo.
  2. The subprocess sets ``DJANGO_SETTINGS_MODULE``, calls ``django.setup()``,
     opens a ``transaction.atomic`` block, runs ``loaddata`` against the
     supplied fixture files, then sets ``transaction.set_rollback(True)``
     so nothing actually commits.
  3. Captures stdout/stderr + exit code; an FK violation surfaces as a
     non-zero exit + ``FK_VIOLATION:`` line in stderr.

Why subprocess and not in-process: chad-captain doesn't import Django, and
running Django setup against a foreign settings module from inside the
captain process would pollute sys.modules and lose isolation between apps
on a multi-app daemon. Subprocess is one-and-done — clean state every call.

Why loaddata + rollback (not just JSON parsing for FKs): natural keys,
custom serializers, and signals all run during real loaddata. Static
parsing misses anything beyond shape-level FK refs. The transaction
rollback guarantees the prod DB is untouched.

Public API:

    validate_django_fixtures(
        repo_path: str | Path,
        fixture_paths: list[str | Path],
        *,
        settings_module: str,
        python: str = sys.executable,
        timeout_seconds: int = 60,
    ) -> FixtureValidation
"""

from __future__ import annotations

import os
import shlex
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

# Driver script executed in the target repo's interpreter. Kept as one string
# rather than a separate file so callers don't have to know where chad-captain
# is installed; we just pipe it via `python -c`.
_DRIVER = """
import os
import sys
import json

settings = os.environ.get("DJANGO_SETTINGS_MODULE")
if not settings:
    print("MISSING_SETTINGS", file=sys.stderr)
    sys.exit(4)

try:
    import django
except Exception as e:
    print(f"DJANGO_NOT_INSTALLED: {e}", file=sys.stderr)
    sys.exit(5)

try:
    django.setup()
except Exception as e:
    print(f"DJANGO_SETUP_FAILED: {type(e).__name__}: {e}", file=sys.stderr)
    sys.exit(6)

from django.core.management import call_command
from django.db import transaction
from django.db.utils import IntegrityError

fixtures = json.loads(os.environ["CHAD_CAPTAIN_FIXTURES_JSON"])

try:
    with transaction.atomic():
        call_command("loaddata", *fixtures, verbosity=0)
        transaction.set_rollback(True)
    print("OK")
    sys.exit(0)
except IntegrityError as e:
    print(f"FK_VIOLATION: {type(e).__name__}: {e}", file=sys.stderr)
    sys.exit(2)
except Exception as e:
    print(f"LOAD_ERROR: {type(e).__name__}: {e}", file=sys.stderr)
    sys.exit(3)
"""


@dataclass
class FixtureValidation:
    ok: bool
    summary: str
    exit_code: int
    stdout: str = ""
    stderr: str = ""


def validate_django_fixtures(
    repo_path: str | Path,
    fixture_paths: list[str | Path],
    *,
    settings_module: str,
    python: str | None = None,
    timeout_seconds: int = 60,
    extra_env: dict[str, str] | None = None,
) -> FixtureValidation:
    """Run Django ``loaddata`` against fixture_paths inside a rolled-back
    transaction. Return a FixtureValidation describing the outcome.

    ``settings_module`` must be importable from the repo's Python path
    (e.g. ``"config.settings.test"``). ``python`` defaults to the current
    interpreter — override when the target repo uses a venv whose Python
    has Django installed but the captain's doesn't.

    Exit-code conventions (from the embedded driver):
      0 = OK         — fixtures loaded cleanly (rolled back)
      2 = FK violation
      3 = Other load error (unique constraint, missing model, etc.)
      4 = Settings module env var was empty (caller bug)
      5 = Django not installed in target interpreter
      6 = Django setup failed (settings import error, app init crash)
    """
    repo = Path(repo_path).expanduser().resolve()
    if not repo.is_dir():
        return FixtureValidation(
            ok=False,
            summary=f"repo_path not a directory: {repo}",
            exit_code=-1,
        )

    if not fixture_paths:
        return FixtureValidation(
            ok=False,
            summary="no fixture paths supplied",
            exit_code=-1,
        )

    fixture_strs = [str(p) for p in fixture_paths]

    env = {
        **os.environ,
        "DJANGO_SETTINGS_MODULE": settings_module,
        # Pass fixtures via env (not argv) so paths with spaces / special
        # chars don't need quoting gymnastics in the shell-less invocation.
        "CHAD_CAPTAIN_FIXTURES_JSON": _json_dumps(fixture_strs),
    }
    if extra_env:
        env.update(extra_env)

    interpreter = python or sys.executable

    try:
        proc = subprocess.run(  # noqa: S603 — trusted local invocation
            [interpreter, "-c", _DRIVER],
            cwd=str(repo),
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired:
        return FixtureValidation(
            ok=False,
            summary=f"loaddata validation timed out after {timeout_seconds}s",
            exit_code=-2,
        )
    except OSError as e:
        return FixtureValidation(
            ok=False,
            summary=f"failed to launch python: {e}",
            exit_code=-3,
        )

    rc = proc.returncode
    summary = _summarize(rc, proc.stdout, proc.stderr, fixture_strs)
    return FixtureValidation(
        ok=(rc == 0),
        summary=summary,
        exit_code=rc,
        stdout=proc.stdout,
        stderr=proc.stderr,
    )


def _json_dumps(value: list[str]) -> str:
    import json
    return json.dumps(value)


def _summarize(rc: int, stdout: str, stderr: str, fixtures: list[str]) -> str:
    """Render a short human-readable summary line for the captain log."""
    n = len(fixtures)
    if rc == 0:
        return f"loaddata --dry-run OK ({n} fixture file{'s' if n != 1 else ''})"
    err_tail = (stderr or stdout or "").strip().splitlines()
    first = err_tail[0] if err_tail else "(no stderr)"
    code_label = {
        2: "FK violation",
        3: "load error",
        4: "missing settings module",
        5: "Django not installed",
        6: "Django setup failed",
    }.get(rc, f"exit {rc}")
    return f"{code_label}: {first[:300]}"


__all__ = [
    "FixtureValidation",
    "validate_django_fixtures",
]
