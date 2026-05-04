"""``chad-week`` CLI.

Subcommands:
    intake   — parse stdin/file markdown into WeekItems and append to the week folder.
    list     — print this week's items as JSON or as a compact table.
    route    — write an admiral_note (and register/scaffold if needed) for one item.
    status   — roll up captain state across this week's routed items.

The driver (chad-twin/chad-agent in chat) calls these between turns. Each
command prints either JSON (machine-readable) or a short human-readable
summary; pick with ``--format``.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import re

from week_intake.protocol import WeekFolder, iso_week_for, next_item_id
from week_intake.types import WeekItem

_ITEM_ID_RE = re.compile(r"^wk-\d{3,6}$")
_QID_RE = re.compile(r"^(q\d{3}|kind_or_target)$")
_WEEK_RE = re.compile(r"^\d{4}-W\d{2}$")


def _cmd_intake(args: argparse.Namespace) -> int:
    from week_intake.llm import LLMError
    from week_intake.parser import parse_dump

    text = _read_input(args.from_path)
    if not text.strip():
        print("ERROR: empty input", file=sys.stderr)
        return 1

    week = args.week or iso_week_for()
    folder = WeekFolder(week=week)

    # LLM call is OUTSIDE the lock — can take 90-120s, holding a per-week
    # lock that long would block all other intake/route ops.
    try:
        items = parse_dump(text, week=week)
    except LLMError as e:
        print(f"ERROR parsing dump: {e}", file=sys.stderr)
        return 2

    if not items:
        print("ERROR: parser returned 0 items", file=sys.stderr)
        return 3

    # Lock ONLY for the allocate-IDs → append window. Re-allocate against
    # current state so two concurrent intakes can't both pick wk-001.
    with folder.lock():
        starting_id = next_item_id(folder)
        starting_n = int(starting_id.split("-", 1)[1])
        for idx, it in enumerate(items):
            it.item_id = f"wk-{starting_n + idx:03d}"
        folder.append_items(items)
        folder.log_driver(f"intake parsed {len(items)} items from {args.from_path or '<stdin>'}")

    if args.format == "json":
        print(json.dumps(_items_payload(items), indent=2))
    else:
        _print_table(items)
    return 0


def _cmd_list(args: argparse.Namespace) -> int:
    week = args.week or iso_week_for()
    folder = WeekFolder(week=week)
    items = folder.list_items()
    if args.state:
        items = [it for it in items if it.state == args.state]
    if args.kind:
        items = [it for it in items if it.kind == args.kind]
    if args.format == "json":
        print(json.dumps(_items_payload(items), indent=2))
    else:
        _print_table(items)
    return 0


def _cmd_route(args: argparse.Namespace) -> int:
    from week_intake.route_target import validate_route_target
    from week_intake.router import RouteError, route_item

    if not _ITEM_ID_RE.match(args.item_id):
        print(f"ERROR: invalid item_id {args.item_id!r} (expected wk-NNN)", file=sys.stderr)
        return 7

    week = args.week or iso_week_for()
    folder = WeekFolder(week=week)

    # Hold the per-week lock across read → side-effects → upsert so that
    # two concurrent `route` calls on the same item can't double-file an
    # admiral note. The lock also serializes drivers.log appends.
    with folder.lock():
        item = folder.get_item(args.item_id)
        if item is None:
            print(f"ERROR: item {args.item_id!r} not found in week {week}", file=sys.stderr)
            return 4

        # Refuse to re-route an already-routed item — the user must
        # explicitly create a new item or reset state if they really want
        # to file a second admiral note for the same week-item.
        if item.state == "routed" and item.captain_note_id:
            print(
                f"ERROR: item {args.item_id!r} is already routed "
                f"(note_id={item.captain_note_id}); refusing to double-file",
                file=sys.stderr,
            )
            return 6

        # Route mode resolution: explicit mode-selecting flags WIN. Stored
        # target fields are inherited only when no explicit mode flag is given.
        # This prevents stale target fields from a bad clarify hijacking the
        # mode (e.g. --app foo + stale repo_path → wrong "new_repo" mode).
        if args.greenfield:
            # greenfield mode: --app + --repo + --greenfield required
            app_id = args.app or item.target.app_id
            repo_path = args.repo or item.target.repo_path
            greenfield_name = args.greenfield
        elif args.repo:
            # new_repo mode: ignore stored greenfield_name
            app_id = args.app or item.target.app_id
            repo_path = args.repo
            greenfield_name = None
        elif args.app:
            # existing_app mode: ignore stored repo_path/greenfield_name
            app_id = args.app
            repo_path = None
            greenfield_name = None
        else:
            # No explicit flags: inherit from item.target.
            app_id = item.target.app_id
            repo_path = item.target.repo_path
            greenfield_name = item.target.greenfield_name

        # Pre-flight check using the resolved target. Surface a helpful
        # message if it's not routable rather than raising deeper.
        from week_intake.types import RouteTarget
        check = validate_route_target(
            RouteTarget(
                app_id=app_id,
                repo_path=repo_path,
                greenfield_name=greenfield_name,
            )
        )
        if not check.ok:
            print(
                f"ERROR: cannot route {args.item_id} ({check.reason}); "
                f"missing: {check.missing}. "
                "Either run `chad-week clarify` to advance state, "
                "or pass explicit --app/--repo/--greenfield flags.",
                file=sys.stderr,
            )
            return 8

        try:
            updated = route_item(
                item,
                app_id=app_id,
                repo_path=repo_path,
                greenfield_name=greenfield_name,
                note_body=args.note,
            )
        except RouteError as e:
            print(f"ERROR routing {args.item_id}: {e}", file=sys.stderr)
            return 5

        folder.upsert_item(updated)
        folder.log_driver(
            f"route {updated.item_id} → app={updated.target.app_id} "
            f"note_id={updated.captain_note_id}"
        )

    if args.format == "json":
        print(json.dumps(updated.model_dump(mode="json"), indent=2))
    else:
        print(f"routed {updated.item_id} → {updated.target.app_id} (note: {updated.captain_note_id})")
    return 0


def _cmd_clarify(args: argparse.Namespace) -> int:
    from week_intake.clarifier import (
        ClarifyConflict,
        ClarifyError,
        clarify_continue,
        clarify_with_answer,
    )
    from week_intake.llm import LLMError

    # ---- input validation ----
    if not _ITEM_ID_RE.match(args.item_id):
        print(f"ERROR: invalid item_id {args.item_id!r} (expected wk-NNN)", file=sys.stderr)
        return 7
    if args.question_id is not None and not _QID_RE.match(args.question_id):
        print(
            f"ERROR: invalid question_id {args.question_id!r} (expected q### or kind_or_target)",
            file=sys.stderr,
        )
        return 7
    if args.week is not None and not _WEEK_RE.match(args.week):
        print(f"ERROR: invalid week tag {args.week!r} (expected YYYY-WNN)", file=sys.stderr)
        return 7
    if not args.cont and not args.answer:
        print("ERROR: --answer is required (or use --continue to resume a pending refresh)", file=sys.stderr)
        return 7
    if args.cont and args.answer:
        print("ERROR: --continue and --answer are mutually exclusive", file=sys.stderr)
        return 7

    week = args.week or iso_week_for()
    folder = WeekFolder(week=week)

    try:
        if args.cont:
            result = clarify_continue(folder, item_id=args.item_id)
        else:
            result = clarify_with_answer(
                folder,
                item_id=args.item_id,
                answer=args.answer,
                question_id=args.question_id,
            )
    except ClarifyConflict as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 9
    except ClarifyError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 10
    except LLMError as e:
        print(
            f"ERROR: LLM failure during clarify ({e}); "
            "the answer was persisted. Re-run with `chad-week clarify --continue` to retry.",
            file=sys.stderr,
        )
        return 11

    # Surface warnings to stderr (don't pollute stdout JSON).
    for w in result.warnings:
        print(f"WARN: {w}", file=sys.stderr)

    if args.format == "json":
        print(json.dumps(result.item.model_dump(mode="json"), indent=2))
    else:
        next_q = ""
        if result.next_question_id:
            new_q = next(
                (c for c in result.item.clarifications if c.question_id == result.next_question_id),
                None,
            )
            if new_q:
                next_q = f"\n  next question ({new_q.question_id}): {new_q.prompt}"
        print(
            f"clarify {result.item.item_id} → state={result.item.state} "
            f"kind={result.item.kind} confidence={result.item.confidence:.2f}"
            + next_q
        )
    return 0


def _cmd_status(args: argparse.Namespace) -> int:
    from week_intake.status import rollup

    week = args.week or iso_week_for()
    folder = WeekFolder(week=week)
    items = folder.list_items()
    report = rollup(items)
    if args.format == "json":
        print(json.dumps(report, indent=2))
    else:
        _print_status_table(week, report)
    return 0


def _print_status_table(week: str, report: dict[str, Any]) -> None:
    print(f"week {week}: {report['totals']['items']} items "
          f"({report['totals']['routed']} routed, "
          f"{report['totals']['captain_unreachable']} captain-unreachable)")
    print()
    print("by state:")
    for k, v in sorted(report["by_state"].items()):
        print(f"  {k:24s} {v}")
    print()
    print("by app:")
    for k, v in sorted(report["by_app"].items()):
        print(f"  {k:24s} {v}")
    print()
    if not report["items"]:
        return
    headers = ("ID", "STATE", "APP", "NOTE", "TITLE")
    rows: list[tuple[str, ...]] = [headers]
    for r in report["items"]:
        rows.append(
            (
                r["item_id"],
                r["state"],
                r.get("app_id") or "-",
                r["captain_note_status"],
                (r["title"] or "")[:60],
            )
        )
    widths = [max(len(c) for c in (row[i] for row in rows)) for i in range(len(headers))]
    for r in rows:
        print("  ".join(c.ljust(widths[i]) for i, c in enumerate(r)))


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _read_input(from_path: str | None) -> str:
    if from_path and from_path != "-":
        return Path(from_path).read_text(encoding="utf-8")
    return sys.stdin.read()


def _items_payload(items: list[WeekItem]) -> list[dict[str, Any]]:
    return [it.model_dump(mode="json") for it in items]


def _print_table(items: list[WeekItem]) -> None:
    if not items:
        print("(no items)")
        return
    headers = ("ID", "STATE", "KIND", "CONF", "APP", "TITLE")
    rows: list[tuple[str, ...]] = [headers]
    for it in items:
        rows.append(
            (
                it.item_id,
                it.state,
                it.kind,
                f"{it.confidence:.2f}",
                it.target.app_id or "-",
                it.title[:60],
            )
        )
    widths = [max(len(r[i]) for r in rows) for i in range(len(headers))]
    for r in rows:
        print("  ".join(cell.ljust(widths[i]) for i, cell in enumerate(r)))


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="chad-week", description="Weekly task intake → chad-fleet router.")
    sub = p.add_subparsers(dest="cmd", required=True)

    pi = sub.add_parser("intake", help="parse markdown/prose brain dump into WeekItems")
    pi.add_argument("--week", default=None, help="ISO week tag (default: current week)")
    pi.add_argument(
        "--from", dest="from_path", default=None,
        help="path to input markdown file ('-' or omit = stdin)",
    )
    pi.add_argument("--format", choices=("json", "table"), default="table")
    pi.set_defaults(func=_cmd_intake)

    pl = sub.add_parser("list", help="print this week's items")
    pl.add_argument("--week", default=None)
    pl.add_argument("--state", default=None, help="filter by state")
    pl.add_argument("--kind", default=None, help="filter by kind")
    pl.add_argument("--format", choices=("json", "table"), default="table")
    pl.set_defaults(func=_cmd_list)

    pc = sub.add_parser("clarify", help="record an answer + reclassify an item")
    pc.add_argument("item_id")
    pc.add_argument("--week", default=None, help="ISO week tag (default: current week)")
    pc.add_argument("--question-id", default=None, help="target a specific question (default: first unanswered)")
    pc.add_argument("--answer", default=None, help="Chad's answer text")
    pc.add_argument(
        "--continue", dest="cont", action="store_true",
        help="resume a clarify whose phase-2 LLM call previously failed",
    )
    pc.add_argument("--format", choices=("json", "table"), default="table")
    pc.set_defaults(func=_cmd_clarify)

    pr = sub.add_parser("route", help="route a clarified item into chad-captain")
    pr.add_argument("item_id")
    pr.add_argument("--app", default=None, help="existing app_id to route to")
    pr.add_argument("--repo", default=None, help="local repo path (for new app or scaffold)")
    pr.add_argument("--greenfield", default=None, help="name for a brand-new project")
    pr.add_argument("--note", default=None, help="admiral-note body; falls back to item title+raw_text")
    pr.add_argument("--week", default=None)
    pr.add_argument("--format", choices=("json", "table"), default="table")
    pr.set_defaults(func=_cmd_route)

    ps = sub.add_parser("status", help="roll up captain state across this week's items")
    ps.add_argument("--week", default=None)
    ps.add_argument("--format", choices=("json", "table"), default="table")
    ps.set_defaults(func=_cmd_status)

    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
