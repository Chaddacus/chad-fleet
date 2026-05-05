"""Spark of Defiance — chapter audit grading state.

PR2 / Cycle T1: persists per-chapter grade entries that admiral writes
during the v2 publish-prep audit (read each chapter, codex it, write a
grade). The captain only READS this file (in observe_only mode); admiral
is the writer via `chad-captain replan` or direct edit.

File schema (committed to the manuscript repo at one of the candidate
paths so grades travel with the manuscript, not the captain workspace):

    {
      "last_updated": "2026-05-04T12:00:00Z",
      "grades": [
        {
          "chapter_id": "ch01",
          "last_graded_at": "2026-05-04T12:00:00Z",
          "overall_score": 0.7,
          "blockers": ["pacing dip mid-chapter", "weak hook"],
          "next_action": "tighten chapter-end cliffhanger"
        }
      ]
    }
"""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field

from chad_captain.scorecard import DimensionScore

GRADES_PATH_CANDIDATES = (
    "bible/chapter_grades.json",
    "manuscript/chapter_grades.json",
    "chapter_grades.json",
)

CHAPTER_DIR_CANDIDATES = (
    "chapters",
    "drafts",
    "manuscript/chapters",
    "manuscript/drafts",
    "src/chapters",
)


class ChapterGrade(BaseModel):
    chapter_id: str  # e.g. "ch01" or "draft-prologue"
    last_graded_at: str  # ISO timestamp
    overall_score: float = Field(ge=0.0, le=1.0)
    blockers: list[str] = Field(default_factory=list)
    next_action: str = ""


class ChapterGradesFile(BaseModel):
    last_updated: str = ""
    grades: list[ChapterGrade] = Field(default_factory=list)

    def by_id(self, chapter_id: str) -> ChapterGrade | None:
        return next((g for g in self.grades if g.chapter_id == chapter_id), None)


def find_grades_file(repo: Path) -> Path | None:
    for c in GRADES_PATH_CANDIDATES:
        p = repo / c
        if p.exists():
            return p
    return None


def read_chapter_grades(repo: Path) -> ChapterGradesFile | None:
    """Return parsed grades file, or None if missing/corrupt.

    None vs empty file is meaningful: None = audit not started; empty
    = file present but no grades yet (probably mid-bootstrap).
    """
    p = find_grades_file(repo)
    if p is None:
        return None
    try:
        return ChapterGradesFile.model_validate_json(p.read_text())
    except Exception:  # noqa: BLE001 — malformed → treat as None
        return None


def _detected_chapter_files(repo: Path) -> list[Path]:
    """All files that look like chapter content (chapters/ + drafts/ md)."""
    out: list[Path] = []
    for d in CHAPTER_DIR_CANDIDATES:
        p = repo / d
        if p.is_dir():
            out.extend(sorted(f for f in p.glob("*.md") if f.is_file()))
    return out


def chapter_audit_progress(repo: Path) -> DimensionScore:
    """Score = fraction of detected chapter files that have a grade entry.

    Score conventions:
      - 0.5 floor when no grades file present (audit not started; signal
        "this manuscript is in pre-audit state, captain knows about it")
      - 1.0 when every detected chapter has a grade (audit complete)
      - fractional in between (audit in progress)
      - 0.0 only when grades file is malformed (escalation signal)
    """
    grades = read_chapter_grades(repo)
    if grades is None:
        # Distinguish missing from malformed: try the open path manually.
        p = find_grades_file(repo)
        if p is None:
            return DimensionScore(
                name="chapter_audit_progress",
                score=0.5,
                rationale="no chapter_grades.json — audit not started",
            )
        # File exists but parse failed.
        return DimensionScore(
            name="chapter_audit_progress",
            score=0.0,
            rationale=f"chapter_grades.json malformed at {p.relative_to(repo)}",
        )

    chapters = _detected_chapter_files(repo)
    if not chapters:
        # No chapters yet at all — score the file's own self-consistency.
        # If admiral started writing grades for chapters that haven't been
        # drafted yet, that's still progress signal.
        n = len(grades.grades)
        return DimensionScore(
            name="chapter_audit_progress",
            score=0.5 if n == 0 else 1.0,
            rationale=(
                f"no chapter files detected; grades file has {n} entries"
            ),
        )

    graded_ids = {g.chapter_id for g in grades.grades}
    chapter_ids = {f.stem for f in chapters}
    covered = chapter_ids & graded_ids
    score = len(covered) / len(chapter_ids)
    return DimensionScore(
        name="chapter_audit_progress",
        score=score,
        rationale=(
            f"{len(covered)}/{len(chapter_ids)} chapters graded "
            f"({len(grades.grades)} total grades on file)"
        ),
        detail={
            "ungraded": sorted(chapter_ids - graded_ids)[:10],
            "stale_grades": sorted(graded_ids - chapter_ids)[:10],
        },
    )


__all__ = [
    "ChapterGrade",
    "ChapterGradesFile",
    "GRADES_PATH_CANDIDATES",
    "chapter_audit_progress",
    "find_grades_file",
    "read_chapter_grades",
]
