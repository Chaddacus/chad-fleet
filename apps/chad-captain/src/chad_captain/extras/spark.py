"""Spark of Defiance — app-specific dimensions.

Dimensions:
    voice_guide_intact         — VOICE_GUIDE.md is present in the repo
    chapters_word_count_target — chapters/ dir has chapter files within
                                 [target_min, target_max] word count band
"""

from __future__ import annotations

from pathlib import Path

from chad_captain.scorecard import DimensionScore

CHAPTER_WORD_MIN = 1500
CHAPTER_WORD_MAX = 6000
VOICE_GUIDE_NAMES = ("VOICE_GUIDE.md", "voice_guide.md", "VOICE.md")


def voice_guide_intact(repo: Path) -> DimensionScore:
    for name in VOICE_GUIDE_NAMES:
        for candidate in (repo / name, *(repo / sub / name for sub in ("docs", "publishing"))):
            if candidate.exists():
                return DimensionScore(
                    name="voice_guide_intact",
                    score=1.0,
                    rationale=f"voice guide present at {candidate.relative_to(repo)}",
                )
    return DimensionScore(
        name="voice_guide_intact",
        score=0.0,
        rationale="no voice guide found in repo",
    )


def chapters_word_count_target(repo: Path) -> DimensionScore:
    chapters_dir = _find_chapters_dir(repo)
    if chapters_dir is None:
        return DimensionScore(
            name="chapters_word_count_target",
            score=0.5,
            rationale="no chapters/ dir found — manuscript likely not yet drafted",
        )
    chapter_files = sorted(p for p in chapters_dir.glob("*.md") if p.is_file())
    if not chapter_files:
        return DimensionScore(
            name="chapters_word_count_target",
            score=0.5,
            rationale=f"chapters/ dir is empty",
        )

    in_band = 0
    out_of_band: list[dict] = []
    for f in chapter_files:
        words = _word_count(f)
        if CHAPTER_WORD_MIN <= words <= CHAPTER_WORD_MAX:
            in_band += 1
        else:
            out_of_band.append({"file": f.name, "words": words})
    score = in_band / len(chapter_files)
    return DimensionScore(
        name="chapters_word_count_target",
        score=score,
        rationale=f"{in_band}/{len(chapter_files)} chapters in [{CHAPTER_WORD_MIN},{CHAPTER_WORD_MAX}] words",
        detail={"out_of_band": out_of_band[:10]},
    )


def _find_chapters_dir(repo: Path) -> Path | None:
    for candidate in (repo / "chapters", repo / "manuscript" / "chapters",
                      repo / "src" / "chapters"):
        if candidate.is_dir():
            return candidate
    return None


def _word_count(path: Path) -> int:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return 0
    return len(text.split())


__all__ = ["chapters_word_count_target", "voice_guide_intact"]
