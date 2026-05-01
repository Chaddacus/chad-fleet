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


def cmd_tick_all(_args: argparse.Namespace) -> None:
    """Run one autonomous tick across every mode==autonomous app.

    Same code path the daemon's autonomous-tick loop calls every interval.
    Useful for kicking the loop manually (post-deploy, post-config-change)
    or for one-shot diagnostics outside the daemon.
    """
    from chad_captain.daemon import tick_autonomous_apps

    results = tick_autonomous_apps()
    if not results:
        print("No autonomous apps registered.")
        return
    for app_id, status in results.items():
        print(f"[{app_id}] {status}")


def cmd_tick(args: argparse.Namespace) -> None:
    """Run one captain_tick for an app — what launchd invokes daily.

    For observe_only apps we run the *validation* half of the tick (so any
    slice_complete written by an external workflow gets processed) but
    skip dispatching new slices into current_slice.json — the admiral
    drives changes manually for those apps.
    """
    from chad_captain.apps_registry import load_registry
    from chad_captain.protocol import AppWorkspace
    from chad_captain.validator import captain_tick

    reg = load_registry()
    app = reg.by_id(args.app)
    repo = args.repo or (app.repo_path if app else None)
    if not repo:
        print(f"Unknown app {args.app!r} and no --repo provided", file=sys.stderr)
        sys.exit(2)

    mode = (app.mode if app else "autonomous")
    ws = AppWorkspace(args.app)
    ws.ensure()

    if mode == "observe_only":
        # Validation only: process completion if present, no dispatch and no replan.
        from chad_captain.protocol import read_slice_complete
        if read_slice_complete(ws) is None:
            # Cheapest signal of life: refresh scorecard + ensure roadmap exists.
            from chad_captain.replanner import replan_if_needed
            new_rm = replan_if_needed(ws, repo)
            print(f"[{args.app}] observe_only: " + (
                f"replanned ({len(new_rm.slices)} slices)" if new_rm else "idle"
            ))
            return
        # If a completion exists, run only the validate half — pass auto_replan=False
        # and pre-clear roadmap dispatch by leaving current_slice on file.
        status = captain_tick(ws, repo_path=repo, auto_replan=False)
        print(f"[{args.app}] observe_only: {status or 'idle'}")
        return

    status = captain_tick(ws, repo_path=repo, auto_replan=not args.no_replan)
    print(f"[{args.app}] {status or 'idle'}")


def cmd_register(args: argparse.Namespace) -> None:
    from chad_captain.apps_registry import (
        RegisteredApp,
        load_registry,
        save_registry,
        seed_default_registry,
    )

    if args.seed_defaults:
        reg = seed_default_registry(force=args.force)
        print(f"Seeded {len(reg.apps)} apps:")
        for a in reg.apps:
            print(f"  {a.app_id} → {a.repo_path} ({a.mode}, {a.schedule_hour}:00 {a.schedule_tz})")
        return

    if not args.app or not args.repo:
        print("--app and --repo are required (or use --seed-defaults)", file=sys.stderr)
        sys.exit(2)

    reg = load_registry()
    app = RegisteredApp(
        app_id=args.app,
        name=args.name or args.app,
        repo_path=args.repo,
        mode=args.mode,
        schedule_hour=args.hour,
        schedule_tz=args.tz,
        notes=args.notes,
    )
    reg.upsert(app)
    save_registry(reg)
    print(f"Registered {app.app_id} ({app.mode}); registry has {len(reg.apps)} apps total.")


def cmd_install_plists(args: argparse.Namespace) -> None:
    from pathlib import Path as _Path

    from chad_captain.apps_registry import load_registry
    from chad_captain.launchd import bootstrap_command, render_plist, write_plist

    reg = load_registry()
    if not reg.apps:
        print("No apps registered yet. Run `chad-captain register --seed-defaults` first.")
        sys.exit(1)
    target = _Path(args.target_dir).expanduser() if args.target_dir else None
    for app in reg.apps:
        if args.dry_run:
            print(f"=== {app.app_id} ===")
            print(render_plist(app))
            continue
        path = write_plist(app, target_dir=target)
        print(f"Wrote {path}")
        print("  Bootstrap with:")
        print("    " + " ".join(bootstrap_command(app)))


def cmd_init_workspace(args: argparse.Namespace) -> None:
    """Scaffold the per-app workspace and (optionally) bootstrap a roadmap."""
    from chad_captain.apps_registry import load_registry
    from chad_captain.protocol import AppWorkspace

    reg = load_registry()
    apps_to_init = [reg.by_id(args.app)] if args.app else reg.apps
    apps_to_init = [a for a in apps_to_init if a is not None]
    if not apps_to_init:
        print("No matching app in registry.", file=sys.stderr)
        sys.exit(1)
    for app in apps_to_init:
        ws = AppWorkspace(app.app_id)
        ws.ensure()
        print(f"Initialized workspace for {app.app_id} at {ws.root}")
        if args.replan:
            from chad_captain.replanner import replan
            roadmap = replan(ws, app.repo_path, trigger="initial",
                              use_llm=not args.no_llm)
            print(f"  Wrote initial roadmap: {len(roadmap.slices)} slices")


def cmd_replan(args: argparse.Namespace) -> None:
    from chad_captain.protocol import AppWorkspace
    from chad_captain.replanner import replan

    ws = AppWorkspace(args.app)
    roadmap = replan(
        ws,
        args.repo,
        trigger=args.trigger,
        refresh_research=args.refresh_research,
        use_llm=not args.no_llm,
    )
    print(f"App: {roadmap.app_id}")
    print(f"Generated by: {roadmap.generated_by} at {roadmap.generated_at}")
    print(f"Objective: {roadmap.objective_summary}")
    print(f"Slices ({len(roadmap.slices)}):")
    for s in roadmap.slices:
        deps = f" (after {','.join(s.blocked_by)})" if s.blocked_by else ""
        print(f"  {s.slice_id}{deps} [{s.status}] — {s.objective}")


def cmd_research(args: argparse.Namespace) -> None:
    from chad_captain.protocol import AppWorkspace
    from chad_captain.research import load_profile, synthesize_profile

    ws = AppWorkspace(args.app)
    if args.show and not args.refresh:
        profile = load_profile(ws)
        if profile is None:
            print(f"No cached research for app {args.app}.")
            sys.exit(1)
    else:
        if not args.repo:
            print("--repo is required when generating research", file=sys.stderr)
            sys.exit(2)
        profile = synthesize_profile(
            ws,
            args.repo,
            refresh=args.refresh,
            do_web=not args.no_web,
        )
    if args.json:
        print(profile.model_dump_json(indent=2))
        return
    print(f"App: {profile.app_id}")
    print(f"Generated: {profile.generated_at}")
    print(f"Repo: {profile.local.repo_path}")
    print(f"Languages: {', '.join(f'{k}:{v}' for k, v in list(profile.local.languages.items())[:5]) or '(none)'}")
    print(f"Recent commits: {len(profile.local.recent_commits)}")
    print()
    print("Summary:")
    print(profile.summary or "(none)")
    print()
    print(f"Web research: {profile.web.status}")
    if profile.web.status == "ok":
        print(profile.web.landscape_md)
    elif profile.web.reason:
        print(f"  reason: {profile.web.reason}")


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

    tick_p = sub.add_parser("tick", help="Run one captain_tick for an app")
    tick_p.add_argument("--app", required=True, metavar="APP_ID")
    tick_p.add_argument("--repo", default=None,
                         help="Repo path; defaults to registry entry")
    tick_p.add_argument("--no-replan", action="store_true",
                         help="Disable auto-replan when roadmap is exhausted")

    sub.add_parser(
        "tick-all",
        help="Run one autonomous tick for every mode==autonomous app "
             "(same code path the daemon uses on its interval)",
    )

    register_p = sub.add_parser("register", help="Register an app in the captain registry")
    register_p.add_argument("--app", default=None, metavar="APP_ID")
    register_p.add_argument("--repo", default=None, metavar="PATH")
    register_p.add_argument("--name", default=None)
    register_p.add_argument("--mode", default="observe_only",
                             choices=("autonomous", "observe_only"))
    register_p.add_argument("--hour", type=int, default=9)
    register_p.add_argument("--tz", default="America/New_York")
    register_p.add_argument("--notes", default="")
    register_p.add_argument("--seed-defaults", action="store_true",
                             help="Write the Spark + author-toolkit default seeds")
    register_p.add_argument("--force", action="store_true",
                             help="With --seed-defaults: overwrite existing registry")

    install_p = sub.add_parser("install-plists",
                                help="Generate launchd plists for registered apps")
    install_p.add_argument("--target-dir", default=None,
                            help="Override target dir (default: ~/Library/LaunchAgents)")
    install_p.add_argument("--dry-run", action="store_true",
                            help="Print plist contents instead of writing")

    init_p = sub.add_parser("init-workspace",
                             help="Scaffold per-app workspace and optional initial roadmap")
    init_p.add_argument("--app", default=None,
                         help="Specific app to init (defaults to all registered)")
    init_p.add_argument("--replan", action="store_true",
                         help="After init, run the replanner")
    init_p.add_argument("--no-llm", action="store_true")

    replan_p = sub.add_parser("replan", help="Run the replanner and write a fresh roadmap")
    replan_p.add_argument("--app", required=True, metavar="APP_ID")
    replan_p.add_argument("--repo", required=True, metavar="PATH")
    replan_p.add_argument("--trigger", default="manual",
                          choices=("initial", "exhausted", "soft_accept_streak",
                                    "admiral_note", "manual"))
    replan_p.add_argument("--refresh-research", action="store_true")
    replan_p.add_argument("--no-llm", action="store_true",
                          help="Skip the LLM call; use the deterministic fallback")

    research_p = sub.add_parser("research", help="Build or read app research profile")
    research_p.add_argument("--app", required=True, metavar="APP_ID")
    research_p.add_argument("--repo", default=None, metavar="PATH",
                            help="Repo path (required unless --show)")
    research_p.add_argument("--refresh", action="store_true",
                            help="Force rebuild even if cache is fresh")
    research_p.add_argument("--no-web", action="store_true",
                            help="Skip the web research call")
    research_p.add_argument("--show", action="store_true",
                            help="Print existing cached profile (no rebuild)")
    research_p.add_argument("--json", action="store_true",
                            help="Output the full profile as JSON")

    args = parser.parse_args(argv)
    dispatch = {
        "run": cmd_run,
        "daemon": cmd_daemon,
        "brief": cmd_brief,
        "alerts": cmd_alerts,
        "actions": cmd_actions,
        "status": cmd_status,
        "research": cmd_research,
        "replan": cmd_replan,
        "tick": cmd_tick,
        "tick-all": cmd_tick_all,
        "register": cmd_register,
        "install-plists": cmd_install_plists,
        "init-workspace": cmd_init_workspace,
    }
    dispatch[args.command](args)


if __name__ == "__main__":
    main()
