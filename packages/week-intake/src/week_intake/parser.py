"""Markdown brain-dump → list[WeekItem].

The parser does ONE LLM call per intake. It produces a structured shape
per bullet: title, kind (best guess), confidence, and at most ONE
clarifying question (the highest-leverage one). Everything else is the
chat-driver's job.

Output items always start in ``state="parsed"``. If confidence < 0.65
or the LLM emitted a clarifying question, the caller should flip state
to ``"needs_clarification"``.

Note (cycle 1): the per-item classification call (kind/confidence/target/
next_question) is now shared with ``clarifier`` via
``classification.classify_item``. Intake still does its own LLM call to
SPLIT the brain dump into items (different prompt + schema), but each
item's classification stays in sync with reclassify.
"""

from __future__ import annotations

from typing import Any

from week_intake.classification import ALLOWED_KINDS
from week_intake.llm import LLMError, claude_json
from week_intake.protocol import WeekFolder, next_item_id
from week_intake.types import (
    ClarificationQuestion,
    RouteTarget,
    WeekItem,
    WeekItemKind,
)

PARSE_SCHEMA: dict = {
    "type": "object",
    "required": ["items"],
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["title", "raw_text", "kind", "confidence"],
                "properties": {
                    "title": {"type": "string", "maxLength": 80},
                    "raw_text": {"type": "string"},
                    "kind": {"type": "string", "enum": list(ALLOWED_KINDS)},
                    "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
                    "candidate_app_id": {"type": ["string", "null"]},
                    "first_question": {"type": ["string", "null"]},
                },
            },
        }
    },
}

PARSER_SYSTEM_PROMPT = """You are a parser. Given Chad's weekly brain dump (markdown bullets,
free-form prose, or a mix), split it into discrete WORK ITEMS and classify each.

For every item, output:
  - title: ≤80 chars, captures the action
  - raw_text: the original bullet/prose, verbatim
  - kind: ONE of: unknown, wip, github_repo, greenfield, decision, meeting_prep, research
      * wip          — continuing work on an existing tracked app
      * github_repo  — a specific repo (URL or `org/name`) not yet tracked
      * greenfield   — net-new project with no repo yet
      * decision     — a non-code choice Chad needs to make
      * meeting_prep — prep for a specific meeting/call
      * research     — exploratory, no shippable artifact yet
      * unknown      — genuinely cannot tell from the text
  - confidence: 0.0–1.0, your confidence in the kind classification
  - candidate_app_id: if kind=wip and you can guess the chad-fleet app slug
    (e.g. "spark-of-defiance", "author-toolkit", "chad-agent"), include it; else null
  - first_question: if confidence < 0.7, ask the SINGLE highest-leverage question
    that would resolve direction (e.g. "Is this a fresh repo or continuing
    work on author-toolkit?"). One question only. Else null.

Be terse. Don't invent items the dump doesn't mention. Don't merge separate items.
Don't split one cohesive item into many.""".strip()


def parse_dump(
    text: str,
    *,
    week: str | None = None,
    base=None,
    timeout: int = 120,
) -> list[WeekItem]:
    """Parse a markdown/prose brain dump into a list of `WeekItem`s.

    Items are NOT persisted by this function. The caller decides whether
    to append them to the week folder. ID allocation is best-effort here;
    callers that persist the result should hold ``WeekFolder.lock()``
    around allocate+append to prevent concurrent intake collisions.
    """
    folder = WeekFolder(week=week, base=base)

    payload = claude_json(
        prompt=text,
        schema=PARSE_SCHEMA,
        system=PARSER_SYSTEM_PROMPT,
        timeout=timeout,
    )

    # Use a sentinel rather than `or []` so falsy non-lists ("", 0, False)
    # raise a malformed-shape error instead of being silently coerced.
    _MISSING = object()
    raw_items = payload.get("items", _MISSING)
    if raw_items is _MISSING or raw_items is None:
        raw_items = []
    if not isinstance(raw_items, list):
        raise LLMError(f"parser: 'items' must be a list, got {type(raw_items).__name__}")

    out: list[WeekItem] = []
    starting_n = _starting_n(folder)
    for idx, raw in enumerate(raw_items):
        if not isinstance(raw, dict):
            raise LLMError(f"parser: items[{idx}] must be an object, got {type(raw).__name__}")
        item_id = f"wk-{starting_n + idx:03d}"
        try:
            item = _build_item(item_id=item_id, week=folder.week, raw=raw)
        except (TypeError, ValueError) as e:
            # Malformed field types from a hallucinating LLM (e.g.
            # ``confidence: "high"``). Fail with a clear message rather
            # than crashing with a TypeError two layers down.
            raise LLMError(f"parser: items[{idx}] malformed: {e}") from e
        out.append(item)

    return out


def _starting_n(folder: WeekFolder) -> int:
    seed = next_item_id(folder)  # "wk-NNN"
    return int(seed.split("-", 1)[1])


def _build_item(*, item_id: str, week: str, raw: dict[str, Any]) -> WeekItem:
    title = (raw.get("title") or "").strip() if isinstance(raw.get("title"), str) else ""
    raw_text_raw = raw.get("raw_text")
    raw_text = raw_text_raw.strip() if isinstance(raw_text_raw, str) else ""
    if not raw_text:
        raise LLMError(f"parser produced item with empty raw_text: {raw!r}")
    kind = raw.get("kind") or "unknown"
    if not isinstance(kind, str) or kind not in ALLOWED_KINDS:
        kind = "unknown"
    # Coerce confidence carefully: a string like "high" must raise rather
    # than silently become 0.0. Pydantic's bound check below catches NaN/inf.
    raw_conf = raw.get("confidence")
    if raw_conf is None:
        confidence = 0.0
    elif isinstance(raw_conf, (int, float)) and not isinstance(raw_conf, bool):
        confidence = float(raw_conf)
    else:
        raise ValueError(f"confidence must be a number, got {type(raw_conf).__name__}={raw_conf!r}")

    target = RouteTarget()
    candidate_app = raw.get("candidate_app_id")
    if isinstance(candidate_app, str) and candidate_app.strip():
        target.app_id = candidate_app.strip()

    clarifications: list[ClarificationQuestion] = []
    fq = raw.get("first_question")
    needs_q = isinstance(fq, str) and fq.strip()
    if needs_q:
        clarifications.append(
            ClarificationQuestion(question_id="kind_or_target", prompt=fq.strip())
        )

    state = "parsed" if (confidence >= 0.65 and not needs_q) else "needs_clarification"

    return WeekItem(
        item_id=item_id,
        week=week,
        raw_text=raw_text,
        title=title or raw_text[:80],
        kind=kind,
        state=state,
        confidence=confidence,
        target=target,
        clarifications=clarifications,
    )


__all__ = ["PARSE_SCHEMA", "PARSER_SYSTEM_PROMPT", "parse_dump"]
