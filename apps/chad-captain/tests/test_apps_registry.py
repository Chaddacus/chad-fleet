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


# ---------------------------------------------------------------------------
# Cycle G — repo_path normalization
# ---------------------------------------------------------------------------


def test_repo_path_expands_tilde() -> None:
    """Registry storage of `~/code/spark` was breaking API existence checks
    that did `Path(entry.repo_path).exists()` without expanduser."""
    import os
    app = RegisteredApp(app_id="x", name="X", repo_path="~/code/foo")
    assert "~" not in app.repo_path
    assert app.repo_path.startswith(os.path.expanduser("~"))


def test_repo_path_absolute_unchanged() -> None:
    app = RegisteredApp(app_id="x", name="X", repo_path="/tmp/abs")
    assert app.repo_path == "/tmp/abs"


def test_repo_path_round_trip_with_tilde(_isolate_registry: Path) -> None:
    import os
    reg = AppsRegistry(apps=[
        RegisteredApp(app_id="x", name="X", repo_path="~/foo/bar"),
    ])
    save_registry(reg)
    loaded = load_registry()
    assert "~" not in loaded.apps[0].repo_path
    assert loaded.apps[0].repo_path.startswith(os.path.expanduser("~"))


def test_repo_path_empty_string_passthrough() -> None:
    """Edge case: validator must not crash on empty string."""
    app = RegisteredApp(app_id="x", name="X", repo_path="")
    assert app.repo_path == ""


# ---------------------------------------------------------------------------
# PR2 — goose-runner plist generator (R3-HIGH-1)
# ---------------------------------------------------------------------------


def test_goose_runner_label_is_distinct_from_tick_label() -> None:
    from chad_captain.launchd import goose_runner_label_for, label_for
    app = RegisteredApp(app_id="x", name="X", repo_path="/tmp/x")
    tick = label_for(app)
    runner = goose_runner_label_for(app)
    assert tick != runner
    assert runner.endswith(".goose-runner")


def test_render_goose_runner_plist_uses_keepalive_true(tmp_path: Path) -> None:
    from chad_captain.launchd import render_goose_runner_plist
    app = RegisteredApp(app_id="x", name="X", repo_path="/tmp/x")
    plist = render_goose_runner_plist(app, runner_bin="/usr/bin/runner")
    assert "<key>KeepAlive</key>" in plist
    # KeepAlive must be true so launchd respawns the runner if it dies.
    assert "<true/>" in plist
    assert "<key>RunAtLoad</key>" in plist
    assert "/usr/bin/runner" in plist
    assert "<string>x</string>" in plist
    assert "/tmp/x" in plist


def test_write_goose_runner_plist_creates_distinct_file(tmp_path: Path) -> None:
    from chad_captain.launchd import (
        goose_runner_label_for, label_for,
        write_goose_runner_plist, write_plist,
    )
    app = RegisteredApp(app_id="x", name="X", repo_path="/tmp/x",
                        mode="autonomous")
    target = tmp_path / "agents"
    tick_path = write_plist(app, target_dir=target)
    runner_path = write_goose_runner_plist(app, target_dir=target)
    assert tick_path.exists()
    assert runner_path.exists()
    assert tick_path != runner_path
    assert label_for(app) in tick_path.name
    assert goose_runner_label_for(app) in runner_path.name


def test_goose_runner_bootstrap_command_format() -> None:
    from chad_captain.launchd import (
        goose_runner_bootstrap_command,
        goose_runner_plist_path_for,
    )
    app = RegisteredApp(app_id="x", name="X", repo_path="/tmp/x")
    cmd = goose_runner_bootstrap_command(app)
    assert cmd[0] == "launchctl"
    assert cmd[1] == "bootstrap"
    assert str(goose_runner_plist_path_for(app)) in cmd


# ---------------------------------------------------------------------------
# PR6: file locking + transaction + enabled field
# ---------------------------------------------------------------------------


def test_registered_app_enabled_default_true() -> None:
    """PR6/v8 R5#2: enabled defaults to True for back-compat."""
    app = RegisteredApp(app_id="x", name="X", repo_path="/tmp/x")
    assert app.enabled is True


def test_registered_app_enabled_can_be_false() -> None:
    """Scaffold staging: phase 4 REGISTER writes enabled=False; phase 5
    flips to True only on successful activation."""
    app = RegisteredApp(app_id="x", name="X", repo_path="/tmp/x", enabled=False)
    assert app.enabled is False


def test_save_load_roundtrips_enabled_field() -> None:
    reg = AppsRegistry(apps=[
        RegisteredApp(app_id="active", name="A", repo_path="/tmp/a", enabled=True),
        RegisteredApp(app_id="staged", name="S", repo_path="/tmp/s", enabled=False),
    ])
    save_registry(reg)
    out = load_registry()
    assert out.by_id("active").enabled is True
    assert out.by_id("staged").enabled is False


def test_load_registry_uses_atomic_write_no_torn_read(tmp_path: Path) -> None:
    """PR6 R3#1: save_registry uses tempfile + os.replace so a concurrent
    reader never sees a partial JSON file. Simulate by writing many times
    and reading; never see a parse error from torn write."""
    reg = AppsRegistry(apps=[
        RegisteredApp(app_id="x", name="X", repo_path="/tmp/x"),
    ])
    for _ in range(50):
        save_registry(reg)
        out = load_registry()
        assert len(out.apps) == 1
        assert out.apps[0].app_id == "x"


def test_registry_transaction_commits_on_normal_exit() -> None:
    from chad_captain.apps_registry import registry_transaction
    with registry_transaction() as reg:
        reg.upsert(RegisteredApp(app_id="t1", name="T1", repo_path="/tmp/t1"))
    out = load_registry()
    assert out.by_id("t1") is not None


def test_registry_transaction_swallows_changes_if_caller_raises() -> None:
    """Sanity: if the caller raises inside the transaction, save still
    commits whatever was mutated up to the raise. fcntl flock is released
    on context-manager exit either way. Documenting current behavior so
    callers don't expect rollback semantics from the transaction."""
    from chad_captain.apps_registry import registry_transaction
    initial = AppsRegistry(apps=[
        RegisteredApp(app_id="keep", name="K", repo_path="/tmp/k"),
    ])
    save_registry(initial)
    with pytest.raises(RuntimeError):
        with registry_transaction() as reg:
            reg.upsert(RegisteredApp(app_id="new", name="N", repo_path="/tmp/n"))
            raise RuntimeError("simulated crash")
    out = load_registry()
    # Pre-existing app still there; new app NOT committed because the raise
    # happened before the save block.
    assert out.by_id("keep") is not None
    assert out.by_id("new") is None


def test_registry_lock_path_separate_from_registry_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The lock file is sibling to the registry file with a `.` prefix +
    `.lock` suffix, NOT a different directory."""
    from chad_captain.apps_registry import registry_lock_path, registry_path
    assert registry_lock_path() != registry_path()
    assert registry_lock_path().parent == registry_path().parent


def test_registry_lock_path_env_override(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    custom = tmp_path / "custom.lock"
    monkeypatch.setenv("CHAD_CAPTAIN_APPS_REGISTRY_LOCK", str(custom))
    from chad_captain.apps_registry import registry_lock_path
    assert registry_lock_path() == custom


def test_load_registry_propagates_parse_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """PR6 R3#1: prior load_registry() swallowed ANY exception as 'empty
    registry'. That hid corruption from daemon ticks. New behavior:
    propagate the exception so the caller sees the real failure."""
    from chad_captain.apps_registry import registry_path
    p = registry_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("not-json{{")
    with pytest.raises(Exception):
        load_registry()


# ---------------------------------------------------------------------------
# PR6 Slice 3: task_id propagation through models
# ---------------------------------------------------------------------------


def test_current_slice_task_id_default_none() -> None:
    from chad_captain.protocol import CurrentSlice
    cs = CurrentSlice(
        slice_id="s1", app_id="a", objective="o",
        system_prompt="s", user_prompt="u", repo_path="/tmp/r",
    )
    assert cs.task_id is None


def test_current_slice_task_id_roundtrips() -> None:
    from chad_captain.protocol import CurrentSlice
    cs = CurrentSlice(
        slice_id="s1", app_id="a", objective="o",
        system_prompt="s", user_prompt="u", repo_path="/tmp/r",
        task_id="task-abc",
    )
    rt = CurrentSlice.model_validate_json(cs.model_dump_json())
    assert rt.task_id == "task-abc"


def test_slice_complete_carries_task_id_and_removed_tests_reason() -> None:
    from chad_captain.protocol import SliceComplete
    sc = SliceComplete(
        slice_id="s1", app_id="a", duration_seconds=1.0, goose_exit_code=0,
        summary="ok", task_id="t-1", removed_tests_reason="rationale",
    )
    rt = SliceComplete.model_validate_json(sc.model_dump_json())
    assert rt.task_id == "t-1"
    assert rt.removed_tests_reason == "rationale"


def test_captain_log_entry_carries_task_id() -> None:
    from chad_captain.protocol import CaptainLogEntry
    e = CaptainLogEntry(
        app_id="a", kind="dispatch", rationale="x", task_id="t-1",
    )
    rt = CaptainLogEntry.model_validate_json(e.model_dump_json())
    assert rt.task_id == "t-1"


def test_roadmap_slice_carries_task_id() -> None:
    from chad_captain.protocol import RoadmapSlice
    rs = RoadmapSlice(
        slice_id="s1", objective="o", task_id="t-1",
    )
    rt = RoadmapSlice.model_validate_json(rs.model_dump_json())
    assert rt.task_id == "t-1"


def test_feature_backlog_item_carries_task_id() -> None:
    from chad_captain.protocol import FeatureBacklogItem
    fb = FeatureBacklogItem(id="fb-001", title="x", task_id="t-1")
    rt = FeatureBacklogItem.model_validate_json(fb.model_dump_json())
    assert rt.task_id == "t-1"


def test_build_current_slice_propagates_task_id() -> None:
    from chad_captain.protocol import RoadmapSlice
    from chad_captain.validator import build_current_slice
    rs = RoadmapSlice(
        slice_id="s1", objective="ship a thing", task_id="task-xyz",
    )
    cs = build_current_slice(rs, app_id="my-app", repo_path="/tmp/r")
    assert cs.task_id == "task-xyz"


def test_build_current_slice_task_id_none_when_roadmap_slice_has_none() -> None:
    from chad_captain.protocol import RoadmapSlice
    from chad_captain.validator import build_current_slice
    rs = RoadmapSlice(slice_id="s1", objective="x")
    cs = build_current_slice(rs, app_id="my-app", repo_path="/tmp/r")
    assert cs.task_id is None


def test_legacy_models_load_without_task_id_field() -> None:
    """Migration safety: pre-PR6 JSON without task_id field still loads."""
    from chad_captain.protocol import (
        CaptainLogEntry, CurrentSlice, FeatureBacklogItem,
        RoadmapSlice, SliceComplete,
    )
    # Each model loads from JSON that omits task_id — defaults to None.
    cs = CurrentSlice.model_validate_json(
        '{"slice_id":"s","app_id":"a","objective":"o",'
        '"system_prompt":"s","user_prompt":"u","repo_path":"/tmp"}'
    )
    assert cs.task_id is None
    sc = SliceComplete.model_validate_json(
        '{"slice_id":"s","app_id":"a","duration_seconds":1,'
        '"goose_exit_code":0,"summary":"ok"}'
    )
    assert sc.task_id is None
    cle = CaptainLogEntry.model_validate_json(
        '{"app_id":"a","kind":"dispatch"}'
    )
    assert cle.task_id is None
    rs = RoadmapSlice.model_validate_json(
        '{"slice_id":"s","objective":"o"}'
    )
    assert rs.task_id is None
    fb = FeatureBacklogItem.model_validate_json(
        '{"id":"fb-1","title":"x"}'
    )
    assert fb.task_id is None


# ---------------------------------------------------------------------------
# PR7 R3#7: verify_cmd required when auto_merge=True
# ---------------------------------------------------------------------------


def test_auto_merge_without_verify_cmd_rejected():
    """Auto-merging without a build gate is unsafe — reject at construction."""
    import pytest
    from chad_captain.apps_registry import RegisteredApp
    with pytest.raises(ValueError, match="verify_cmd is unset"):
        RegisteredApp(
            app_id="x", name="X", repo_path="/tmp/x",
            mode="autonomous", auto_merge=True,
        )


def test_auto_merge_with_empty_verify_cmd_rejected():
    """Whitespace-only verify_cmd is treated as unset."""
    import pytest
    from chad_captain.apps_registry import RegisteredApp
    with pytest.raises(ValueError, match="verify_cmd is unset"):
        RegisteredApp(
            app_id="x", name="X", repo_path="/tmp/x",
            mode="autonomous", auto_merge=True, verify_cmd="   ",
        )


def test_auto_merge_with_verify_cmd_accepted():
    """Auto-merge + non-empty verify_cmd constructs cleanly."""
    from chad_captain.apps_registry import RegisteredApp
    app = RegisteredApp(
        app_id="x", name="X", repo_path="/tmp/x",
        mode="autonomous", auto_merge=True, verify_cmd="make check",
    )
    assert app.auto_merge is True
    assert app.verify_cmd == "make check"


def test_auto_merge_off_without_verify_cmd_accepted():
    """Default (auto_merge=False) does NOT require verify_cmd — back-compat."""
    from chad_captain.apps_registry import RegisteredApp
    app = RegisteredApp(
        app_id="x", name="X", repo_path="/tmp/x", mode="autonomous",
    )
    assert app.auto_merge is False
    assert app.verify_cmd is None


# ---------------------------------------------------------------------------
# PR12 R3#7 v6 §validation close: VerifyHost SSH model
# ---------------------------------------------------------------------------


def test_verify_host_default_constructs():
    """Minimal VerifyHost only requires hostname; rest defaults sanely."""
    from chad_captain.apps_registry import VerifyHost
    vh = VerifyHost(hostname="ci.example.com")
    assert vh.hostname == "ci.example.com"
    assert vh.user == "root"
    assert vh.port == 22
    assert vh.identity_file is None
    assert vh.remote_workdir == "."
    assert vh.ssh_options == []


def test_verify_host_full_construct():
    from chad_captain.apps_registry import VerifyHost
    vh = VerifyHost(
        hostname="ci.example.com", user="builder", port=2222,
        identity_file="/home/captain/.ssh/ci_key",
        remote_workdir="/srv/build",
        ssh_options=["ConnectTimeout=10", "StrictHostKeyChecking=accept-new"],
    )
    assert vh.user == "builder"
    assert vh.port == 2222
    assert vh.remote_workdir == "/srv/build"
    assert "ConnectTimeout=10" in vh.ssh_options


def test_registered_app_with_verify_host():
    """RegisteredApp accepts a VerifyHost and roundtrips through model_dump."""
    import json
    from chad_captain.apps_registry import RegisteredApp, VerifyHost
    app = RegisteredApp(
        app_id="remote-app", name="R", repo_path="/tmp/r",
        mode="autonomous",
        verify_cmd="make check",
        verify_host=VerifyHost(hostname="ci.example.com", user="builder"),
    )
    blob = app.model_dump_json()
    parsed = RegisteredApp.model_validate_json(blob)
    assert parsed.verify_host is not None
    assert parsed.verify_host.hostname == "ci.example.com"
    assert parsed.verify_host.user == "builder"


def test_registered_app_verify_host_none_by_default():
    """Back-compat: existing apps without verify_host keep working."""
    from chad_captain.apps_registry import RegisteredApp
    app = RegisteredApp(
        app_id="x", name="X", repo_path="/tmp/x", mode="autonomous",
    )
    assert app.verify_host is None
