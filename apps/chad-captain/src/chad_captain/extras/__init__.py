"""Per-app dimension overlays for the compliance rubric scorer.

The baseline scorer in ``scorecard.py`` runs the same seven dimensions on
every app. Each app *also* gets a small set of app-specific dimensions
that capture what "good" looks like for *this* app — e.g. Spark of
Defiance cares about chapter word counts and voice-guide presence,
author-toolkit cares about TypeScript typecheck cleanliness.

Registry pattern: each app maps to a list of callables
``(repo: Path) -> DimensionScore``. The captain calls ``get_extras(app_id)``
to fetch them and threads them into ``score_repo(repo, extras=...)``.

Add new apps by importing their module and adding to ``EXTRAS_REGISTRY``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from chad_captain.scorecard import DimensionScore

ExtraDimension = Callable[[Path], DimensionScore]

# Lazy imports keep the registry cheap when only one app is being scored.
def _spark_extras() -> list[ExtraDimension]:
    # Cycle F: drafts/ and bible/ added so the rubric sees actual manuscript
    # work (exploratory drafting + worldbuilding canon), not just the
    # finished-chapter dir.
    # PR2/T1: chapter_audit_progress added so the captain reports per-chapter
    # audit state during the v2 publish prep.
    from chad_captain.extras.spark import (
        bible_intact,
        chapters_word_count_target,
        drafts_word_count_target,
        voice_guide_intact,
    )
    from chad_captain.extras.spark_grades import chapter_audit_progress
    return [
        voice_guide_intact,
        chapters_word_count_target,
        drafts_word_count_target,
        bible_intact,
        chapter_audit_progress,
    ]


def _author_toolkit_extras() -> list[ExtraDimension]:
    from chad_captain.extras.author_toolkit import (
        sentinel_present,
        typescript_typecheck_clean,
    )
    return [sentinel_present, typescript_typecheck_clean]


def _captain_self_extras() -> list[ExtraDimension]:
    from chad_captain.extras.captain_self import captain_test_count_growing
    return [captain_test_count_growing]


def _t3_marketing_extras() -> list[ExtraDimension]:
    # PR4/T3: Chadacys marketing captain — pinned to the EXACT registry
    # app_id "t3-chadacys-marketing" (no aliases, no fuzzy match) so
    # mis-typed registry entries fall back to baseline-only and surface
    # the misconfiguration immediately.
    from chad_captain.extras.t3_marketing import (
        posts_queue_depth,
        voice_guide_present,
    )
    return [voice_guide_present, posts_queue_depth]


EXTRAS_FACTORIES: dict[str, Callable[[], list[ExtraDimension]]] = {
    "spark-of-defiance": _spark_extras,
    "spark": _spark_extras,
    "author-toolkit": _author_toolkit_extras,
    "author_toolkit": _author_toolkit_extras,
    "captain-self": _captain_self_extras,
    "t3-chadacys-marketing": _t3_marketing_extras,
}


def get_extras(app_id: str) -> list[ExtraDimension]:
    """Return registered extras for an app, or [] if none."""
    factory = EXTRAS_FACTORIES.get(app_id)
    if factory is not None:
        return factory()

    # PR11 R3#8 + R2#2 v6 — dynamic discovery for scaffold-installed
    # extras modules. Tries importing chad_captain.extras.<slug> where
    # slug is the app_id with hyphens normalized to underscores. The
    # module must export EXTRAS: list[ExtraDimension].
    import importlib
    slug = _app_id_to_module_slug(app_id)
    expected_module = f"chad_captain.extras.{slug}"
    importlib.invalidate_caches()
    try:
        mod = importlib.import_module(expected_module)
    except ModuleNotFoundError as e:
        # Only swallow the OUTER missing module — nested ImportErrors
        # (the extras module's own broken imports) must propagate so
        # scaffold bugs surface immediately.
        if e.name == expected_module:
            return []
        raise

    extras = getattr(mod, "EXTRAS", None)
    if extras is None:
        return []
    if not isinstance(extras, list):
        raise TypeError(
            f"{expected_module}.EXTRAS must be list[ExtraDimension], "
            f"got {type(extras).__name__}"
        )
    return extras


def _app_id_to_module_slug(app_id: str) -> str:
    """Normalize an app_id like 't3-chadacys-marketing' to a Python
    module slug 't3_chadacys_marketing'. Used by the dynamic extras
    discovery path so scaffold-installed extras packages can be
    found by app_id.
    """
    return app_id.replace("-", "_").lower()


__all__ = [
    "ExtraDimension",
    "EXTRAS_FACTORIES",
    "get_extras",
    "_app_id_to_module_slug",
]
