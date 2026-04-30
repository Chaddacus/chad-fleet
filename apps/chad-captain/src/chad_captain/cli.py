"""CLI entry point: chad-captain {run,daemon,brief,alerts,actions,status}."""

from __future__ import annotations

import argparse
import json
import sys


def _print_brief(brief_dict: dict) -> None:
    print(f"Headline: {brief_dict.get('headline', '(no headline)')}")
    apps = brief_dict.get("apps_summary", [])
    if apps:
        print(f"\nApps ({len(apps)}):")
        for app in apps:
            stall = f" [{app['stall_severity'].upper()}]" if app.get("stall_severity") else ""
            action = f" -> {app['top_action']}" if app.get("top_action") else ""
            print(f"  {app['app_name']} ({app['state']}){stall}{action}")
    actions = brief_dict.get("next_actions", [])
    if actions:
        print(f"\nTop actions ({len(actions)}):")
        for a in actions:
            print(f"  {a['priority']}. [{a['app_id']}] {a['title']}")


def cmd_run(_args: argparse.Namespace) -> None:
    from chad_captain.config import load_config
    from chad_captain.runner import CaptainRunner

    cfg = load_config()
    runner = CaptainRunner(cfg)
    brief = runner.run_daily()
    _print_brief(json.loads(brief.model_dump_json()))


def cmd_daemon(_args: argparse.Namespace) -> None:
    from chad_captain.config import load_config
    from chad_captain.daemon import run_forever

    cfg = load_config()
    print("Starting chad-captain daemon...")
    run_forever(cfg)


def cmd_brief(args: argparse.Namespace) -> None:
    from chad_captain.config import load_config
    from chad_captain.runner import CaptainRunner, _today_str

    cfg = load_config()
    runner = CaptainRunner(cfg)
    date_str = args.date or _today_str(cfg.schedule_tz)
    path = runner._brief_file(date_str)
    if path.exists():
        try:
            data = json.loads(path.read_text())
            _print_brief(data)
            return
        except Exception as exc:
            print(f"Error reading brief: {exc}", file=sys.stderr)
            sys.exit(1)
    print(f"No brief found for {date_str}.")


def cmd_alerts(_args: argparse.Namespace) -> None:
    from chad_captain.config import load_config
    from chad_captain.runner import CaptainRunner

    cfg = load_config()
    runner = CaptainRunner(cfg)
    alerts = runner.run_alerts_only()
    if not alerts:
        print("No stall alerts.")
        return
    for a in alerts:
        print(f"[{a.severity.upper()}] {a.app_name}: {a.detail}")


def cmd_actions(args: argparse.Namespace) -> None:
    from chad_captain.config import load_config
    from chad_captain.runner import CaptainRunner

    cfg = load_config()
    runner = CaptainRunner(cfg)
    cap = getattr(args, "cap", 7)
    actions = runner.run_actions_only(cap=cap)
    if not actions:
        print("No actions.")
        return
    for a in actions:
        print(f"{a.priority}. [{a.app_id}] {a.title}")
        print(f"   {a.rationale}")


def cmd_status(_args: argparse.Namespace) -> None:
    from datetime import datetime

    from state_aggregator import Aggregator

    from chad_captain.config import load_config
    from chad_captain.runner import CaptainRunner
    from chad_captain.scheduler import next_tick

    cfg = load_config()
    runner = CaptainRunner(cfg)
    last_run = runner._read_last_run_date()
    secs = next_tick(
        datetime.now(),
        hour=cfg.schedule_hour,
        tz=cfg.schedule_tz,
        weekdays_only=cfg.weekdays_only,
    )
    fleet = Aggregator().snapshot()
    status = {
        "last_run_date": last_run,
        "scheduler_next_tick_seconds": round(secs, 1),
        "fleet_summary": fleet.summary,
    }
    print(json.dumps(status, indent=2))


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="chad-captain",
        description="Chad Captain CLI",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("run", help="Run daily brief pipeline now")
    sub.add_parser("daemon", help="Start the asyncio scheduler daemon (blocking)")

    brief_p = sub.add_parser("brief", help="Print saved brief")
    brief_p.add_argument("--date", default=None, metavar="YYYY-MM-DD")

    sub.add_parser("alerts", help="Print current stall alerts")

    actions_p = sub.add_parser("actions", help="Print top next actions")
    actions_p.add_argument("--cap", type=int, default=7, metavar="N")

    sub.add_parser("status", help="Print scheduler and fleet status as JSON")

    args = parser.parse_args(argv)
    dispatch = {
        "run": cmd_run,
        "daemon": cmd_daemon,
        "brief": cmd_brief,
        "alerts": cmd_alerts,
        "actions": cmd_actions,
        "status": cmd_status,
    }
    dispatch[args.command](args)


if __name__ == "__main__":
    main()
