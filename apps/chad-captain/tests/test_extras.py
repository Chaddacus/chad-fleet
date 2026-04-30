"""Tests for the app-specific dimension overlays."""

from __future__ import annotations

from pathlib import Path

import pytest

from chad_captain.extras import EXTRAS_FACTORIES, get_extras
from chad_captain.extras.author_toolkit import sentinel_present, typescript_typecheck_clean
from chad_captain.extras.captain_self import captain_test_count_growing
from chad_captain.extras.spark import (
    chapters_word_count_target,
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
    extras = get_extras("spark-of-defiance")
    assert len(extras) == 2
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
