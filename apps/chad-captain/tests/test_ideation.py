"""Tests for Phase B feature ideation."""

from __future__ import annotations

from pathlib import Path

import pytest

from chad_captain.protocol import (
    AppWorkspace,
    FeatureBacklog,
    FeatureBacklogItem,
    read_feature_backlog,
    write_feature_backlog,
)
from chad_captain.research.synthesize import AppProfile
from chad_captain.research.local import LocalProfile
from chad_captain.research.web import WebProfile
from chad_captain.research.ideation import (
    _build_ideation_prompt,
    _title_similarity,
    ideate_features,
    merge_candidates_into_backlog,
)


@pytest.fixture()
def ws(tmp_path: Path) -> AppWorkspace:
    w = AppWorkspace("ideate-test", base=tmp_path)
    w.ensure()
    return w


def _profile() -> AppProfile:
    return AppProfile(
        app_id="ideate-test",
        local=LocalProfile(repo_path="/tmp/r", name="r",
                            languages={"python": 1000, "typescript": 500}),
        web=WebProfile(status="ok", landscape_md=(
            "## Comparable products\n"
            "- BookFunnel — handles ARC distribution and reader feedback\n"
            "- ConvertKit — email automation for creators\n"
        )),
        summary="Tooling for indie authors to manage launches.",
    )


# ---------------------------------------------------------------------------
# Prompt construction
# ---------------------------------------------------------------------------


def test_prompt_includes_summary_and_landscape() -> None:
    prompt = _build_ideation_prompt(
        _profile(),
        queued_titles=[],
        shipped_titles=[],
    )
    assert "indie authors" in prompt
    assert "BookFunnel" in prompt
    assert "ConvertKit" in prompt


def test_prompt_lists_already_in_backlog() -> None:
    prompt = _build_ideation_prompt(
        _profile(),
        queued_titles=["Cover A/B testing"],
        shipped_titles=["CSV export"],
    )
    assert "Cover A/B testing" in prompt
    assert "CSV export" in prompt
    assert "DO NOT propose duplicates" in prompt


def test_prompt_includes_weak_dim_hints() -> None:
    prompt = _build_ideation_prompt(
        _profile(),
        queued_titles=[],
        shipped_titles=[],
        scorecard_weak_dims=["test_density (0.30): few tests"],
    )
    assert "test_density" in prompt


# ---------------------------------------------------------------------------
# ideate_features — LLM stubbed
# ---------------------------------------------------------------------------


def test_ideate_features_returns_items_when_llm_succeeds(
    ws: AppWorkspace, monkeypatch
) -> None:
    from chad_captain.research import ideation as id_mod
    monkeypatch.setattr(id_mod, "claude_json", lambda *_a, **_kw: {
        "candidates": [
            {"title": "Reader feedback aggregation",
             "rationale": "BookFunnel ships this; ours doesn't",
             "priority": 0.85, "estimated_slice_count": 3,
             "competitive_evidence": ["bookfunnel.com/feedback"]},
            {"title": "Email drip sequences",
             "rationale": "ConvertKit parity",
             "priority": 0.7, "estimated_slice_count": 4},
        ],
        "saturation_note": "",
    })
    items, sat = ideate_features(ws, _profile())
    assert len(items) == 2
    assert items[0].title == "Reader feedback aggregation"
    assert items[0].source == "auto-ideation"
    assert items[0].priority == 0.85
    assert items[0].competitive_evidence == ["bookfunnel.com/feedback"]
    assert sat == ""


def test_ideate_features_handles_llm_failure(
    ws: AppWorkspace, monkeypatch
) -> None:
    from chad_captain.research import ideation as id_mod
    from chad_captain.llm import LLMError
    def boom(*a, **kw):
        raise LLMError("test stub")
    monkeypatch.setattr(id_mod, "claude_json", boom)
    items, sat = ideate_features(ws, _profile())
    assert items == []
    assert sat.startswith("llm_error")


def test_ideate_features_clamps_priority_and_drops_blank(
    ws: AppWorkspace, monkeypatch
) -> None:
    from chad_captain.research import ideation as id_mod
    monkeypatch.setattr(id_mod, "claude_json", lambda *_a, **_kw: {
        "candidates": [
            {"title": "Valid feature", "rationale": "x",
             "priority": 1.5, "estimated_slice_count": 99},
            {"title": "  ", "rationale": "x", "priority": 0.5,
             "estimated_slice_count": 2},
            {"title": "Negative priority", "rationale": "x",
             "priority": -0.4, "estimated_slice_count": 0},
        ],
    })
    items, _sat = ideate_features(ws, _profile())
    titles = [i.title for i in items]
    assert "Valid feature" in titles
    assert "Negative priority" in titles
    # blank dropped
    assert all(i.title.strip() for i in items)
    # clamped
    valid = next(i for i in items if i.title == "Valid feature")
    assert valid.priority == 1.0
    assert valid.estimated_slice_count == 8
    neg = next(i for i in items if i.title == "Negative priority")
    assert neg.priority == 0.0
    assert neg.estimated_slice_count == 1


# ---------------------------------------------------------------------------
# merge_candidates_into_backlog — dedup
# ---------------------------------------------------------------------------


def test_merge_appends_new_items_with_fresh_ids(ws: AppWorkspace) -> None:
    write_feature_backlog(ws, FeatureBacklog(
        app_id="ideate-test",
        items=[FeatureBacklogItem(id="fb-001", title="Existing thing", priority=0.5)],
    ))
    cands = [
        FeatureBacklogItem(id="fb-?", title="Brand new feature",
                            priority=0.9, source="auto-ideation"),
    ]
    added, skipped = merge_candidates_into_backlog(ws, cands)
    assert (added, skipped) == (1, 0)
    bl = read_feature_backlog(ws)
    assert len(bl.items) == 2
    new_item = bl.by_id("fb-002")
    assert new_item is not None
    assert new_item.title == "Brand new feature"


def test_merge_skips_duplicates_by_token_overlap(ws: AppWorkspace) -> None:
    write_feature_backlog(ws, FeatureBacklog(
        app_id="ideate-test",
        items=[FeatureBacklogItem(id="fb-001",
                                    title="Cover image variation testing dashboard",
                                    priority=0.85)],
    ))
    cands = [
        FeatureBacklogItem(id="fb-?", title="Cover image variation testing UI",
                            priority=0.8, source="auto-ideation"),
        FeatureBacklogItem(id="fb-?", title="Newsletter signup landing page",
                            priority=0.6, source="auto-ideation"),
    ]
    added, skipped = merge_candidates_into_backlog(ws, cands)
    assert added == 1
    assert skipped == 1
    bl = read_feature_backlog(ws)
    titles = [i.title for i in bl.items]
    assert "Newsletter signup landing page" in titles
    # no duplicate of the cover one
    assert sum("Cover image variation" in t for t in titles) == 1


def test_merge_skips_duplicates_against_shipped(ws: AppWorkspace) -> None:
    write_feature_backlog(ws, FeatureBacklog(
        app_id="ideate-test",
        items=[FeatureBacklogItem(id="fb-001", title="ARC reader feedback",
                                    status="shipped", shipped_in="PR#1",
                                    priority=0.9)],
    ))
    cands = [FeatureBacklogItem(id="fb-?",
                                  title="ARC reader feedback aggregation",
                                  priority=0.7, source="auto-ideation")]
    added, skipped = merge_candidates_into_backlog(ws, cands)
    assert (added, skipped) == (0, 1)


def test_merge_returns_zero_zero_for_empty_candidates(ws: AppWorkspace) -> None:
    added, skipped = merge_candidates_into_backlog(ws, [])
    assert (added, skipped) == (0, 0)


# ---------------------------------------------------------------------------
# _title_similarity sanity
# ---------------------------------------------------------------------------


def test_similarity_matches_paraphrase() -> None:
    score = _title_similarity(
        "Cover image variation testing dashboard",
        "Cover image variation testing UI",
    )
    assert score >= 0.6


def test_similarity_low_for_unrelated() -> None:
    score = _title_similarity(
        "Cover image variation testing",
        "Database migration cleanup",
    )
    assert score < 0.2
