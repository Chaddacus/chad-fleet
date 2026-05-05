"""T3 Chadacys marketing — app-specific dimensions.

Dimensions:
    voice_guide_present  — bible/AUTHOR_VOICE_GUIDE.md exists with content
    posts_queue_depth    — number of unpublished Post fixture entries;
                           1.0 when ≥ POSTS_QUEUE_TARGET drafts queued
"""

from __future__ import annotations

import json
from pathlib import Path

from chad_captain.scorecard import DimensionScore

POSTS_QUEUE_TARGET = 10
VOICE_GUIDE_NAMES: tuple[str, ...] = (
    "AUTHOR_VOICE_GUIDE.md",
    "author_voice_guide.md",
)
VOICE_GUIDE_DIRS: tuple[str, ...] = ("bible", "docs", "publishing")
POST_FIXTURE_GLOBS: tuple[str, ...] = (
    "**/fixtures/posts*.json",
    "**/fixtures/marketing_posts*.json",
)


def _word_count(path: Path) -> int:
    try:
        return len(path.read_text(encoding="utf-8", errors="replace").split())
    except OSError:
        return 0


def voice_guide_present(repo: Path) -> DimensionScore:
    """Score the presence + non-emptiness of the AUTHOR_VOICE_GUIDE.md.

    Returns 1.0 when the file exists with non-empty content; 0.5 when
    present but empty; 0.0 when absent. The 0.5 floor signals "scaffold
    is there, content is not" so the captain can dispatch the voice
    research slice (fb-001) without burning a hard reject.
    """
    candidates = [repo / name for name in VOICE_GUIDE_NAMES]
    candidates += [
        repo / sub / name for sub in VOICE_GUIDE_DIRS for name in VOICE_GUIDE_NAMES
    ]
    for candidate in candidates:
        if candidate.is_file():
            words = _word_count(candidate)
            if words == 0:
                return DimensionScore(
                    name="voice_guide_present",
                    score=0.5,
                    rationale=(
                        f"voice guide present at {candidate.relative_to(repo)} "
                        f"but empty"
                    ),
                )
            return DimensionScore(
                name="voice_guide_present",
                score=1.0,
                rationale=(
                    f"voice guide present at {candidate.relative_to(repo)} "
                    f"({words} words)"
                ),
            )
    return DimensionScore(
        name="voice_guide_present",
        score=0.0,
        rationale="no AUTHOR_VOICE_GUIDE.md found in repo",
    )


def _iter_post_fixtures(repo: Path):
    seen: set[Path] = set()
    for pattern in POST_FIXTURE_GLOBS:
        for f in repo.glob(pattern):
            if f.is_file() and f not in seen:
                seen.add(f)
                yield f


def _count_unpublished_posts(repo: Path) -> tuple[int, int, list[str]]:
    """Return (unpublished, total, malformed_files)."""
    unpublished = 0
    total = 0
    malformed: list[str] = []
    for f in _iter_post_fixtures(repo):
        try:
            data = json.loads(f.read_text(encoding="utf-8", errors="replace"))
        except (OSError, json.JSONDecodeError):
            malformed.append(str(f.relative_to(repo)))
            continue
        if not isinstance(data, list):
            malformed.append(str(f.relative_to(repo)))
            continue
        for entry in data:
            if not isinstance(entry, dict):
                continue
            fields = entry.get("fields") if isinstance(entry.get("fields"), dict) else entry
            # Treat as a Post-like entry if it carries a publish flag or status.
            published = fields.get("published")
            status = fields.get("status")
            if published is None and status is None:
                continue
            total += 1
            if published is False or (isinstance(status, str) and status.lower() in {"draft", "queued", "pending"}):
                unpublished += 1
    return unpublished, total, malformed


def posts_queue_depth(repo: Path) -> DimensionScore:
    """Score the depth of the unpublished post queue.

    Reads Django-style fixtures matching ``POST_FIXTURE_GLOBS``, counts
    entries flagged as draft/queued/unpublished, normalizes to
    ``POSTS_QUEUE_TARGET``. 0.5 floor when no fixtures exist (signals
    "captain is bootstrapping; queue is empty by definition" rather than
    a regression).
    """
    fixtures = list(_iter_post_fixtures(repo))
    if not fixtures:
        return DimensionScore(
            name="posts_queue_depth",
            score=0.5,
            rationale="no post fixtures found in repo",
        )
    unpublished, total, malformed = _count_unpublished_posts(repo)
    if total == 0:
        return DimensionScore(
            name="posts_queue_depth",
            score=0.5,
            rationale=(
                f"{len(fixtures)} fixture file(s) present but contain no "
                f"recognizable Post entries"
            ),
            detail={"malformed": malformed[:10]},
        )
    score = min(1.0, unpublished / POSTS_QUEUE_TARGET)
    return DimensionScore(
        name="posts_queue_depth",
        score=score,
        rationale=(
            f"{unpublished}/{total} posts queued (target ≥{POSTS_QUEUE_TARGET})"
        ),
        detail={"malformed": malformed[:10]},
    )


__all__ = [
    "POSTS_QUEUE_TARGET",
    "posts_queue_depth",
    "voice_guide_present",
]
