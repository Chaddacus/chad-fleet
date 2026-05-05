"""Tests for the captain apps registry + launchd plist generator."""

from __future__ import annotations

from pathlib import Path

import pytest

from chad_captain.apps_registry import (
    AUTHOR_TOOLKIT_DEFAULT,
    AppsRegistry,
    DEFAULT_SEEDS,
    RegisteredApp,
    SPARK_DEFAULT,
    load_registry,
    save_registry,
    seed_default_registry,
)
from chad_captain.launchd import (
    LABEL_PREFIX,
    bootstrap_command,
    label_for,
    plist_path_for,
    render_plist,
    write_plist,
)


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _isolate_registry(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    target = tmp_path / "captain" / "apps_registry.json"
    monkeypatch.setenv("CHAD_CAPTAIN_APPS_REGISTRY", str(target))
    return target


def test_load_registry_empty_when_missing() -> None:
    reg = load_registry()
    assert reg.apps == []


def test_save_and_load_round_trip() -> None:
    reg = AppsRegistry(apps=[
        RegisteredApp(app_id="x", name="X", repo_path="/tmp/x"),
    ])
    save_registry(reg)
    loaded = load_registry()
    assert len(loaded.apps) == 1
    assert loaded.apps[0].app_id == "x"


def test_upsert_replaces_existing() -> None:
    reg = AppsRegistry()
    reg.upsert(RegisteredApp(app_id="x", name="X", repo_path="/old"))
    reg.upsert(RegisteredApp(app_id="x", name="X", repo_path="/new", schedule_hour=12))
    assert len(reg.apps) == 1
    assert reg.apps[0].repo_path == "/new"
    assert reg.apps[0].schedule_hour == 12


def test_seed_default_registry() -> None:
    reg = seed_default_registry()
    ids = {a.app_id for a in reg.apps}
    assert {"spark-of-defiance", "author-toolkit"}.issubset(ids)
    spark = reg.by_id("spark-of-defiance")
    assert spark.mode == "observe_only"
    at = reg.by_id("author-toolkit")
    assert at.mode == "autonomous"


def test_seed_default_registry_idempotent() -> None:
    seed_default_registry()
    # Modify an entry
    reg = load_registry()
    reg.apps[0].schedule_hour = 18
    save_registry(reg)
    # Re-seed without --force should not clobber
    seed_default_registry()
    again = load_registry()
    assert again.apps[0].schedule_hour == 18


def test_seed_default_registry_force_overwrites() -> None:
    seed_default_registry()
    reg = load_registry()
    reg.apps.clear()
    save_registry(reg)
    seed_default_registry(force=True)
    final = load_registry()
    assert len(final.apps) == len(DEFAULT_SEEDS)


def test_default_seeds_have_consistent_app_ids() -> None:
    assert SPARK_DEFAULT.app_id == "spark-of-defiance"
    assert AUTHOR_TOOLKIT_DEFAULT.app_id == "author-toolkit"


# ---------------------------------------------------------------------------
# Cycle C — pluggable validator field
# ---------------------------------------------------------------------------


def test_validator_module_defaults_to_none() -> None:
    app = RegisteredApp(app_id="x", name="X", repo_path="/tmp/x")
    assert app.validator_module is None


def test_validator_module_round_trips() -> None:
    reg = AppsRegistry(apps=[
        RegisteredApp(
            app_id="x",
            name="X",
            repo_path="/tmp/x",
            validator_module="my_pkg.my_validator",
        ),
    ])
    save_registry(reg)
    loaded = load_registry()
    assert loaded.apps[0].validator_module == "my_pkg.my_validator"


def test_existing_registry_loads_when_validator_module_missing(
    _isolate_registry: Path,
) -> None:
    """Back-compat: registry JSON written before Cycle C lacks `validator_module`.

    Pydantic should default it to None on load; loading must not error.
    """
    legacy_json = (
        '{"apps": [{"app_id": "old", "name": "Old", "repo_path": "/tmp/old", '
        '"mode": "observe_only", "schedule_hour": 9}]}'
    )
    _isolate_registry.parent.mkdir(parents=True, exist_ok=True)
    _isolate_registry.write_text(legacy_json)
    loaded = load_registry()
    assert len(loaded.apps) == 1
    assert loaded.apps[0].validator_module is None


# ---------------------------------------------------------------------------
# launchd
# ---------------------------------------------------------------------------


def test_label_for_includes_prefix() -> None:
    app = RegisteredApp(app_id="my-app", name="My App", repo_path="/tmp/r")
    assert label_for(app) == f"{LABEL_PREFIX}.my-app"


def test_render_plist_contains_app_id_and_repo() -> None:
    app = RegisteredApp(app_id="x", name="X", repo_path="/tmp/x", schedule_hour=15)
    plist = render_plist(app, captain_bin="/usr/local/bin/chad-captain")
    assert "<string>x</string>" in plist
    assert "/tmp/x" in plist
    assert "<integer>15</integer>" in plist
    assert "/usr/local/bin/chad-captain" in plist
    assert "<string>tick</string>" in plist


def test_render_plist_uses_resolved_bin_when_not_specified() -> None:
    app = RegisteredApp(app_id="x", name="X", repo_path="/tmp/x")
    plist = render_plist(app)
    # Should include some path that ends in chad-captain
    assert "chad-captain" in plist


def test_write_plist_creates_file(tmp_path: Path) -> None:
    app = RegisteredApp(app_id="x", name="X", repo_path="/tmp/x")
    target = tmp_path / "agents"
    path = write_plist(app, target_dir=target)
    assert path.exists()
    assert path.suffix == ".plist"
    assert "x" in path.read_text()


def test_bootstrap_command_format() -> None:
    app = RegisteredApp(app_id="x", name="X", repo_path="/tmp/x")
    cmd = bootstrap_command(app)
    assert cmd[0] == "launchctl"
    assert cmd[1] == "bootstrap"
    assert str(plist_path_for(app)) in cmd
