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

        # Cycle-5 guards: refuse to route terminal items, and refuse any
        # item that already has a captain_note_id (covers in_progress,
        # blocked, and legacy ready+note items).
        if item.state in {"done", "abandoned"}:
            print(
                f"ERROR: cannot route {args.item_id!r}: state is {item.state!r}. "
                f"Run `chad-week reopen {args.item_id}` first.",
                file=sys.stderr,
            )
            return 1
        if item.captain_note_id:
            print(
                f"ERROR: item {args.item_id!r} already has captain_note_id="
                f"{item.captain_note_id!r}. Captain dedups deterministic note "
                "ids; refusing to re-route.",
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
                app_mode=args.app_mode,
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


def _cmd_note(args: argparse.Namespace) -> int:
    from week_intake.lifecycle import TransitionError, record_note

    week = args.week or iso_week_for()
    text = args.text
    if text is None or not text.strip():
        print("ERROR: --text is required and must be non-empty", file=sys.stderr)
        return 1
    try:
        item = record_note(week, args.item_id, text)
    except TransitionError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    if args.format == "json":
        print(json.dumps(item.model_dump(mode="json"), indent=2, default=str))
    else:
        latest = item.notes[-1]
        snippet = latest.text if len(latest.text) <= 80 else latest.text[:77] + "..."
        print(f"{item.item_id} ({item.week}): note recorded ({len(item.notes)} total)")
        print(f"  {snippet}")
    return 0


def _cmd_lifecycle(transition: str):
    """Factory: returns a _cmd_* fn for complete/abandon/reopen."""

    def _cmd(args: argparse.Namespace) -> int:
        from week_intake.lifecycle import (
            TransitionError,
            abandon_item,
            complete_item,
            reopen_item,
        )

        week = args.week or iso_week_for()
        try:
            if transition == "complete":
                item = complete_item(week, args.item_id)
            elif transition == "abandon":
                item = abandon_item(week, args.item_id, reason=args.reason)
            else:
                item = reopen_item(week, args.item_id)
        except TransitionError as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 1

        if args.format == "json":
            print(json.dumps(item.model_dump(mode="json"), indent=2, default=str))
        else:
            ev = item.lifecycle_log[-1] if item.lifecycle_log else None
            from_s = ev.from_state if ev else "?"
            to_s = ev.to_state if ev else item.state
            print(f"{item.item_id} ({item.week}): {from_s} → {to_s}")
            if item.refresh_warnings:
                for w in item.refresh_warnings:
                    print(f"  warning: {w}")
        return 0

    return _cmd


def _lookback_arg(value: str) -> int:
    try:
        n = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"--lookback must be int, got {value!r}")
    if n < 0:
        raise argparse.ArgumentTypeError(f"--lookback must be >= 0, got {n}")
    return n


def _cmd_active(args: argparse.Namespace) -> int:
    from week_intake.active import list_active, list_active_enriched

    if args.enrich:
        rows, enrichment = list_active_enriched(
            lookback=args.lookback, state=args.state,
        )
    else:
        rows = list_active(lookback=args.lookback, state=args.state)
        enrichment = {}

    def _enrich_for(row) -> dict:
        return enrichment.get((row.week, row.item.item_id), {})

    if args.format == "json":
        payload: list[dict[str, Any]] = []
        for r in rows:
            entry: dict[str, Any] = {
                "week": r.week,
                "item": r.item.model_dump(mode="json"),
            }
            if args.enrich:
                e = _enrich_for(r)
                entry["captain"] = {
                    "note_status": e.get("captain_note_status"),
                    "last_meaningful_action": e.get("last_meaningful_action"),
                    "last_captain_action": e.get("last_captain_action"),
                    "needs_attention": e.get("needs_attention", False),
                    "attention_reason": e.get("attention_reason"),
                    "slice_in_flight": e.get("slice_in_flight"),
                    "pause_active": e.get("pause_active", False),
                }
            payload.append(entry)
        print(json.dumps(payload, indent=2))
        return 0

    # Table
    if not rows:
        print("(no active items)")
        return 0
    if args.enrich:
        headers = ("ID", "WEEK", "STATE", "KIND", "APP", "NOTE", "ACTION", "ATTN", "TITLE")
    else:
        headers = ("ID", "WEEK", "STATE", "KIND", "APP", "TITLE")
    out_rows: list[tuple[str, ...]] = [headers]
    for r in rows:
        it = r.item
        title = (it.title or it.raw_text or "")[:60]
        if args.enrich:
            e = _enrich_for(r)
            note = e.get("captain_note_status") or "-"
            action = (
                e.get("last_meaningful_action")
                or e.get("last_captain_action")
                or "-"
            )
            attn = _attn_indicator(e)
            out_rows.append(
                (
                    it.item_id, r.week, it.state, it.kind,
                    it.target.app_id or "-",
                    str(note), str(action)[:24], attn, title,
                )
            )
        else:
            out_rows.append(
                (
                    it.item_id, r.week, it.state, it.kind,
                    it.target.app_id or "-", title,
                )
            )
    widths = [max(len(row[i]) for row in out_rows) for i in range(len(headers))]
    for row in out_rows:
        print("  ".join(c.ljust(widths[i]) for i, c in enumerate(row)))
    return 0


def _cmd_brief(args: argparse.Namespace) -> int:
    from dataclasses import asdict

    from week_intake.brief import build_brief, render_markdown

    brief = build_brief(
        week=args.week,
        use_llm=not args.no_llm,
        refresh=args.refresh,
    )
    if args.format == "json":
        payload = {
            "week": brief.week,
            "week_start_utc": brief.week_start_utc,
            "week_end_utc": brief.week_end_utc,
            "totals": brief.totals,
            "apps": [asdict(a) for a in brief.apps],
            "attention_items": [asdict(a) for a in brief.attention_items],
            "narrative": brief.narrative,
            "prompt_version": brief.prompt_version,
            "used_cache": brief.used_cache,
        }
        print(json.dumps(payload, indent=2))
    else:
        print(render_markdown(brief), end="")
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


def _truncate(s: str, n: int) -> str:
    """Truncate to n chars with an ellipsis. n must be ≥ 1."""
    if len(s) <= n:
        return s
    if n <= 1:
        return s[:n]
    return s[: n - 1] + "…"


def _attn_indicator(row: dict[str, Any]) -> str:
    """Compact attention indicator for the ATTN column."""
    reason = row.get("attention_reason")
    if reason == "escalation":
        return "!E"
    if reason == "pause":
        return "!P"
    if reason == "pause_parse_error":
        return "!?"
    return "-"


def _print_status_table(week: str, report: dict[str, Any]) -> None:
    import shutil

    totals = report["totals"]
    parts = [
        f"{totals['items']} items",
        f"{totals['routed']} routed",
        f"{totals['captain_unreachable']} captain-unreachable",
    ]
    needs_attn = totals.get("needs_attention", 0)
    if needs_attn:
        parts.append(f"{needs_attn} need attention")
    print(f"week {week}: " + ", ".join(parts))
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

    cols = shutil.get_terminal_size((120, 24)).columns
    show_slice = cols >= 120
    show_action = cols >= 80

    headers: list[str] = ["ID", "STATE", "APP", "NOTE"]
    if show_slice:
        headers.append("SLICE")
    if show_action:
        headers.append("ACTION")
    headers += ["ATTN", "TITLE"]

    rows: list[tuple[str, ...]] = [tuple(headers)]
    for r in report["items"]:
        slice_label = _truncate(r.get("slice_in_flight") or "-", 24)
        action = (
            r.get("last_meaningful_action")
            or r.get("last_captain_action")
            or "-"
        )
        action_label = _truncate(action, 32)
        attn = _attn_indicator(r)
        title = r.get("title") or ""
        row_data: list[str] = [
            r["item_id"],
            r["state"],
            r.get("app_id") or "-",
            r["captain_note_status"],
        ]
        if show_slice:
            row_data.append(slice_label)
        if show_action:
            row_data.append(action_label)
        row_data.append(attn)
        row_data.append(title)  # truncated below using remaining width
        rows.append(tuple(row_data))

    # Compute column widths from data, then cap title to remaining terminal width.
    base_widths = [
        max(len(row[i]) for row in rows)
        for i in range(len(headers) - 1)  # all cols except TITLE
    ]
    title_idx = len(headers) - 1
    fixed_total = sum(base_widths) + 2 * (len(headers) - 1)  # 2 spaces between cols
    title_max = max(20, cols - fixed_total)
    final_rows: list[list[str]] = []
    for ridx, row in enumerate(rows):
        out_row = list(row)
        if ridx == 0:
            out_row[title_idx] = _truncate(out_row[title_idx], title_max)
        else:
            out_row[title_idx] = _truncate(out_row[title_idx], title_max)
        final_rows.append(out_row)
    widths = base_widths + [title_max]
    for r in final_rows:
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
    pr.add_argument(
        "--app-mode",
        dest="app_mode",
        choices=("autonomous", "observe_only"),
        default="observe_only",
        help=(
            "captain mode at registration. 'observe_only' (default): captain "
            "tracks but does not dispatch. 'autonomous': captain daemon "
            "ticks the app and dispatches from its roadmap. Only takes "
            "effect for new_repo / greenfield routes (existing-app routes "
            "do not re-register)."
        ),
    )
    pr.add_argument("--week", default=None)
    pr.add_argument("--format", choices=("json", "table"), default="table")
    pr.set_defaults(func=_cmd_route)

    ps = sub.add_parser("status", help="roll up captain state across this week's items")
    ps.add_argument("--week", default=None)
    ps.add_argument("--format", choices=("json", "table"), default="table")
    ps.set_defaults(func=_cmd_status)

    pn = sub.add_parser("note", help="record an ad-hoc observation on a WeekItem (any state)")
    pn.add_argument("item_id")
    pn.add_argument("--text", required=True, help="note body (required, non-empty)")
    pn.add_argument("--week", default=None)
    pn.add_argument("--format", choices=("json", "table"), default="table")
    pn.set_defaults(func=_cmd_note)

    for trans in ("complete", "abandon", "reopen"):
        helptext = {
            "complete": "mark a routed/in_progress/blocked item as done",
            "abandon": "mark any non-terminal item as abandoned (does NOT halt captain)",
            "reopen": "undo a complete/abandon — restores the prior state",
        }[trans]
        sp = sub.add_parser(trans, help=helptext)
        sp.add_argument("item_id")
        sp.add_argument("--week", default=None)
        sp.add_argument("--format", choices=("json", "table"), default="table")
        if trans == "abandon":
            sp.add_argument("--reason", default=None,
                            help="optional reason recorded in lifecycle_log")
        else:
            sp.set_defaults(reason=None)
        sp.set_defaults(func=_cmd_lifecycle(trans))

    pa = sub.add_parser(
        "active",
        help="list non-terminal items across the current week and N prior weeks",
    )
    pa.add_argument("--lookback", type=_lookback_arg, default=4,
                    help="number of prior weeks to include (default 4)")
    pa.add_argument("--format", choices=("json", "table"), default="table")
    # Import here so build_parser doesn't pay module-load cost when unused.
    from week_intake.active import ACTIVE_STATES
    pa.add_argument("--state", choices=sorted(ACTIVE_STATES), default=None,
                    help="filter to one active state")
    pa.add_argument("--enrich", action="store_true",
                    help="add live captain status per row (one HTTP per unique app)")
    pa.set_defaults(func=_cmd_active)

    pb = sub.add_parser("brief", help="narrative weekly digest with LLM-generated summary")
    pb.add_argument("--week", default=None)
    pb.add_argument("--format", choices=("json", "markdown"), default="markdown")
    pb.add_argument("--no-llm", dest="no_llm", action="store_true",
                    help="skip LLM call and cache; print deterministic-only brief")
    pb.add_argument("--refresh", action="store_true",
                    help="ignore cached narrative even if hash matches")
    pb.set_defaults(func=_cmd_brief)

    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
