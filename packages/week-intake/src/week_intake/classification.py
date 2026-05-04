"""Shared classification: kind/confidence/target inference from raw text + Q&A.

Both ``parser.parse_dump`` (intake) and ``clarifier.reclassify`` (clarify)
call ``classify_item`` here, so kind semantics, confidence handling, and
target population stay in sync. The LLM contract is enforced via Pydantic;
post-validation demotes invalid LLM-proposed slugs/paths to warnings.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, ValidationError

from week_intake.llm import LLMError, claude_json
from week_intake.types import WeekItemKind
from week_intake.validation import (
    ValidationError as InputValidationError,
    validate_repo_path,
    validate_scaffold_target,
    validate_slug,
)

ALLOWED_KINDS: tuple[WeekItemKind, ...] = (
    "unknown",
    "wip",
    "github_repo",
    "greenfield",
    "decision",
    "meeting_prep",
    "research",
)


class ReclassifyOutput(BaseModel):
    """Pydantic-enforced LLM contract for ``classify_item``.

    Used by both intake parsing (Q&A empty) and clarify (Q&A populated).
    Pydantic validates types/enums/bounds; post-validation in
    ``classify_item`` handles slug demotion and target sufficiency.
    """

    kind: Literal[
        "unknown",
        "wip",
        "github_repo",
        "greenfield",
        "decision",
        "meeting_prep",
        "research",
    ]
    confidence: float = Field(ge=0.0, le=1.0)
    candidate_app_id: str | None = None
    greenfield_name: str | None = None
    repo_path_hint: str | None = None
    next_question: str | None = None
    resolution_status: Literal["ready", "ask_next"] = "ask_next"
    rationale: str = Field(default="", max_length=280)


# ---------------------------------------------------------------------------
# Prompt
# ---------------------------------------------------------------------------


CLASSIFY_SYSTEM_PROMPT = """You classify Chad's weekly work items.

For ONE item (its raw_text plus any prior Q&A), output:
  - kind: ONE of unknown, wip, github_repo, greenfield, decision, meeting_prep, research
      * wip          — continuing work on an existing tracked app
      * github_repo  — a specific repo (URL or `org/name`) not yet tracked
      * greenfield   — net-new project with no repo yet
      * decision     — a non-code choice Chad needs to make
      * meeting_prep — prep for a specific meeting/call
      * research     — exploratory, no shippable artifact
      * unknown      — genuinely cannot tell from the text
  - confidence: 0.0–1.0 in the kind classification
  - candidate_app_id: lowercase-slug guess (a-z, 0-9, dash, ≤64 chars), null otherwise
  - greenfield_name: same slug rules; populate ONLY when kind=greenfield and you've inferred a project name
  - repo_path_hint: a local filesystem path the user mentioned (e.g. /Users/.../foo, ~/code/bar);
    NEVER a GitHub URL or `org/repo` shorthand — those go in `rationale`
  - next_question: ONE highest-leverage question to resolve direction, or null if no question is needed.
    Do NOT ask Chad about machine state (whether an app is registered, whether a path exists);
    the calling code checks those itself.
  - resolution_status: 'ready' if confidence >= 0.7 AND target is fully specified for some route mode;
    'ask_next' otherwise
  - rationale: ≤280 chars summarizing why you classified it this way

Be terse. Stay strict on the schema.""".strip()


def _build_prompt(raw_text: str, q_and_a: list[tuple[str, str]]) -> str:
    """Build the user-prompt for classify_item.

    Q&A pairs are appended in chronological order so the LLM sees the full
    clarification history. ``parse_dump`` passes ``q_and_a=[]`` for fresh
    intake.
    """
    parts = [f"raw_text:\n{raw_text}\n"]
    if q_and_a:
        parts.append("Prior clarification Q&A (most recent last):")
        for q, a in q_and_a:
            parts.append(f"  Q: {q}")
            parts.append(f"  A: {a}")
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# classify_item
# ---------------------------------------------------------------------------


def classify_item(
    raw_text: str,
    q_and_a: list[tuple[str, str]] | None = None,
    *,
    timeout: int = 90,
) -> tuple[ReclassifyOutput, list[str]]:
    """Run the LLM classifier, validate output, return (refresh, warnings).

    Returns a sanitized ``ReclassifyOutput`` (slug fields demoted to None
    if invalid) and a list of human-readable warnings about anything the
    LLM tried to set that we couldn't apply.

    Raises ``LLMError`` if the LLM call fails OR returns a payload that
    fails Pydantic validation.
    """
    prompt = _build_prompt(raw_text, q_and_a or [])
    payload = claude_json(
        prompt=prompt,
        schema=ReclassifyOutput.model_json_schema(),
        system=CLASSIFY_SYSTEM_PROMPT,
        timeout=timeout,
    )
    try:
        out = ReclassifyOutput.model_validate(payload)
    except ValidationError as e:
        raise LLMError(f"classify_item output failed validation: {e}") from e

    warnings: list[str] = []

    # Slug post-validation: invalid syntax → demote + warn.
    if out.candidate_app_id is not None:
        try:
            validate_slug(out.candidate_app_id, field="candidate_app_id")
        except InputValidationError as e:
            warnings.append(f"LLM proposed invalid candidate_app_id={out.candidate_app_id!r} ({e}); ignored")
            out.candidate_app_id = None

    if out.greenfield_name is not None:
        try:
            validate_slug(out.greenfield_name, field="greenfield_name")
        except InputValidationError as e:
            warnings.append(f"LLM proposed invalid greenfield_name={out.greenfield_name!r} ({e}); ignored")
            out.greenfield_name = None

    # Repo path hint validation: try the appropriate validator for the kind.
    # If invalid, drop the hint (don't crash; don't promote to target).
    if out.repo_path_hint is not None:
        if out.kind == "greenfield":
            try:
                validate_scaffold_target(out.repo_path_hint)
            except InputValidationError as e:
                warnings.append(
                    f"LLM proposed repo_path_hint={out.repo_path_hint!r} that is not a "
                    f"valid scaffold target ({e}); ignored"
                )
                out.repo_path_hint = None
        else:
            try:
                validate_repo_path(out.repo_path_hint, must_have_git=True)
            except InputValidationError as e:
                warnings.append(
                    f"LLM proposed repo_path_hint={out.repo_path_hint!r} that is not "
                    f"a git worktree ({e}); ignored"
                )
                out.repo_path_hint = None

    return out, warnings


def workspace_exists(app_id: str, fleet_base_path: Path) -> bool:
    """Check whether captain has a workspace dir for this app slug.

    Used to demote LLM-proposed app_ids that point to apps captain
    doesn't actually know about.
    """
    if not app_id:
        return False
    return (fleet_base_path / app_id).exists()


__all__ = [
    "ALLOWED_KINDS",
    "CLASSIFY_SYSTEM_PROMPT",
    "ReclassifyOutput",
    "classify_item",
    "workspace_exists",
]
