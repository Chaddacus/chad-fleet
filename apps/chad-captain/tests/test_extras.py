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
    expected = {"spark-of-defiance", "spark", "author-toolkit", "author_toolkit", "captain-self"}
    assert expected.issubset(EXTRAS_FACTORIES.keys())


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
