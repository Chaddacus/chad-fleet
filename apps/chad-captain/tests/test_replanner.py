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
