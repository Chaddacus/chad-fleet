"""Clarification: record an answer, reclassify, advance state.

Two-phase optimistic locking:

  Phase 1 — under lock (~10ms):
    Verify state allows clarify. Find the question to answer. Record
    answer + answered_at. Set ``pending_refresh_question_id``. Bump
    ``revision``. Atomic upsert.

  Phase 2 — no lock (5-30s):
    Call ``classify_item`` with the full Q&A history.

  Phase 3 — under lock (~10ms):
    Re-read item. If revision changed since phase 1 → ``ClarifyConflict``.
    Apply refresh: kind/confidence/target, append next_question if any,
    write refresh_warnings, clear pending_refresh_question_id, bump
    revision again. Atomic upsert.

This module owns Q&A and classification refresh. ``router`` owns side
effects (admiral_note, register, scaffold). The boundary is enforced by
keeping ``clarifier`` ignorant of captain HTTP/filesystem.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from week_intake.captain_client import fleet_base as captain_fleet_base
from week_intake.classification import (
    ReclassifyOutput,
    classify_item,
    workspace_exists,
)
from week_intake.protocol import WeekFolder
from week_intake.route_target import validate_route_target
from week_intake.types import ClarificationQuestion, RouteTarget, WeekItem
from week_intake.validation import ValidationError, validate_slug

_QUESTION_ID_RE = re.compile(r"^(q\d{3}|kind_or_target)$")


class ClarifyError(RuntimeError):
    """Clarify failed for a reason the user can act on."""


class ClarifyConflict(ClarifyError):
    """Item revision changed under us between phase 1 and phase 3."""


@dataclass
class ClarifyResult:
    item: WeekItem
    warnings: list[str]
    next_question_id: str | None  # if a new question was appended


# ---------------------------------------------------------------------------
# Public entry points
# ---------------------------------------------------------------------------


def clarify_with_answer(
    folder: WeekFolder,
    *,
    item_id: str,
    answer: str,
    question_id: str | None = None,
) -> ClarifyResult:
    """Standard clarify flow: record answer, reclassify, apply refresh.

    Validates inputs, runs phase 1 under the folder lock, releases for
    the LLM call, then re-acquires for phase 3.
    """
    answer = (answer or "").strip()
    if not answer:
        raise ClarifyError("answer must be non-empty")
    if len(answer) > 8192:
        raise ClarifyError(f"answer too long ({len(answer)} > 8192 bytes)")
    if question_id is not None and not _QUESTION_ID_RE.match(question_id):
        raise ClarifyError(
            f"question_id={question_id!r} must match {_QUESTION_ID_RE.pattern}"
        )

    # ---- Phase 1: record answer atomically --------------------------------
    with folder.lock():
        item = folder.get_item(item_id)
        if item is None:
            raise ClarifyError(f"item {item_id!r} not found in week {folder.week}")
        _check_clarify_allowed(item)

        target_question = _find_question_for_answer(item, question_id)
        target_question.answer = answer
        from datetime import datetime, timezone
        target_question.answered_at = datetime.now(timezone.utc).isoformat()
        rev_seen = item.revision
        item.pending_refresh_question_id = target_question.question_id
        item.revision = rev_seen + 1
        folder.upsert_item(item)
        folder.log_driver(
            f"clarify {item.item_id} answer recorded for {target_question.question_id}"
        )

    # ---- Phase 2: classify_item (no lock, slow) ---------------------------
    return _phase2_and_apply(folder, item_id=item_id, expected_revision=rev_seen + 1)


def clarify_continue(folder: WeekFolder, *, item_id: str) -> ClarifyResult:
    """Resume a clarify whose phase-2 LLM call previously failed.

    Phase 1 here is a consistency check, not a state mutation: we verify
    that ``pending_refresh_question_id`` is set AND points to an answered
    question. Then we run phase 2 + 3 normally.
    """
    with folder.lock():
        item = folder.get_item(item_id)
        if item is None:
            raise ClarifyError(f"item {item_id!r} not found in week {folder.week}")
        if item.pending_refresh_question_id is None:
            raise ClarifyError(
                f"item {item_id!r} has no pending refresh; "
                "nothing to continue. Use `clarify --answer` to start a new round."
            )
        pending_qid = item.pending_refresh_question_id
        q = next((c for c in item.clarifications if c.question_id == pending_qid), None)
        if q is None:
            raise ClarifyError(
                f"pending refresh points to missing question {pending_qid!r}; "
                "data is inconsistent. Edit the JSONL by hand or run "
                "`clarify --answer` to start a new round."
            )
        if q.answer is None:
            raise ClarifyError(
                f"pending refresh points to unanswered question {pending_qid!r}; "
                "use `clarify --answer` to provide the answer."
            )
        rev_seen = item.revision

    return _phase2_and_apply(folder, item_id=item_id, expected_revision=rev_seen)


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _phase2_and_apply(
    folder: WeekFolder,
    *,
    item_id: str,
    expected_revision: int,
) -> ClarifyResult:
    """Run classify_item outside the lock, then apply under lock with conflict detection.

    ``expected_revision`` is what we saw at the end of phase 1 (or, for
    --continue, what the consistency check captured). Phase 3 verifies it
    hasn't changed before writing.
    """
    # We need the item snapshot to drive classify_item. Re-read briefly
    # under lock to capture a consistent view; release before the LLM call.
    with folder.lock():
        snapshot = folder.get_item(item_id)
        if snapshot is None:
            raise ClarifyError(f"item {item_id!r} disappeared before phase 2")
    q_and_a = [
        (c.prompt, c.answer)
        for c in snapshot.clarifications
        if c.answer is not None
    ]

    refresh, llm_warnings = classify_item(snapshot.raw_text, q_and_a)

    # ---- Phase 3: apply refresh -----------------------------------------
    with folder.lock():
        item = folder.get_item(item_id)
        if item is None:
            raise ClarifyError(f"item {item_id!r} disappeared during phase 3")
        if item.revision != expected_revision:
            raise ClarifyConflict(
                f"item {item_id!r} revision changed during clarify "
                f"(expected {expected_revision}, got {item.revision}); "
                "another writer modified it. Re-run `chad-week clarify`."
            )

        warnings = list(llm_warnings)
        next_qid = _apply_refresh(item, refresh, warnings)
        item.pending_refresh_question_id = None
        item.refresh_warnings = warnings  # replace, not append
        item.revision = expected_revision + 1
        folder.upsert_item(item)
        folder.log_driver(
            f"clarify {item.item_id} refresh applied "
            f"(state={item.state}, kind={item.kind}, confidence={item.confidence:.2f})"
        )
        return ClarifyResult(item=item, warnings=warnings, next_question_id=next_qid)


def _check_clarify_allowed(item: WeekItem) -> None:
    """Enforce the state matrix: which states accept a fresh answer."""
    state = item.state
    if state in ("routed", "in_progress", "blocked", "done", "abandoned"):
        raise ClarifyError(
            f"item {item.item_id!r} is in terminal state {state!r}; "
            "clarify is not allowed post-route. v1 does not support post-route edits."
        )
    if state == "ready":
        raise ClarifyError(
            f"item {item.item_id!r} is already ready; "
            "run `chad-week route` to dispatch it. (Re-clarify on a ready item is deferred to v1.1.)"
        )
    if state == "parsed" and not _has_unanswered_question(item):
        raise ClarifyError(
            f"item {item.item_id!r} is parsed with no open question; "
            "run `chad-week route` directly with explicit flags. "
            "(--add-question is deferred to v1.1.)"
        )


def _has_unanswered_question(item: WeekItem) -> bool:
    return any(c.answer is None for c in item.clarifications)


def _find_question_for_answer(
    item: WeekItem, question_id: str | None
) -> ClarificationQuestion:
    if question_id is not None:
        q = next((c for c in item.clarifications if c.question_id == question_id), None)
        if q is None:
            raise ClarifyError(
                f"item {item.item_id!r} has no question {question_id!r}; "
                f"existing: {[c.question_id for c in item.clarifications]}"
            )
        if q.answer is not None:
            raise ClarifyError(
                f"question {question_id!r} on item {item.item_id!r} is already answered. "
                "Re-answering an existing question is deferred to v1.1."
            )
        return q

    # Default: first unanswered question.
    q = next((c for c in item.clarifications if c.answer is None), None)
    if q is None:
        raise ClarifyError(
            f"item {item.item_id!r} has no unanswered questions; "
            "add a --question-id explicitly (deferred to v1.1) or use route directly."
        )
    return q


def _next_question_id(item: WeekItem) -> str:
    """Generate a unique q### id for a new question on this item.

    Legacy ``kind_or_target`` is treated as q000 for ordering purposes.
    """
    max_n = 0
    for c in item.clarifications:
        m = re.match(r"^q(\d{3})$", c.question_id)
        if m:
            n = int(m.group(1))
            if n > max_n:
                max_n = n
    return f"q{max_n + 1:03d}"


def _apply_refresh(
    item: WeekItem,
    refresh: ReclassifyOutput,
    warnings: list[str],
) -> str | None:
    """Mutate ``item`` to reflect the LLM's refresh. Returns the new
    question_id if one was appended, else None.

    Cross-validation: if LLM says ready but workspace doesn't exist or
    target is incomplete, we downgrade to ask_next and (if needed)
    synthesize a question that asks Chad to fix the missing field.
    """
    item.kind = refresh.kind
    item.confidence = refresh.confidence
    item.target.app_id = refresh.candidate_app_id
    item.target.greenfield_name = refresh.greenfield_name
    if refresh.repo_path_hint is not None:
        item.target.repo_path = refresh.repo_path_hint

    # Demote LLM-proposed app_id that points to a missing workspace.
    # We prefer warning over synthesizing "is X registered?" because
    # machine state isn't something Chad can answer in chat.
    if (
        refresh.kind == "wip"
        and item.target.app_id
        and not workspace_exists(item.target.app_id, captain_fleet_base())
    ):
        warnings.append(
            f"LLM proposed candidate_app_id={item.target.app_id!r} but no captain "
            f"workspace exists at {captain_fleet_base() / item.target.app_id}; "
            "use `chad-week route --app <slug> --repo <path>` to register, or correct the slug."
        )
        # Drop the unverified candidate so state computation doesn't claim
        # this is "existing app" when it isn't.
        item.target.app_id = None

    # Compute final state via shared route-target validator.
    check = validate_route_target(item.target)

    next_qid: str | None = None
    if refresh.resolution_status == "ready" and check.ok:
        item.state = "ready"
    else:
        # Downgrade or stay needs_clarification.
        item.state = "needs_clarification"
        synthesize = refresh.next_question
        if synthesize is None and not check.ok and check.missing:
            # No LLM question; synthesize from the first missing field.
            synthesize = _synthesize_missing_field_question(check.missing[0])
        if synthesize is None and refresh.resolution_status == "ready" and not check.ok:
            warnings.append(
                "LLM said resolution_status=ready but target is incomplete: "
                f"{check.reason}; downgrading to needs_clarification"
            )
        if synthesize:
            next_qid = _next_question_id(item)
            item.clarifications.append(
                ClarificationQuestion(
                    question_id=next_qid,
                    prompt=synthesize.strip(),
                )
            )
        else:
            # No new question available — Chad must use route with overrides.
            warnings.append(
                "no further question; route this item manually with explicit "
                "--app/--repo/--greenfield flags. See `chad-week route --help`."
            )
    return next_qid


def _synthesize_missing_field_question(field_name: str) -> str:
    """Map a missing-field name to a Chad-readable question."""
    return {
        "app_id": "What's the app slug? (e.g. 'chad-agent', 'spark-of-defiance')",
        "repo_path": "What's the local repo path?",
        "greenfield_name": "What slug should the new project use?",
        "existing_workspace": (
            "That app slug isn't registered with captain. Should I register it "
            "(provide --repo) or did you mean a different slug?"
        ),
    }.get(field_name, f"What value for {field_name}?")


__all__ = [
    "ClarifyConflict",
    "ClarifyError",
    "ClarifyResult",
    "clarify_continue",
    "clarify_with_answer",
]
