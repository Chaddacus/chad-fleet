"""Cycle H — Django fixture validator tests.

We don't actually need a Django install to test the orchestration shape.
The driver script is testable end-to-end with a tiny stub `django` package
created in tmp_path that simulates loaddata + transaction.atomic.

This is more reliable than depending on a real Django version's loaddata
quirks, and keeps the chad-captain test suite self-contained.
"""

from __future__ import annotations

import json
import os
import sys
import textwrap
from pathlib import Path

import pytest

from chad_captain.fixture_validator import (
    FixtureValidation,
    validate_django_fixtures,
)


def _write_django_stub(repo: Path, *, scenario: str) -> None:
    """Create a tiny `django` package importable from `repo` that mimics
    the surface validate_django_fixtures uses. `scenario` controls what
    loaddata raises (or doesn't).
    """
    pkg = repo / "django"
    pkg.mkdir(parents=True, exist_ok=True)

    init_py = textwrap.dedent(f"""
        # Stub Django for chad-captain fixture_validator tests.
        SCENARIO = {scenario!r}

        def setup():
            return None
    """)
    (pkg / "__init__.py").write_text(init_py)

    # django.core.management.call_command
    core = pkg / "core"
    core.mkdir(exist_ok=True)
    (core / "__init__.py").write_text("")
    mgmt = core / "management"
    mgmt.mkdir(exist_ok=True)
    (mgmt / "__init__.py").write_text(textwrap.dedent("""
        from django import SCENARIO
        from django.db.utils import IntegrityError

        def call_command(name, *args, **kwargs):
            if name != "loaddata":
                raise RuntimeError(f"unexpected command: {name}")
            if SCENARIO == "ok":
                return
            if SCENARIO == "fk_violation":
                raise IntegrityError("FOREIGN KEY constraint failed")
            if SCENARIO == "unique_violation":
                raise RuntimeError("UNIQUE constraint failed: posts.slug")
            if SCENARIO == "setup_fail":
                raise RuntimeError("should not get here")
    """))

    # django.db.transaction + django.db.utils.IntegrityError
    db = pkg / "db"
    db.mkdir(exist_ok=True)
    (db / "__init__.py").write_text("")
    (db / "utils.py").write_text(textwrap.dedent("""
        class IntegrityError(Exception):
            pass
    """))
    (db / "transaction.py").write_text(textwrap.dedent("""
        import contextlib

        @contextlib.contextmanager
        def atomic():
            yield

        def set_rollback(_flag):
            return None
    """))


def test_returns_error_when_repo_missing(tmp_path: Path) -> None:
    res = validate_django_fixtures(
        tmp_path / "does-not-exist",
        ["fixture.json"],
        settings_module="cfg.settings",
    )
    assert isinstance(res, FixtureValidation)
    assert res.ok is False
    assert "not a directory" in res.summary


def test_returns_error_when_no_fixtures(tmp_path: Path) -> None:
    res = validate_django_fixtures(
        tmp_path, [], settings_module="cfg.settings",
    )
    assert res.ok is False
    assert "no fixture paths" in res.summary


def test_loaddata_ok_returns_passing_validation(tmp_path: Path) -> None:
    _write_django_stub(tmp_path, scenario="ok")
    res = validate_django_fixtures(
        tmp_path,
        ["fixture1.json", "fixture2.json"],
        settings_module="cfg.settings",
    )
    assert res.ok is True
    assert res.exit_code == 0
    assert "2 fixture files" in res.summary


def test_loaddata_fk_violation_surfaces_in_summary(tmp_path: Path) -> None:
    _write_django_stub(tmp_path, scenario="fk_violation")
    res = validate_django_fixtures(
        tmp_path,
        ["broken.json"],
        settings_module="cfg.settings",
    )
    assert res.ok is False
    assert res.exit_code == 2
    assert "FK violation" in res.summary
    assert "FOREIGN KEY" in res.summary


def test_loaddata_other_error_surfaces_as_load_error(tmp_path: Path) -> None:
    _write_django_stub(tmp_path, scenario="unique_violation")
    res = validate_django_fixtures(
        tmp_path,
        ["dup.json"],
        settings_module="cfg.settings",
    )
    assert res.ok is False
    assert res.exit_code == 3
    assert "load error" in res.summary
    assert "UNIQUE" in res.summary


def test_django_not_installed_returns_exit_5(tmp_path: Path) -> None:
    """No django stub written → real interpreter can't import django."""
    # Use an explicit empty PYTHONPATH so a system django (if any) is hidden.
    res = validate_django_fixtures(
        tmp_path,
        ["fixture.json"],
        settings_module="cfg.settings",
        extra_env={"PYTHONPATH": str(tmp_path)},
    )
    # Exit 5 only if no system django is importable; otherwise exit 6 (setup
    # failure on the bogus settings module). Either way ok=False.
    assert res.ok is False
    assert res.exit_code in (5, 6)


def test_timeout_returns_negative_exit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Driver hang → caller's timeout_seconds bound is honored."""
    import subprocess as _subproc

    def fake_run(*args, **kwargs):
        raise _subproc.TimeoutExpired(cmd=args, timeout=kwargs.get("timeout"))

    monkeypatch.setattr(
        "chad_captain.fixture_validator.subprocess.run", fake_run,
    )
    res = validate_django_fixtures(
        tmp_path,
        ["x.json"],
        settings_module="cfg.settings",
        timeout_seconds=1,
    )
    assert res.ok is False
    assert res.exit_code == -2
    assert "timed out" in res.summary


def test_passes_fixtures_via_env_not_argv(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify call shape: fixtures travel via env, not via shell-quoted argv."""
    captured = {}

    class _Result:
        def __init__(self) -> None:
            self.returncode = 0
            self.stdout = "OK\n"
            self.stderr = ""

    def fake_run(cmd, *, cwd, env, capture_output, text, timeout):
        captured["cmd"] = cmd
        captured["cwd"] = cwd
        captured["env"] = env
        return _Result()

    monkeypatch.setattr(
        "chad_captain.fixture_validator.subprocess.run", fake_run,
    )

    validate_django_fixtures(
        tmp_path,
        ["a.json", "b.json"],
        settings_module="cfg.settings",
    )
    # No fixture path appears in argv (just python -c <DRIVER>).
    assert all("a.json" not in s and "b.json" not in s for s in captured["cmd"])
    # All fixture paths in env JSON.
    fixtures = json.loads(captured["env"]["CHAD_CAPTAIN_FIXTURES_JSON"])
    assert fixtures == ["a.json", "b.json"]
    assert captured["env"]["DJANGO_SETTINGS_MODULE"] == "cfg.settings"


def test_extra_env_overrides_pythonpath(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured = {}

    class _Result:
        returncode = 0
        stdout = "OK\n"
        stderr = ""

    def fake_run(cmd, *, cwd, env, capture_output, text, timeout):
        captured["env"] = env
        return _Result()

    monkeypatch.setattr(
        "chad_captain.fixture_validator.subprocess.run", fake_run,
    )

    validate_django_fixtures(
        tmp_path, ["a.json"], settings_module="cfg",
        extra_env={"PYTHONPATH": "/custom/path", "DJANGO_DEBUG": "1"},
    )
    assert captured["env"]["PYTHONPATH"] == "/custom/path"
    assert captured["env"]["DJANGO_DEBUG"] == "1"
