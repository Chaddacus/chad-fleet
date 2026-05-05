"""Tests for the captain replanner."""

from __future__ import annotations

from pathlib import Path

import pytest

from chad_captain.protocol import (
    AdmiralNote,
    AppWorkspace,
    CaptainLogEntry,
    Roadmap,
    RoadmapSlice,
    append_captain_log,
    read_roadmap,
    write_admiral_note,
    write_roadmap,
)
from chad_captain.replanner import (
    REPLAN_TRIGGERS,
    _clean_title,
    _detect_trigger,
    _fallback_roadmap,
    replan,
    replan_if_needed,
)
from chad_captain.replanner import ReplanContext
from chad_captain.research import AppProfile
from chad_captain.research.local import LocalProfile
from chad_captain.research.web import WebProfile
from chad_captain.scorecard import DimensionScore, Scorecard


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def ws(tmp_path: Path) -> AppWorkspace:
    w = AppWorkspace("test-app", base=tmp_path)
    w.ensure()
    return w


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    r = tmp_path / "repo"
    r.mkdir()
    (r / "README.md").write_text("# Repo\n\nDescription.\n")
    (r / "main.py").write_text("x = 1\n")
    return r


@pytest.fixture(autouse=True)
def _no_live_calls(monkeypatch):
    """Replanner indirectly calls synthesize_profile (web research) and
    claude_json. Stub both so tests are deterministic and fast. Tests that
    care about LLM-success path override claude_json themselves."""
    from chad_captain import replanner as r
    from chad_captain.research import synthesize as syn_mod
    from chad_captain.research import web as web_mod

    monkeypatch.setattr(web_mod, "research_web",
                         lambda **_kw: web_mod.WebProfile.skipped("test-stub"))
    monkeypatch.setattr(syn_mod, "research_web",
                         lambda **_kw: web_mod.WebProfile.skipped("test-stub"))

    def _default_claude_json(*_a, **_kw):
        raise r.LLMError("test-stub: claude_json not configured for this test")
    monkeypatch.setattr(r, "claude_json", _default_claude_json)


def _profile() -> AppProfile:
    return AppProfile(
        app_id="test-app",
        local=LocalProfile(repo_path="/tmp/r", name="r"),
        web=WebProfile.skipped("test"),
        summary="A test project.",
    )


def _scorecard(*dims) -> Scorecard:
    if not dims:
        dims = (
            DimensionScore(name="tests_present", score=0.4, rationale="few tests"),
            DimensionScore(name="docs_present", score=0.2, rationale="thin"),
            DimensionScore(name="todo_pressure", score=0.9, rationale="ok"),
        )
    aggregate = sum(d.score for d in dims) / len(dims)
    return Scorecard(repo_path="/tmp/r", dimensions=list(dims), aggregate=aggregate)


# ---------------------------------------------------------------------------
# Trigger detection
# ---------------------------------------------------------------------------


def test_detect_trigger_initial_when_no_roadmap(ws: AppWorkspace) -> None:
    assert _detect_trigger(ws) == "initial"


def test_detect_trigger_exhausted_when_no_queued(ws: AppWorkspace) -> None:
    write_roadmap(ws, Roadmap(
        app_id="test-app",
        slices=[RoadmapSlice(slice_id="s1", objective="a", status="done")],
    ))
    assert _detect_trigger(ws) == "exhausted"


def test_detect_trigger_none_when_queued_present(ws: AppWorkspace) -> None:
    write_roadmap(ws, Roadmap(
        app_id="test-app",
        slices=[RoadmapSlice(slice_id="s1", objective="a", status="queued")],
    ))
    assert _detect_trigger(ws) is None


def test_detect_trigger_soft_accept_streak(ws: AppWorkspace) -> None:
    write_roadmap(ws, Roadmap(
        app_id="test-app",
        slices=[RoadmapSlice(slice_id="s1", objective="a", status="queued")],
    ))
    for _ in range(2):
        append_captain_log(ws, CaptainLogEntry(
            app_id="test-app", slice_id="s", kind="validate",
            verdict="soft_accept", rationale="low yield",
        ))
    assert _detect_trigger(ws) == "soft_accept_streak"


def test_detect_trigger_admiral_note_replan(ws: AppWorkspace) -> None:
    write_roadmap(ws, Roadmap(
        app_id="test-app",
        slices=[RoadmapSlice(slice_id="s1", objective="a", status="queued")],
    ))
    write_admiral_note(ws, AdmiralNote(
        note_id="n1", app_id="test-app",
        body="Please replan — direction shifted.",
    ))
    assert _detect_trigger(ws) == "admiral_note"


# ---------------------------------------------------------------------------
# Fallback roadmap
# ---------------------------------------------------------------------------


def test_fallback_targets_weakest_dimensions() -> None:
    sc = _scorecard(
        DimensionScore(name="tests_present", score=0.0, rationale=""),
        DimensionScore(name="docs_present", score=0.0, rationale=""),
        DimensionScore(name="todo_pressure", score=1.0, rationale=""),
    )
    ctx = ReplanContext(trigger="exhausted", profile=_profile(), scorecard=sc)
    rm = _fallback_roadmap(ctx, app_id="test-app")
    objectives = " ".join(s.objective for s in rm.slices)
    assert "tests" in objectives.lower()
    assert "readme" in objectives.lower() or "docs" in objectives.lower()


def test_fallback_healthy_scorecard_returns_smoke_slices() -> None:
    sc = _scorecard(
        DimensionScore(name="x", score=1.0, rationale=""),
        DimensionScore(name="y", score=1.0, rationale=""),
    )
    ctx = ReplanContext(trigger="manual", profile=_profile(), scorecard=sc)
    rm = _fallback_roadmap(ctx, app_id="test-app")
    assert len(rm.slices) >= 1
    assert all(s.status == "queued" for s in rm.slices)


def test_fallback_blocks_subsequent_slices_on_predecessor() -> None:
    sc = _scorecard()
    ctx = ReplanContext(trigger="exhausted", profile=_profile(), scorecard=sc)
    rm = _fallback_roadmap(ctx, app_id="test-app")
    # S2 should be blocked_by S1, etc.
    for i, s in enumerate(rm.slices[1:], start=2):
        assert f"S{i-1}" in s.blocked_by


# ---------------------------------------------------------------------------
# replan()
# ---------------------------------------------------------------------------


def test_replan_with_no_llm_writes_fallback_roadmap(ws: AppWorkspace, repo: Path) -> None:
    rm = replan(ws, repo, trigger="initial", use_llm=False)
    assert rm.app_id == "test-app"
    assert rm.generated_by == "replanner"
    assert len(rm.slices) >= 1
    on_disk = read_roadmap(ws)
    assert on_disk is not None
    assert len(on_disk.slices) == len(rm.slices)


def test_replan_rejects_unknown_trigger(ws: AppWorkspace, repo: Path) -> None:
    with pytest.raises(ValueError):
        replan(ws, repo, trigger="bogus", use_llm=False)


def test_replan_consumes_admiral_notes_on_success(ws: AppWorkspace, repo: Path) -> None:
    write_admiral_note(ws, AdmiralNote(
        note_id="note-test-consume", app_id="test-app",
        received_at="2026-05-01T00:00:00Z",
        body="please replan: consolidate billing modules",
    ))
    note_path = ws.admiral_notes_dir / "note-test-consume.json"
    consumed_path = ws.admiral_notes_consumed_dir / "note-test-consume.json"
    assert note_path.exists()
    assert not consumed_path.exists()

    replan(ws, repo, trigger="admiral_note", use_llm=False)

    assert not note_path.exists(), "note should be moved out of queue after replan"
    assert consumed_path.exists(), "note should land in admiral_notes/consumed/"


def test_replan_uses_llm_when_available(ws: AppWorkspace, repo: Path, monkeypatch) -> None:
    """When the LLM call succeeds, the replanner should consume its slices."""
    fake_payload = {
        "objective_summary": "Stub roadmap",
        "slices": [
            {"slice_id": "S1", "objective": "Do thing one", "phase": "alpha"},
            {"slice_id": "S2", "objective": "Do thing two", "blocked_by": ["S1"]},
        ],
    }
    from chad_captain import replanner as r

    monkeypatch.setattr(r, "claude_json", lambda *_a, **_kw: fake_payload)
    rm = replan(ws, repo, trigger="manual")
    assert [s.slice_id for s in rm.slices] == ["S1", "S2"]
    assert rm.slices[1].blocked_by == ["S1"]
    assert rm.objective_summary == "Stub roadmap"


def test_replan_falls_back_when_llm_raises(ws: AppWorkspace, repo: Path, monkeypatch) -> None:
    from chad_captain import replanner as r

    def boom(*_a, **_kw):
        raise r.LLMError("nope")

    monkeypatch.setattr(r, "claude_json", boom)
    rm = replan(ws, repo, trigger="manual")
    # Fallback path still produces a roadmap; objective_summary lives in fallback string.
    assert "Fallback" in rm.objective_summary or "Healthy" in rm.objective_summary


def test_replan_falls_back_on_empty_slices_payload(ws: AppWorkspace, repo: Path, monkeypatch) -> None:
    from chad_captain import replanner as r
    monkeypatch.setattr(r, "claude_json", lambda *_a, **_kw: {"objective_summary": "", "slices": []})
    rm = replan(ws, repo, trigger="manual")
    assert len(rm.slices) >= 1


# ---------------------------------------------------------------------------
# replan_if_needed
# ---------------------------------------------------------------------------


def test_replan_if_needed_runs_on_initial(ws: AppWorkspace, repo: Path) -> None:
    rm = replan_if_needed(ws, repo)
    assert rm is not None
    assert rm.generated_by == "replanner"


def test_replan_if_needed_skips_when_queued_present(ws: AppWorkspace, repo: Path) -> None:
    write_roadmap(ws, Roadmap(
        app_id="test-app",
        slices=[RoadmapSlice(slice_id="s1", objective="a", status="queued")],
    ))
    assert replan_if_needed(ws, repo) is None


# ---------------------------------------------------------------------------
# Triggers constant integrity
# ---------------------------------------------------------------------------


def test_replan_triggers_match_documentation() -> None:
    assert "initial" in REPLAN_TRIGGERS
    assert "exhausted" in REPLAN_TRIGGERS
    assert "admiral_note" in REPLAN_TRIGGERS


def test_rubric_is_stalled_detects_consecutive_zero_deltas() -> None:
    from chad_captain.replanner import _rubric_is_stalled
    decisions = [
        {"ts": "2026-05-01T00:00:00Z", "kind": "validate", "verdict": "soft_accept", "rationale": "low-yield rubric delta +0.00pp"},
        {"ts": "2026-05-01T00:00:00Z", "kind": "validate", "verdict": "soft_accept", "rationale": "low-yield rubric delta +0.00pp"},
        {"ts": "2026-05-01T00:00:00Z", "kind": "validate", "verdict": "soft_accept", "rationale": "rubric delta +0.80pp"},
        {"ts": "2026-05-01T00:00:00Z", "kind": "validate", "verdict": "soft_accept", "rationale": "low-yield rubric delta +0.00pp"},
    ]
    # Last 4 includes one +0.80pp ≥ 0.5pp threshold → not stalled
    assert _rubric_is_stalled(decisions) is False

    # All 4 below 0.5pp → stalled
    decisions[2]["rationale"] = "low-yield rubric delta +0.00pp"
    assert _rubric_is_stalled(decisions) is True


def test_rubric_is_stalled_requires_min_history() -> None:
    from chad_captain.replanner import _rubric_is_stalled
    # Only 3 validates, need 4 → conservatively not stalled
    decisions = [
        {"ts": "2026-05-01T00:00:00Z", "kind": "validate", "verdict": "soft_accept", "rationale": "+0.00pp"},
    ] * 3
    assert _rubric_is_stalled(decisions) is False


def test_rubric_is_stalled_resets_on_real_accept() -> None:
    from chad_captain.replanner import _rubric_is_stalled
    decisions = [
        {"ts": "2026-05-01T00:00:00Z", "kind": "validate", "verdict": "soft_accept", "rationale": "+0.00pp"},
        {"ts": "2026-05-01T00:00:00Z", "kind": "validate", "verdict": "accept", "rationale": "real progress"},
        {"ts": "2026-05-01T00:00:00Z", "kind": "validate", "verdict": "soft_accept", "rationale": "+0.00pp"},
        {"ts": "2026-05-01T00:00:00Z", "kind": "validate", "verdict": "soft_accept", "rationale": "+0.00pp"},
    ]
    # 'accept' breaks the soft_accept streak → not stalled
    assert _rubric_is_stalled(decisions) is False


def test_build_code_inventory_finds_django_apps(tmp_path: Path) -> None:
    """Inventory must list directories with models.py declaring
    `models.Model`. Live failure: replanner was blind to existing
    `apps/billing/` and shipped a parallel top-level `billing/`."""
    from chad_captain.replanner import _build_code_inventory

    repo = tmp_path / "r"
    repo.mkdir()
    (repo / "apps").mkdir()
    (repo / "apps" / "billing").mkdir()
    (repo / "apps" / "billing" / "models.py").write_text(
        "from django.db import models\n\nclass Plan(models.Model):\n    pass\n"
    )
    (repo / "apps" / "tenants").mkdir()
    (repo / "apps" / "tenants" / "models.py").write_text(
        "from django.db import models\n\nclass Tenant(models.Model):\n    pass\n"
    )
    inv = _build_code_inventory(repo)
    assert "apps/billing" in inv["django_apps"]
    assert "apps/tenants" in inv["django_apps"]


def test_build_code_inventory_lists_service_and_view_modules(tmp_path: Path) -> None:
    from chad_captain.replanner import _build_code_inventory

    repo = tmp_path / "r"
    repo.mkdir()
    svc = repo / "apps" / "billing" / "services"
    svc.mkdir(parents=True)
    (svc / "billing_service.py").write_text("def x(): pass\n")
    (repo / "apps" / "billing" / "views.py").write_text("def view(): pass\n")
    inv = _build_code_inventory(repo)
    assert any("billing_service.py" in s for s in inv["service_modules"])
    assert any("billing/views.py" in v for v in inv["view_modules"])


def test_build_code_inventory_skips_vendor_and_hidden(tmp_path: Path) -> None:
    from chad_captain.replanner import _build_code_inventory

    repo = tmp_path / "r"
    repo.mkdir()
    vend = repo / "vendor" / "thirdparty"
    vend.mkdir(parents=True)
    (vend / "models.py").write_text(
        "from django.db import models\n\nclass X(models.Model): pass\n"
    )
    (repo / ".venv").mkdir()
    (repo / ".venv" / "models.py").write_text(
        "from django.db import models\n\nclass X(models.Model): pass\n"
    )
    inv = _build_code_inventory(repo)
    assert all("vendor" not in a for a in inv["django_apps"])
    assert all(".venv" not in a for a in inv["django_apps"])


def test_build_code_inventory_missing_repo_returns_empty(tmp_path: Path) -> None:
    from chad_captain.replanner import _build_code_inventory
    inv = _build_code_inventory(tmp_path / "nope")
    assert inv == {
        "top_level_dirs": [], "django_apps": [],
        "service_modules": [], "view_modules": [],
    }


def test_build_prompt_includes_inventory_when_present() -> None:
    from chad_captain.replanner import _build_prompt, ReplanContext
    from chad_captain.research.synthesize import AppProfile, LocalProfile, WebProfile
    from chad_captain.scorecard import Scorecard, DimensionScore

    profile = AppProfile(
        app_id="x", local=LocalProfile(repo_path="/tmp/x"),
        web=WebProfile(), summary="A SaaS thing.",
    )
    sc = Scorecard(
        repo_path="/tmp/x",
        dimensions=[DimensionScore(name="x", score=0.5)],
        aggregate=0.5,
    )
    ctx = ReplanContext(
        trigger="exhausted", profile=profile, scorecard=sc,
        recent_decisions=[], admiral_notes=[],
        code_inventory={
            "top_level_dirs": [], "django_apps": ["apps/billing", "apps/tenants"],
            "service_modules": ["apps/billing/services/svc.py"],
            "view_modules": ["apps/billing/views.py"],
        },
    )
    prompt = _build_prompt(ctx)
    assert "Existing code inventory" in prompt
    assert "apps/billing" in prompt
    assert "apps/tenants" in prompt
    assert "REUSE" in prompt or "EXTEND" in prompt


def test_build_prompt_adds_stall_warning_when_deltas_tiny() -> None:
    """When trailing deltas are all near zero, the prompt must instruct
    the LLM to pivot to feature work instead of more remediation."""
    from chad_captain.replanner import _build_prompt, ReplanContext
    from chad_captain.research.synthesize import AppProfile, LocalProfile, WebProfile
    from chad_captain.scorecard import Scorecard, DimensionScore

    profile = AppProfile(
        app_id="x",
        local=LocalProfile(repo_path="/tmp/x"),
        web=WebProfile(),
        summary="A SaaS for tracking widgets.",
    )
    sc = Scorecard(
        repo_path="/tmp/x",
        dimensions=[
            DimensionScore(name="file_size_health", score=0.4),
            DimensionScore(name="tests_present", score=1.0),
        ],
        aggregate=0.7,
    )
    decisions = [
        {"ts": "2026-05-01T00:00:00Z", "kind": "validate", "verdict": "soft_accept", "rationale": "low-yield rubric delta +0.00pp"},
    ] * 4
    ctx = ReplanContext(
        trigger="exhausted", profile=profile, scorecard=sc,
        recent_decisions=decisions, admiral_notes=[],
    )
    prompt = _build_prompt(ctx)
    assert "Rubric stall detected" in prompt
    assert "FEATURE work" in prompt
    # Always-on feature-mix instruction
    assert "FEATURE slices" in prompt


# ---------------------------------------------------------------------------
# _clean_title — dashboard-friendly headlines
# ---------------------------------------------------------------------------


def test_clean_title_uses_explicit_title_when_present() -> None:
    out = _clean_title("Add billing entitlements API endpoint", objective_fallback="ignore me")
    assert out == "Add billing entitlements API endpoint"


def test_clean_title_strips_phase_prefix() -> None:
    out = _clean_title("FEATURE: surface AgentDecision history", objective_fallback="x")
    assert out == "surface AgentDecision history"
    out2 = _clean_title("REMEDIATION: Shrink launch ops file", objective_fallback="x")
    assert out2 == "Shrink launch ops file"


def test_clean_title_truncates_overlong() -> None:
    raw = "A" * 200
    out = _clean_title(raw, objective_fallback="x")
    assert len(out) <= 80
    assert out.endswith("…")


def test_clean_title_falls_back_to_first_sentence_of_objective() -> None:
    objective = (
        "Add GET /api/billing/entitlements/ endpoint in apps/billing/api/views.py. "
        "Wire URL. Add tests."
    )
    out = _clean_title(None, objective_fallback=objective)
    # First sentence wins, no module paths fed through
    assert out.startswith("Add GET /api/billing/entitlements/ endpoint")
    assert "Wire URL" not in out


def test_clean_title_falls_back_when_blank_string() -> None:
    out = _clean_title("   ", objective_fallback="Persist agent decision log via Django model")
    assert out.startswith("Persist agent decision log")


# ---------------------------------------------------------------------------
# Phase A: backlog feeds the replan prompt
# ---------------------------------------------------------------------------


def test_replan_prompt_includes_queued_backlog_items() -> None:
    from chad_captain.protocol import FeatureBacklog, FeatureBacklogItem
    from chad_captain.replanner import _build_prompt
    sc = _scorecard()
    ctx = ReplanContext(
        trigger="manual", profile=_profile(), scorecard=sc,
        backlog_queued=[
            {"id": "fb-001", "title": "Cover A/B testing dashboard",
             "rationale": "10/10 indie author tools have it",
             "priority": 0.9, "estimated_slice_count": 3, "source": "research"},
            {"id": "fb-002", "title": "Email automation flow",
             "rationale": "", "priority": 0.7,
             "estimated_slice_count": 2, "source": "admiral"},
        ],
        backlog_shipped=["CSV export endpoint"],
    )
    prompt = _build_prompt(ctx)
    assert "fb-001" in prompt
    assert "Cover A/B testing dashboard" in prompt
    assert "fb-002" in prompt
    assert "PREFER picking" in prompt
    assert "CSV export endpoint" in prompt
    assert "DO NOT propose duplicates" in prompt


def test_replan_picks_up_seeded_backlog(ws: AppWorkspace, repo: Path) -> None:
    """End-to-end: write_feature_backlog → replan(use_llm=False) reads it."""
    from chad_captain.protocol import (
        FeatureBacklog, FeatureBacklogItem, write_feature_backlog,
    )
    write_feature_backlog(ws, FeatureBacklog(
        app_id="test-app",
        items=[
            FeatureBacklogItem(id="fb-001", title="Demo backlog feature", priority=0.8),
        ],
    ))
    # Use fallback path (no LLM) so we exercise the read path even when LLM
    # is stubbed to fail; backlog still loads into context successfully.
    rm = replan(ws, repo, trigger="initial", use_llm=False)
    # Fallback path doesn't consume backlog, but the replan must not crash
    # when a backlog file exists.
    assert rm is not None
    assert len(rm.slices) >= 1


# ---------------------------------------------------------------------------
# Saturation gate — captain pauses when backlog is empty
# ---------------------------------------------------------------------------


def test_replan_if_needed_pauses_when_backlog_empty(
    ws: AppWorkspace, repo: Path
) -> None:
    """When all features are shipped and roadmap is exhausted, the
    captain should pause with backlog_saturated rather than dispatch
    rubric-only filler."""
    from chad_captain.protocol import (
        FeatureBacklog, FeatureBacklogItem,
        CaptainLogEntry, append_captain_log, read_captain_log,
        write_feature_backlog,
    )
    from chad_captain.replanner import replan_if_needed
    # Empty backlog (all shipped, none queued)
    write_feature_backlog(ws, FeatureBacklog(
        app_id="test-app",
        items=[FeatureBacklogItem(
            id="fb-001", title="Already shipped", status="shipped",
            shipped_in="PR#1", priority=0.5,
        )],
    ))
    # Roadmap exhausted (so trigger fires)
    write_roadmap(ws, Roadmap(
        app_id="test-app",
        slices=[RoadmapSlice(slice_id="S1", objective="x", status="done")],
    ))

    result = replan_if_needed(ws, repo)
    assert result is None, "saturated app should NOT replan"
    assert ws.pause_until_path.exists(), "saturation pause file should exist"

    import json as _json
    pause_data = _json.loads(ws.pause_until_path.read_text())
    assert pause_data.get("reason") == "backlog_saturated"

    log = read_captain_log(ws, limit=5)
    assert any(
        e.kind == "escalation_raised"
        and (e.references or {}).get("event") == "backlog_saturated"
        for e in log
    ), "escalation log entry should be written"


def test_admiral_note_overrides_saturation(
    ws: AppWorkspace, repo: Path
) -> None:
    """If Chad sends an admiral note, replan honoring it even if backlog is empty."""
    from chad_captain.protocol import (
        FeatureBacklog, FeatureBacklogItem,
        write_feature_backlog,
    )
    from chad_captain.replanner import replan_if_needed
    write_feature_backlog(ws, FeatureBacklog(app_id="test-app", items=[]))
    write_roadmap(ws, Roadmap(
        app_id="test-app",
        slices=[RoadmapSlice(slice_id="S1", objective="x", status="done")],
    ))
    write_admiral_note(ws, AdmiralNote(
        note_id="n1", app_id="test-app",
        body="Replan: ship a hotfix for X",
    ))
    result = replan_if_needed(ws, repo)
    # admiral steering wins — replan ran (use_llm fallback path),
    # no saturation pause file written
    assert result is not None
    assert not ws.pause_until_path.exists()


def test_saturation_skips_double_log_within_window(
    ws: AppWorkspace, repo: Path
) -> None:
    from chad_captain.protocol import (
        FeatureBacklog, write_feature_backlog,
        read_captain_log,
    )
    from chad_captain.replanner import replan_if_needed
    write_feature_backlog(ws, FeatureBacklog(app_id="test-app", items=[]))
    write_roadmap(ws, Roadmap(
        app_id="test-app",
        slices=[RoadmapSlice(slice_id="S1", objective="x", status="done")],
    ))
    replan_if_needed(ws, repo)
    replan_if_needed(ws, repo)  # should not double-log
    log = read_captain_log(ws, limit=20)
    saturation_logs = [
        e for e in log
        if e.kind == "escalation_raised"
        and (e.references or {}).get("event") == "backlog_saturated"
    ]
    assert len(saturation_logs) == 1


def test_backlog_add_clears_saturation_pause(ws: AppWorkspace) -> None:
    from chad_captain.cli import _clear_saturation_pause
    import json as _json
    ws.pause_until_path.parent.mkdir(parents=True, exist_ok=True)
    ws.pause_until_path.write_text(
        _json.dumps({"until": "2099-01-01T00:00:00+00:00",
                      "reason": "backlog_saturated"})
    )
    cleared = _clear_saturation_pause(ws)
    assert cleared is True
    assert not ws.pause_until_path.exists()


def test_clear_saturation_pause_skips_circuit_breaker_pause(ws: AppWorkspace) -> None:
    from chad_captain.cli import _clear_saturation_pause
    import json as _json
    ws.pause_until_path.parent.mkdir(parents=True, exist_ok=True)
    ws.pause_until_path.write_text(
        _json.dumps({"until": "2099-01-01T00:00:00+00:00",
                      "reason": "circuit_breaker"})
    )
    cleared = _clear_saturation_pause(ws)
    assert cleared is False
    # Circuit-breaker pause must remain
    assert ws.pause_until_path.exists()


# ---------------------------------------------------------------------------
# Admiral note lifecycle visibility
# ---------------------------------------------------------------------------


def test_any_unread_admiral_note_triggers_replan(ws: AppWorkspace) -> None:
    """Natural-language notes (no 'replan' keyword) must still trigger
    admiral_note replan. Previously we silently dropped them."""
    write_roadmap(ws, Roadmap(
        app_id="test-app",
        slices=[RoadmapSlice(slice_id="s1", objective="a", status="queued")],
    ))
    write_admiral_note(ws, AdmiralNote(
        note_id="n1", app_id="test-app",
        body="look at test density and file size health",
    ))
    assert _detect_trigger(ws) == "admiral_note"


def test_replan_emits_note_response_log_per_consumed_note(
    ws: AppWorkspace, repo: Path
) -> None:
    """Each consumed note should write a note_response log entry linking
    note_id to the new roadmap. That's the visibility surface."""
    from chad_captain.protocol import read_captain_log
    write_admiral_note(ws, AdmiralNote(
        note_id="note-test-link", app_id="test-app",
        body="please ship feature X",
    ))
    replan(ws, repo, trigger="admiral_note", use_llm=False)
    log = read_captain_log(ws, limit=20)
    note_responses = [
        e for e in log
        if e.kind == "note_response"
        and (e.references or {}).get("note_id") == "note-test-link"
    ]
    assert len(note_responses) == 1
    assert "consumed by replan" in note_responses[0].rationale


# ---------------------------------------------------------------------------
# PR7 R3#7: replan rate limit + slice-shape sanity
# ---------------------------------------------------------------------------


def test_slice_shape_signature_is_deterministic_and_normalizes_verbs() -> None:
    """Same shape -> same signature; cosmetic verb/article differences
    must collapse so 'add the foo endpoint' == 'foo endpoint'."""
    from chad_captain.replanner import _slice_shape_signature
    a = RoadmapSlice(slice_id="s1", objective="Add the foo endpoint", phase="api")
    b = RoadmapSlice(slice_id="s2", objective="foo endpoint", phase="api")
    c = RoadmapSlice(slice_id="s3", objective="Add the foo endpoint", phase="db")
    assert _slice_shape_signature(a) == _slice_shape_signature(b)
    # Different phase => different signature.
    assert _slice_shape_signature(a) != _slice_shape_signature(c)


def test_check_replan_rate_limit_allows_initial_when_no_history(
    ws: AppWorkspace,
) -> None:
    """No history file => no rate limit triggered."""
    from chad_captain.replanner import _check_replan_rate_limit
    _check_replan_rate_limit(ws)  # no raise


def test_check_replan_rate_limit_raises_when_over_cap(ws: AppWorkspace) -> None:
    """5 entries in last hour => 6th attempt raises ReplanRateLimited."""
    from datetime import datetime, timezone
    from chad_captain.replanner import (
        REPLAN_RATE_LIMIT_PER_HOUR,
        ReplanRateLimited,
        _check_replan_rate_limit,
        _record_replan,
    )
    rm = Roadmap(app_id="test-app",
                 slices=[RoadmapSlice(slice_id="s", objective="x")])
    for _ in range(REPLAN_RATE_LIMIT_PER_HOUR):
        _record_replan(ws, trigger="manual", roadmap=rm)
    with pytest.raises(ReplanRateLimited, match="5 replans"):
        _check_replan_rate_limit(ws, now=datetime.now(timezone.utc))


def test_check_replan_rate_limit_ignores_old_entries(ws: AppWorkspace) -> None:
    """Entries older than 1h are not counted."""
    from datetime import datetime, timedelta, timezone
    from chad_captain.replanner import _check_replan_rate_limit
    from tracked_app_registry.storage import append_jsonl
    old_ts = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    for _ in range(20):
        append_jsonl(ws.replan_history_path,
                     {"ts": old_ts, "trigger": "manual",
                      "slice_count": 1, "shape_signatures": ["abc"]})
    _check_replan_rate_limit(ws)  # no raise — all stale


def test_drained_replan_sanity_passes_when_prior_is_none() -> None:
    """Initial bootstrap: no prior roadmap => no duplicate check fires."""
    from chad_captain.replanner import _drained_replan_sanity
    fresh = Roadmap(app_id="a",
                    slices=[RoadmapSlice(slice_id="s1", objective="foo")])
    _drained_replan_sanity(None, fresh)  # no raise


def test_drained_replan_sanity_raises_when_shapes_match() -> None:
    """Identical shape sets => 100% Jaccard => raises."""
    from chad_captain.replanner import ReplanDuplicate, _drained_replan_sanity
    slc = lambda i: RoadmapSlice(slice_id=f"s{i}", objective=f"add foo {i}", phase="api")
    prior = Roadmap(app_id="a", slices=[slc(1), slc(2), slc(3)])
    fresh = Roadmap(app_id="a", slices=[slc(1), slc(2), slc(3)])
    with pytest.raises(ReplanDuplicate, match="overlap"):
        _drained_replan_sanity(prior, fresh)


def test_drained_replan_sanity_passes_with_partial_overlap() -> None:
    """Below-threshold Jaccard (33%) => no raise."""
    from chad_captain.replanner import _drained_replan_sanity
    slc = lambda obj: RoadmapSlice(slice_id=obj, objective=obj, phase="api")
    prior = Roadmap(app_id="a", slices=[slc("alpha"), slc("beta"), slc("gamma")])
    fresh = Roadmap(app_id="a", slices=[slc("alpha"), slc("delta"), slc("epsilon")])
    _drained_replan_sanity(prior, fresh)  # no raise


def test_replan_records_to_history_jsonl(ws: AppWorkspace, repo: Path) -> None:
    """Successful replan must append exactly one entry to history."""
    from tracked_app_registry.storage import read_jsonl
    replan(ws, repo, trigger="initial", use_llm=False,
           enforce_duplicate_check=False)
    entries = read_jsonl(ws.replan_history_path)
    assert len(entries) == 1
    assert entries[0]["trigger"] == "initial"
    assert "shape_signatures" in entries[0]


def test_replan_force_bypasses_rate_limit(ws: AppWorkspace, repo: Path) -> None:
    """`force=True` skips the rate-limit check even when over cap."""
    from chad_captain.replanner import REPLAN_RATE_LIMIT_PER_HOUR, _record_replan
    rm = Roadmap(app_id="test-app",
                 slices=[RoadmapSlice(slice_id="s", objective="x")])
    for _ in range(REPLAN_RATE_LIMIT_PER_HOUR):
        _record_replan(ws, trigger="manual", roadmap=rm)
    # Without force this would raise; with force it proceeds.
    replan(ws, repo, trigger="manual", use_llm=False, force=True,
           enforce_duplicate_check=False)


def test_replan_initial_trigger_skips_rate_limit(
    ws: AppWorkspace, repo: Path,
) -> None:
    """initial trigger always proceeds (never rate-limited)."""
    from chad_captain.replanner import REPLAN_RATE_LIMIT_PER_HOUR, _record_replan
    rm = Roadmap(app_id="test-app",
                 slices=[RoadmapSlice(slice_id="s", objective="x")])
    for _ in range(REPLAN_RATE_LIMIT_PER_HOUR):
        _record_replan(ws, trigger="exhausted", roadmap=rm)
    replan(ws, repo, trigger="initial", use_llm=False,
           enforce_duplicate_check=False)


def test_replan_records_duplicate_attempt_then_raises(
    ws: AppWorkspace, repo: Path,
) -> None:
    """When the fresh roadmap duplicates the prior, history captures the
    failed iteration with trigger suffixed `:duplicate` AND raises so the
    caller can escalate."""
    from chad_captain.replanner import ReplanDuplicate
    from tracked_app_registry.storage import read_jsonl
    # Seed prior roadmap with the exact slices the deterministic
    # _fallback_roadmap will produce. We achieve this by running once,
    # then running again against the same context.
    replan(ws, repo, trigger="initial", use_llm=False,
           enforce_duplicate_check=False)
    with pytest.raises(ReplanDuplicate):
        replan(ws, repo, trigger="manual", use_llm=False, force=True)
    entries = read_jsonl(ws.replan_history_path)
    triggers = [e["trigger"] for e in entries]
    assert any(":duplicate" in t for t in triggers)
