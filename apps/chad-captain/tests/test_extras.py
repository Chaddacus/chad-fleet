"""Tests for the app-specific dimension overlays."""

from __future__ import annotations

from pathlib import Path

import pytest

from chad_captain.extras import EXTRAS_FACTORIES, get_extras
from chad_captain.extras.author_toolkit import sentinel_present, typescript_typecheck_clean
from chad_captain.extras.captain_self import captain_test_count_growing
from chad_captain.extras.spark import (
    bible_intact,
    chapters_word_count_target,
    drafts_word_count_target,
    voice_guide_intact,
)
from chad_captain.scorecard import DimensionScore, score_repo


# ---------------------------------------------------------------------------
# Spark
# ---------------------------------------------------------------------------


def test_voice_guide_intact_finds_in_root(tmp_path: Path) -> None:
    (tmp_path / "VOICE_GUIDE.md").write_text("voice")
    d = voice_guide_intact(tmp_path)
    assert d.score == 1.0


def test_voice_guide_intact_finds_in_publishing(tmp_path: Path) -> None:
    (tmp_path / "publishing").mkdir()
    (tmp_path / "publishing" / "VOICE_GUIDE.md").write_text("voice")
    d = voice_guide_intact(tmp_path)
    assert d.score == 1.0


def test_voice_guide_intact_zero_when_missing(tmp_path: Path) -> None:
    d = voice_guide_intact(tmp_path)
    assert d.score == 0.0


def test_chapters_word_count_target_no_dir(tmp_path: Path) -> None:
    d = chapters_word_count_target(tmp_path)
    assert d.score == 0.5
    assert "no chapters" in d.rationale


def test_chapters_word_count_target_perfect(tmp_path: Path) -> None:
    chapters = tmp_path / "chapters"
    chapters.mkdir()
    (chapters / "ch01.md").write_text(" ".join(["word"] * 3000))
    (chapters / "ch02.md").write_text(" ".join(["word"] * 4000))
    d = chapters_word_count_target(tmp_path)
    assert d.score == 1.0


def test_chapters_word_count_target_some_out_of_band(tmp_path: Path) -> None:
    chapters = tmp_path / "chapters"
    chapters.mkdir()
    (chapters / "ch01.md").write_text(" ".join(["w"] * 3000))   # in band
    (chapters / "ch02.md").write_text(" ".join(["w"] * 200))    # too short
    (chapters / "ch03.md").write_text(" ".join(["w"] * 9000))   # too long
    d = chapters_word_count_target(tmp_path)
    assert d.score == pytest.approx(1 / 3, rel=0.01)
    assert len(d.detail["out_of_band"]) == 2


# Cycle F — drafts/ + bible/ extras


def test_drafts_word_count_target_no_dir(tmp_path: Path) -> None:
    d = drafts_word_count_target(tmp_path)
    assert d.score == 0.5
    assert "no drafts/" in d.rationale


def test_drafts_word_count_target_finds_alternate_paths(tmp_path: Path) -> None:
    (tmp_path / "manuscript" / "drafts").mkdir(parents=True)
    (tmp_path / "manuscript" / "drafts" / "scene01.md").write_text(
        " ".join(["w"] * 1000)
    )
    d = drafts_word_count_target(tmp_path)
    assert d.score == 1.0  # 1000 is in [500, 8000]


def test_drafts_word_count_target_perfect(tmp_path: Path) -> None:
    drafts = tmp_path / "drafts"
    drafts.mkdir()
    (drafts / "d01.md").write_text(" ".join(["w"] * 1000))
    (drafts / "d02.md").write_text(" ".join(["w"] * 4000))
    d = drafts_word_count_target(tmp_path)
    assert d.score == 1.0


def test_drafts_word_count_target_partial(tmp_path: Path) -> None:
    drafts = tmp_path / "drafts"
    drafts.mkdir()
    (drafts / "d01.md").write_text(" ".join(["w"] * 1000))   # in band
    (drafts / "d02.md").write_text(" ".join(["w"] * 100))    # too short
    d = drafts_word_count_target(tmp_path)
    assert d.score == pytest.approx(0.5, rel=0.01)


def test_drafts_word_count_target_empty_dir(tmp_path: Path) -> None:
    (tmp_path / "drafts").mkdir()
    d = drafts_word_count_target(tmp_path)
    assert d.score == 0.5
    assert "empty" in d.rationale


def test_bible_intact_no_dir(tmp_path: Path) -> None:
    d = bible_intact(tmp_path)
    assert d.score == 0.0


def test_bible_intact_dir_present_with_content(tmp_path: Path) -> None:
    (tmp_path / "bible").mkdir()
    (tmp_path / "bible" / "magic_system.md").write_text("ley lines pulse")
    d = bible_intact(tmp_path)
    assert d.score == 1.0


def test_bible_intact_alternate_paths(tmp_path: Path) -> None:
    (tmp_path / "worldbuilding").mkdir()
    (tmp_path / "worldbuilding" / "factions.md").write_text("the Council")
    d = bible_intact(tmp_path)
    assert d.score == 1.0


def test_bible_intact_dir_present_no_md(tmp_path: Path) -> None:
    (tmp_path / "bible").mkdir()
    (tmp_path / "bible" / "notes.txt").write_text("not markdown")
    d = bible_intact(tmp_path)
    assert d.score == 0.5


def test_bible_intact_dir_present_empty_md(tmp_path: Path) -> None:
    (tmp_path / "bible").mkdir()
    (tmp_path / "bible" / "empty.md").write_text("")
    d = bible_intact(tmp_path)
    assert d.score == 0.5


def test_bible_intact_recursive_search(tmp_path: Path) -> None:
    (tmp_path / "bible" / "factions").mkdir(parents=True)
    (tmp_path / "bible" / "factions" / "council.md").write_text("the Council")
    d = bible_intact(tmp_path)
    assert d.score == 1.0


# ---------------------------------------------------------------------------
# Author toolkit
# ---------------------------------------------------------------------------


def test_sentinel_present_in_root(tmp_path: Path) -> None:
    (tmp_path / ".sentinel").write_text("ok")
    d = sentinel_present(tmp_path)
    assert d.score == 1.0


def test_sentinel_present_in_ops(tmp_path: Path) -> None:
    (tmp_path / "ops").mkdir()
    (tmp_path / "ops" / "sentinel.json").write_text("{}")
    d = sentinel_present(tmp_path)
    assert d.score == 1.0


def test_sentinel_missing_zero(tmp_path: Path) -> None:
    d = sentinel_present(tmp_path)
    assert d.score == 0.0


def test_typescript_typecheck_clean_when_no_tsconfig(tmp_path: Path) -> None:
    d = typescript_typecheck_clean(tmp_path)
    # No tsconfig — TS not used here, so dimension is full.
    assert d.score == 1.0


# ---------------------------------------------------------------------------
# Captain self
# ---------------------------------------------------------------------------


def test_captain_self_test_count_returns_full_when_above_target(tmp_path: Path) -> None:
    captain_tests = tmp_path / "apps" / "chad-captain" / "tests"
    captain_tests.mkdir(parents=True)
    # Synthesize 120 def test_x funcs.
    body = "\n".join([f"def test_func_{i}():\n    pass" for i in range(120)])
    (captain_tests / "test_a.py").write_text(body)
    d = captain_test_count_growing(tmp_path)
    assert d.score == 1.0
    assert d.detail["test_count"] == 120


def test_captain_self_low_test_count(tmp_path: Path) -> None:
    captain_tests = tmp_path / "apps" / "chad-captain" / "tests"
    captain_tests.mkdir(parents=True)
    (captain_tests / "test_a.py").write_text("def test_one():\n    pass\n")
    d = captain_test_count_growing(tmp_path)
    # 1 / 100 = 0.01
    assert d.score == pytest.approx(0.01, rel=0.01)


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


def test_get_extras_returns_empty_for_unknown_app() -> None:
    assert get_extras("unknown-app") == []


def test_get_extras_returns_spark_dimensions() -> None:
    # Cycle F + PR2/T1: 5 spark dims — voice_guide, chapters, drafts,
    # bible, chapter_audit_progress.
    extras = get_extras("spark-of-defiance")
    assert len(extras) == 5
    assert all(callable(fn) for fn in extras)


def test_get_extras_returns_author_toolkit_dimensions() -> None:
    extras = get_extras("author-toolkit")
    assert len(extras) == 2


def test_extras_registry_has_known_apps() -> None:
    # PR4/T3: t3-chadacys-marketing pinned to exact app_id (no aliases).
    expected = {
        "spark-of-defiance",
        "spark",
        "author-toolkit",
        "author_toolkit",
        "captain-self",
        "t3-chadacys-marketing",
    }
    assert expected.issubset(EXTRAS_FACTORIES.keys())


def test_get_extras_returns_t3_marketing_dimensions(tmp_path: Path) -> None:
    """Wiring regression: every t3-chadacys-marketing extra must be a callable
    that returns a DimensionScore. Catches the case where the factory is
    registered under the wrong key, the import path is broken, or one of
    the dim functions silently returns the wrong type."""
    extras = get_extras("t3-chadacys-marketing")
    assert len(extras) == 2
    assert all(callable(fn) for fn in extras)
    for fn in extras:
        result = fn(tmp_path)
        assert isinstance(result, DimensionScore)
        assert isinstance(result.name, str) and result.name
        assert 0.0 <= result.score <= 1.0


# ---------------------------------------------------------------------------
# score_repo with extras
# ---------------------------------------------------------------------------


def test_score_repo_includes_extras(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text("# r")

    def my_extra(_repo: Path) -> DimensionScore:
        return DimensionScore(name="custom", score=0.9, rationale="hi")

    sc = score_repo(tmp_path, extras=[my_extra])
    custom = sc.by_name("custom")
    assert custom is not None
    assert custom.score == 0.9


def test_score_repo_handles_extra_raising(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text("# r")

    def boom(_repo: Path) -> DimensionScore:
        raise RuntimeError("kaboom")

    sc = score_repo(tmp_path, extras=[boom])
    boom_dim = sc.by_name("boom")
    assert boom_dim is not None
    assert boom_dim.score == 0.0
    assert "kaboom" in boom_dim.rationale


# ---------------------------------------------------------------------------
# PR2 / T1 — chapter_audit_progress + ChapterGrade artifact
# ---------------------------------------------------------------------------


def test_chapter_grade_round_trip() -> None:
    from chad_captain.extras.spark_grades import (
        ChapterGrade, ChapterGradesFile,
    )
    g = ChapterGrade(
        chapter_id="ch01",
        last_graded_at="2026-05-04T12:00:00Z",
        overall_score=0.7,
        blockers=["pacing"],
        next_action="tighten",
    )
    f = ChapterGradesFile(last_updated="2026-05-04T12:00:00Z", grades=[g])
    raw = f.model_dump_json()
    loaded = ChapterGradesFile.model_validate_json(raw)
    assert loaded.grades[0].chapter_id == "ch01"
    assert loaded.grades[0].overall_score == 0.7
    assert loaded.by_id("ch01").blockers == ["pacing"]
    assert loaded.by_id("missing") is None


def test_chapter_audit_progress_no_grades_file_no_chapters(tmp_path: Path) -> None:
    from chad_captain.extras.spark_grades import chapter_audit_progress
    d = chapter_audit_progress(tmp_path)
    assert d.score == 0.5
    assert "audit not started" in d.rationale


def test_chapter_audit_progress_no_grades_with_chapters(tmp_path: Path) -> None:
    """No grades + chapters present = 0/N covered = 0.0 score."""
    from chad_captain.extras.spark_grades import chapter_audit_progress
    chapters = tmp_path / "chapters"
    chapters.mkdir()
    (chapters / "ch01.md").write_text("content")
    (chapters / "ch02.md").write_text("content")
    d = chapter_audit_progress(tmp_path)
    # No grades file → 0.5 floor (audit not started signal).
    assert d.score == 0.5


def test_chapter_audit_progress_full_coverage(tmp_path: Path) -> None:
    from chad_captain.extras.spark_grades import chapter_audit_progress
    chapters = tmp_path / "chapters"
    chapters.mkdir()
    (chapters / "ch01.md").write_text("c")
    (chapters / "ch02.md").write_text("c")
    bible = tmp_path / "bible"
    bible.mkdir()
    (bible / "chapter_grades.json").write_text(
        '{"last_updated": "2026-05-04T12:00:00Z", "grades": ['
        '{"chapter_id": "ch01", "last_graded_at": "...", "overall_score": 0.8},'
        '{"chapter_id": "ch02", "last_graded_at": "...", "overall_score": 0.9}'
        ']}'
    )
    d = chapter_audit_progress(tmp_path)
    assert d.score == 1.0
    assert "2/2 chapters graded" in d.rationale


def test_chapter_audit_progress_partial(tmp_path: Path) -> None:
    from chad_captain.extras.spark_grades import chapter_audit_progress
    chapters = tmp_path / "chapters"
    chapters.mkdir()
    for i in range(1, 5):
        (chapters / f"ch{i:02d}.md").write_text("c")
    bible = tmp_path / "bible"
    bible.mkdir()
    (bible / "chapter_grades.json").write_text(
        '{"last_updated": "x", "grades": ['
        '{"chapter_id": "ch01", "last_graded_at": "x", "overall_score": 0.8}'
        ']}'
    )
    d = chapter_audit_progress(tmp_path)
    assert d.score == 0.25  # 1 of 4
    assert "1/4 chapters graded" in d.rationale


def test_chapter_audit_progress_drafts_dir_counts(tmp_path: Path) -> None:
    """drafts/ files also count as chapter content per the path candidates."""
    from chad_captain.extras.spark_grades import chapter_audit_progress
    drafts = tmp_path / "drafts"
    drafts.mkdir()
    (drafts / "prologue.md").write_text("c")
    bible = tmp_path / "bible"
    bible.mkdir()
    (bible / "chapter_grades.json").write_text(
        '{"last_updated": "x", "grades": ['
        '{"chapter_id": "prologue", "last_graded_at": "x", "overall_score": 0.6}'
        ']}'
    )
    d = chapter_audit_progress(tmp_path)
    assert d.score == 1.0


def test_chapter_audit_progress_malformed_grades_file(tmp_path: Path) -> None:
    from chad_captain.extras.spark_grades import chapter_audit_progress
    bible = tmp_path / "bible"
    bible.mkdir()
    (bible / "chapter_grades.json").write_text("not-json")
    d = chapter_audit_progress(tmp_path)
    assert d.score == 0.0
    assert "malformed" in d.rationale


def test_find_grades_file_checks_all_candidates(tmp_path: Path) -> None:
    from chad_captain.extras.spark_grades import (
        GRADES_PATH_CANDIDATES, find_grades_file,
    )
    assert find_grades_file(tmp_path) is None
    # Use last candidate (root-level).
    (tmp_path / "chapter_grades.json").write_text("{}")
    found = find_grades_file(tmp_path)
    assert found is not None
    assert found.name == "chapter_grades.json"


def test_spark_default_has_auto_replan_false() -> None:
    """T1/PR2: SPARK_DEFAULT must opt out of auto_replan so the captain
    never auto-mutates manuscript work."""
    from chad_captain.apps_registry import SPARK_DEFAULT
    assert SPARK_DEFAULT.auto_replan is False


# ---------------------------------------------------------------------------
# T3 marketing extras
# ---------------------------------------------------------------------------


def test_t3_voice_guide_present_zero_when_missing(tmp_path: Path) -> None:
    from chad_captain.extras.t3_marketing import voice_guide_present
    score = voice_guide_present(tmp_path)
    assert score.name == "voice_guide_present"
    assert score.score == 0.0


def test_t3_voice_guide_present_half_when_empty(tmp_path: Path) -> None:
    from chad_captain.extras.t3_marketing import voice_guide_present
    bible = tmp_path / "bible"
    bible.mkdir()
    (bible / "AUTHOR_VOICE_GUIDE.md").write_text("")
    score = voice_guide_present(tmp_path)
    assert score.score == 0.5
    assert "empty" in score.rationale


def test_t3_voice_guide_present_full_when_populated(tmp_path: Path) -> None:
    from chad_captain.extras.t3_marketing import voice_guide_present
    bible = tmp_path / "bible"
    bible.mkdir()
    (bible / "AUTHOR_VOICE_GUIDE.md").write_text("voice cadence persona taboos")
    score = voice_guide_present(tmp_path)
    assert score.score == 1.0


def test_t3_voice_guide_present_at_repo_root(tmp_path: Path) -> None:
    from chad_captain.extras.t3_marketing import voice_guide_present
    (tmp_path / "AUTHOR_VOICE_GUIDE.md").write_text("hello world")
    score = voice_guide_present(tmp_path)
    assert score.score == 1.0


def test_t3_posts_queue_depth_no_fixtures(tmp_path: Path) -> None:
    from chad_captain.extras.t3_marketing import posts_queue_depth
    score = posts_queue_depth(tmp_path)
    assert score.name == "posts_queue_depth"
    assert score.score == 0.5
    assert "no post fixtures" in score.rationale


def test_t3_posts_queue_depth_full_when_target_met(tmp_path: Path) -> None:
    from chad_captain.extras.t3_marketing import POSTS_QUEUE_TARGET, posts_queue_depth
    fix_dir = tmp_path / "apps" / "marketing" / "fixtures"
    fix_dir.mkdir(parents=True)
    payload = [
        {"model": "marketing.post", "fields": {"slug": f"p{i}", "published": False}}
        for i in range(POSTS_QUEUE_TARGET + 2)
    ]
    (fix_dir / "marketing_posts_001.json").write_text(__import__("json").dumps(payload))
    score = posts_queue_depth(tmp_path)
    assert score.score == 1.0


def test_t3_posts_queue_depth_partial(tmp_path: Path) -> None:
    import json as _json
    from chad_captain.extras.t3_marketing import POSTS_QUEUE_TARGET, posts_queue_depth
    fix_dir = tmp_path / "apps" / "marketing" / "fixtures"
    fix_dir.mkdir(parents=True)
    payload = [
        {"model": "marketing.post", "fields": {"slug": f"p{i}", "published": False}}
        for i in range(3)
    ] + [
        {"model": "marketing.post", "fields": {"slug": "live", "published": True}},
    ]
    (fix_dir / "marketing_posts_001.json").write_text(_json.dumps(payload))
    score = posts_queue_depth(tmp_path)
    assert score.score == pytest.approx(3 / POSTS_QUEUE_TARGET)
    assert "3/4 posts queued" in score.rationale


def test_t3_posts_queue_depth_status_field(tmp_path: Path) -> None:
    import json as _json
    from chad_captain.extras.t3_marketing import posts_queue_depth
    fix_dir = tmp_path / "apps" / "marketing" / "fixtures"
    fix_dir.mkdir(parents=True)
    payload = [
        {"fields": {"status": "draft"}},
        {"fields": {"status": "queued"}},
        {"fields": {"status": "live"}},
    ]
    (fix_dir / "posts_001.json").write_text(_json.dumps(payload))
    score = posts_queue_depth(tmp_path)
    # 2 draft/queued out of POSTS_QUEUE_TARGET=10 = 0.2
    assert score.score == pytest.approx(0.2)


def test_t3_posts_queue_depth_malformed_listed_in_detail(tmp_path: Path) -> None:
    from chad_captain.extras.t3_marketing import posts_queue_depth
    fix_dir = tmp_path / "apps" / "marketing" / "fixtures"
    fix_dir.mkdir(parents=True)
    (fix_dir / "marketing_posts_bad.json").write_text("not json{{")
    score = posts_queue_depth(tmp_path)
    assert score.score == 0.5
    assert score.detail and any(
        "marketing_posts_bad.json" in p for p in score.detail.get("malformed", [])
    )


# ---------------------------------------------------------------------------
# PR11 R3#8 + R2#2: dynamic extras discovery
# ---------------------------------------------------------------------------


def test_app_id_to_module_slug_normalizes_hyphens() -> None:
    from chad_captain.extras import _app_id_to_module_slug
    assert _app_id_to_module_slug("t3-chadacys-marketing") == "t3_chadacys_marketing"
    assert _app_id_to_module_slug("Already_Underscored") == "already_underscored"
    assert _app_id_to_module_slug("plain") == "plain"


def test_get_extras_static_factory_still_wins() -> None:
    """Static factories take precedence over dynamic discovery."""
    extras = get_extras("spark-of-defiance")
    assert len(extras) > 0


def test_get_extras_unknown_app_returns_empty(monkeypatch) -> None:
    """No static factory + no dynamic module => []."""
    extras = get_extras("totally-nonexistent-app-xyz")
    assert extras == []


def test_get_extras_dynamic_discovery(tmp_path: Path, monkeypatch) -> None:
    """Module installed at chad_captain.extras.<slug> is auto-discovered."""
    import sys
    import types
    from chad_captain.scorecard import DimensionScore

    def my_dim(repo: Path) -> DimensionScore:
        return DimensionScore(
            name="dynamic_dim", score=0.42,
            rationale="from dynamic module",
        )

    # Install fake module under chad_captain.extras namespace.
    mod = types.ModuleType("chad_captain.extras.dynamic_test_app")
    mod.EXTRAS = [my_dim]
    sys.modules["chad_captain.extras.dynamic_test_app"] = mod
    try:
        extras = get_extras("dynamic-test-app")
        assert len(extras) == 1
        score = extras[0](tmp_path)
        assert score.name == "dynamic_dim"
        assert score.score == 0.42
    finally:
        del sys.modules["chad_captain.extras.dynamic_test_app"]


def test_get_extras_dynamic_module_without_extras_returns_empty() -> None:
    """Module exists but doesn't export EXTRAS => []."""
    import sys
    import types
    mod = types.ModuleType("chad_captain.extras.dynamic_no_extras")
    sys.modules["chad_captain.extras.dynamic_no_extras"] = mod
    try:
        assert get_extras("dynamic-no-extras") == []
    finally:
        del sys.modules["chad_captain.extras.dynamic_no_extras"]


def test_get_extras_dynamic_module_with_wrong_extras_type_raises() -> None:
    """EXTRAS must be a list — wrong type is a configuration bug,
    not a silent fallback."""
    import sys
    import types
    mod = types.ModuleType("chad_captain.extras.dynamic_bad_extras")
    mod.EXTRAS = "not a list"
    sys.modules["chad_captain.extras.dynamic_bad_extras"] = mod
    try:
        with pytest.raises(TypeError, match="must be list"):
            get_extras("dynamic-bad-extras")
    finally:
        del sys.modules["chad_captain.extras.dynamic_bad_extras"]


def test_get_extras_propagates_inner_import_error() -> None:
    """If the extras module ITSELF imports something missing, the error
    must propagate — silent swallow would mask scaffold bugs.

    Achieved by stubbing import_module to raise ModuleNotFoundError with
    a different name than the expected outer module."""
    import sys
    import types
    import importlib

    # Create a real module that, when import_module is called, raises
    # ModuleNotFoundError naming a NESTED dependency.
    real_import_module = importlib.import_module

    def fake_import_module(name, package=None):
        if name == "chad_captain.extras.broken_inner":
            err = ModuleNotFoundError("No module named 'totally_missing_dep'")
            err.name = "totally_missing_dep"
            raise err
        return real_import_module(name, package)

    monkeypatch_unused = None  # silence linter
    orig = importlib.import_module
    importlib.import_module = fake_import_module
    try:
        with pytest.raises(ModuleNotFoundError, match="totally_missing_dep"):
            get_extras("broken-inner")
    finally:
        importlib.import_module = orig
