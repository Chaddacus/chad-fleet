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
    import logging as _logging
    from chad_captain.config import load_config
    from chad_captain.daemon import run_forever

    _logging.basicConfig(
        level=_logging.INFO,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )
    cfg = load_config()
    print("Starting chad-captain daemon...", flush=True)
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


def cmd_unpause(args: argparse.Namespace) -> None:
    """Clear a per-app circuit-breaker pause marker.

    Use after resolving the underlying issue that tripped the breaker
    (3+ consecutive reject_hard/revert/escalate). Captain resumes
    autonomous dispatch on the next tick.
    """
    from chad_captain.protocol import AppWorkspace
    from chad_captain.validator import clear_pause

    ws = AppWorkspace(args.app)
    cleared = clear_pause(ws)
    print(f"[{args.app}] {'pause cleared' if cleared else 'no pause to clear'}")


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
            # T1/Cycle D fix: registered apps with auto_replan=False
            # (manuscript captains, T1 Spark publish) MUST NOT auto-replan.
            # The admiral controls every replan via `chad-captain replan`.
            if app and not app.auto_replan:
                print(f"[{args.app}] observe_only: idle (auto_replan=False)")
                return
            # Legacy observe_only with auto_replan default-True: refresh
            # scorecard + ensure roadmap exists.
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
    from chad_captain.launchd import (
        bootstrap_command,
        goose_runner_bootstrap_command,
        render_goose_runner_plist,
        render_plist,
        write_goose_runner_plist,
        write_plist,
    )

    reg = load_registry()
    if not reg.apps:
        print("No apps registered yet. Run `chad-captain register --seed-defaults` first.")
        sys.exit(1)
    target = _Path(args.target_dir).expanduser() if args.target_dir else None
    for app in reg.apps:
        # PR6: skip captains with enabled=false (scaffold staging gate).
        # Their plists shouldn't be installed/bootstrapped until activation
        # completes. Dry-run still prints them so admin can preview.
        if not getattr(app, "enabled", True) and not args.dry_run:
            print(f"Skipping {app.app_id} (enabled=false)")
            continue
        if args.dry_run:
            print(f"=== {app.app_id} (tick) ===")
            print(render_plist(app))
            if app.mode == "autonomous":
                print(f"=== {app.app_id} (goose-runner) ===")
                print(render_goose_runner_plist(app))
            continue
        path = write_plist(app, target_dir=target)
        print(f"Wrote {path}")
        print("  Bootstrap with:")
        print("    " + " ".join(bootstrap_command(app)))
        # PR2 R3-HIGH-1: autonomous apps need a long-running goose-runner
        # alongside the periodic tick. observe_only apps don't dispatch
        # slices so they don't need it.
        if app.mode == "autonomous":
            gr_path = write_goose_runner_plist(app, target_dir=target)
            print(f"Wrote {gr_path}")
            print("  Bootstrap with:")
            print("    " + " ".join(goose_runner_bootstrap_command(app)))


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
    from chad_captain.apps_registry import load_registry
    from chad_captain.protocol import AppWorkspace
    from chad_captain.replanner import replan

    # PR2 fix: resolve repo from registry when --repo not provided. Lets
    # admiral run `chad-captain replan --app <id>` without remembering
    # the repo path for every captain.
    repo = args.repo
    if not repo:
        reg_app = load_registry().by_id(args.app)
        repo = reg_app.repo_path if reg_app else None
    if not repo:
        print(
            f"Unknown app {args.app!r} and no --repo provided", file=sys.stderr,
        )
        sys.exit(2)

    ws = AppWorkspace(args.app)
    roadmap = replan(
        ws,
        repo,
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


def cmd_scorecard(args: argparse.Namespace) -> None:
    """Print one app's fresh scorecard (baseline dims + extras).

    PR2 R3-MED-3 fix: T1 manuscript captain runbook needs a way for the
    admiral to see "how is this app doing" without triggering replan or
    dispatch. `chad-captain tick` is too side-effecty for the daily
    glance; `chad-captain status` is global JSON. This is the missing
    per-app inspection command.
    """
    from chad_captain.apps_registry import load_registry
    from chad_captain.extras import get_extras
    from chad_captain.scorecard import score_repo

    reg_app = load_registry().by_id(args.app)
    repo = args.repo or (reg_app.repo_path if reg_app else None)
    if not repo:
        print(
            f"Unknown app {args.app!r} and no --repo provided", file=sys.stderr,
        )
        sys.exit(2)

    sc = score_repo(repo, extras=get_extras(args.app))
    if args.json:
        print(sc.model_dump_json(indent=2))
        return

    print(f"App: {args.app}")
    print(f"Repo: {repo}")
    print(f"Aggregate: {sc.aggregate:.4f}")
    print()
    print(f"{'Dimension':<36} {'Score':>8}  Rationale")
    print(f"{'-' * 36} {'-' * 8}  {'-' * 40}")
    for d in sc.dimensions:
        print(f"{d.name:<36} {d.score:>8.4f}  {d.rationale[:60]}")


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


def _clear_saturation_pause(ws) -> bool:
    """If the app's pause file is a saturation pause, delete it.
    Returns True iff a saturation pause was cleared.
    """
    if not ws.pause_until_path.exists():
        return False
    try:
        data = json.loads(ws.pause_until_path.read_text())
    except (OSError, json.JSONDecodeError):
        return False
    if data.get("reason") != "backlog_saturated":
        return False
    try:
        ws.pause_until_path.unlink()
    except OSError:
        return False
    return True


def cmd_backlog(args: argparse.Namespace) -> None:
    """Dispatch `chad-captain backlog {add,list,ship,defer}`."""
    from chad_captain.protocol import (
        AppWorkspace,
        FeatureBacklogItem,
        read_feature_backlog,
        write_feature_backlog,
    )

    sub = args.backlog_cmd
    ws = AppWorkspace(args.app)
    backlog = read_feature_backlog(ws)

    if sub == "add":
        if not 0.0 <= args.priority <= 1.0:
            print(f"--priority must be in [0.0, 1.0]; got {args.priority}", file=sys.stderr)
            sys.exit(2)
        item = FeatureBacklogItem(
            id=backlog.next_id(),
            title=args.title,
            rationale=args.rationale,
            priority=args.priority,
            estimated_slice_count=args.slices,
            source=args.source,
            competitive_evidence=list(args.evidence or []),
        )
        backlog.items.append(item)
        write_feature_backlog(ws, backlog)
        cleared = _clear_saturation_pause(ws)
        msg = f"added {item.id}: {item.title} (priority={item.priority})"
        if cleared:
            msg += " — saturation pause cleared"
        print(msg)
        return

    if sub == "list":
        items = backlog.items if args.all else backlog.queued()
        if args.json:
            print(json.dumps([i.model_dump(mode="json") for i in items], indent=2))
            return
        if not items:
            print(f"(no {'' if args.all else 'queued '}items in {args.app} backlog)")
            return
        for it in items:
            tag = it.status if args.all else "queued"
            print(
                f"  [{it.id}] {tag:>9s} priority={it.priority:.2f} "
                f"~{it.estimated_slice_count}sl src={it.source:>10s}  {it.title}"
            )
            if it.rationale:
                print(f"             {it.rationale[:140]}")
            if it.shipped_in:
                print(f"             shipped in {it.shipped_in} at {it.shipped_at}")
        return

    if sub == "ship":
        item = backlog.by_id(args.item_id)
        if item is None:
            print(f"no backlog item {args.item_id} in {args.app}", file=sys.stderr)
            sys.exit(1)
        from datetime import datetime, timezone
        item.status = "shipped"
        item.shipped_in = args.pr
        item.shipped_at = datetime.now(timezone.utc).isoformat()
        write_feature_backlog(ws, backlog)
        print(f"shipped {item.id}: {item.title}")
        return

    if sub == "defer":
        item = backlog.by_id(args.item_id)
        if item is None:
            print(f"no backlog item {args.item_id} in {args.app}", file=sys.stderr)
            sys.exit(1)
        item.status = args.status
        write_feature_backlog(ws, backlog)
        print(f"{args.status}: {item.id} {item.title}")
        return

    raise ValueError(f"unknown backlog subcommand: {sub}")


def cmd_summary(args: argparse.Namespace) -> None:
    """Print a human-readable session summary for an app."""
    from chad_captain.protocol import AppWorkspace
    from chad_captain.summary import build_session_summary

    ws = AppWorkspace(args.app)
    try:
        s = build_session_summary(ws, window=args.since)
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        sys.exit(2)

    if args.json:
        print(json.dumps(s.model_dump(mode="json"), indent=2))
        return

    print(f"# {args.app} — {s.window_label}")
    print()
    print(s.headline)
    print()
    print(s.narrative)
    if s.features_shipped:
        print()
        print("Features shipped:")
        for f in s.features_shipped:
            ref = f" → {f.pr_url}" if f.pr_url else ""
            print(f"  ✓ [{f.id}] {f.title}{ref}")
    if s.prs_merged:
        print()
        print("PRs merged:")
        for pr in s.prs_merged:
            tags = (
                f"  ({', '.join(pr.backlog_item_ids)})"
                if pr.backlog_item_ids else ""
            )
            print(f"  ✓ {pr.title}{tags}  {pr.pr_url}")
    if s.slices_total:
        print()
        print(
            f"Slice verdicts: {s.slices_accepted} accept · "
            f"{s.slices_soft_accepted} soft_accept · "
            f"{s.slices_rejected} reject"
        )
    if s.rubric_delta_pp is not None:
        print(f"Rubric delta: {s.rubric_delta_pp:+.2f}pp")
    if s.escalations:
        sat = (
            f" ({s.saturation_events} saturation, {s.circuit_breaker_trips} circuit-breaker)"
            if (s.saturation_events or s.circuit_breaker_trips) else ""
        )
        print(f"Escalations: {s.escalations}{sat}")


def cmd_ideate(args: argparse.Namespace) -> None:
    """Run feature ideation against an app's research profile."""
    from chad_captain.protocol import AppWorkspace
    from chad_captain.research import synthesize_profile, load_profile
    from chad_captain.research.ideation import (
        ideate_features, merge_candidates_into_backlog,
    )
    from chad_captain.scorecard import score_repo
    from chad_captain.extras import get_extras
    from chad_captain.apps_registry import load_registry

    ws = AppWorkspace(args.app)
    reg = load_registry()
    entry = reg.by_id(args.app)
    repo = args.repo or (entry.repo_path if entry else None)
    if not repo:
        print(f"no repo path for {args.app} (pass --repo or register the app)",
              file=sys.stderr)
        sys.exit(2)

    profile = (
        synthesize_profile(ws, repo, refresh=True)
        if args.refresh_research
        else (load_profile(ws) or synthesize_profile(ws, repo))
    )
    sc = score_repo(repo, extras=get_extras(args.app))
    weak = sorted(sc.dimensions, key=lambda d: d.score)[:5]
    weak_dims = [f"{d.name} ({d.score:.2f}): {d.rationale}" for d in weak]

    candidates, saturation = ideate_features(
        ws, profile, scorecard_weak_dims=weak_dims, model=args.model,
    )

    if not candidates:
        print("(no candidates)")
        if saturation:
            print(f"saturation: {saturation}")
        return

    print(f"# {len(candidates)} candidate(s)")
    if saturation:
        print(f"# saturation_note: {saturation}")
    for c in candidates:
        print(f"  {c.title}")
        print(f"    priority={c.priority:.2f} est={c.estimated_slice_count}")
        if c.rationale:
            print(f"    rationale: {c.rationale[:200]}")
        for ev in c.competitive_evidence[:3]:
            print(f"    evidence: {ev[:120]}")

    if args.dry_run:
        print()
        print("(dry-run) — backlog not modified")
        return

    added, skipped = merge_candidates_into_backlog(ws, candidates)
    print()
    print(f"merged: +{added} new, {skipped} dedup'd against existing")
    if added > 0:
        if _clear_saturation_pause(ws):
            print("saturation pause cleared — captain can resume on next tick")


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

    unpause_p = sub.add_parser(
        "unpause", help="Clear circuit-breaker pause marker for an app",
    )
    unpause_p.add_argument("--app", required=True, metavar="APP_ID")

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
    # PR2 fix: --repo is now optional; resolved from registry when registered.
    replan_p.add_argument("--repo", default=None, metavar="PATH",
                          help="Override repo_path (default: registry lookup)")
    replan_p.add_argument("--trigger", default="manual",
                          choices=("initial", "exhausted", "soft_accept_streak",
                                    "admiral_note", "manual", "publish"))
    replan_p.add_argument("--refresh-research", action="store_true")
    replan_p.add_argument("--no-llm", action="store_true",
                          help="Skip the LLM call; use the deterministic fallback")

    backlog_p = sub.add_parser(
        "backlog",
        help="Manage the per-app feature backlog (Phase A — manual seeding)",
    )
    backlog_sub = backlog_p.add_subparsers(dest="backlog_cmd", required=True)

    bl_add = backlog_sub.add_parser("add", help="Add a feature to the backlog")
    bl_add.add_argument("--app", required=True, metavar="APP_ID")
    bl_add.add_argument("--title", required=True)
    bl_add.add_argument("--rationale", default="")
    bl_add.add_argument("--priority", type=float, default=0.5)
    bl_add.add_argument("--slices", type=int, default=2,
                         help="Estimated slice count to ship this feature")
    bl_add.add_argument("--source", default="manual",
                         choices=("admiral", "research", "manual", "auto-ideation"))
    bl_add.add_argument(
        "--evidence", action="append", default=[],
        help="Competitive-evidence URL/excerpt; repeat for multiple",
    )

    bl_list = backlog_sub.add_parser(
        "list", help="List the app's backlog (queued by default)",
    )
    bl_list.add_argument("--app", required=True, metavar="APP_ID")
    bl_list.add_argument("--all", action="store_true",
                          help="Include shipped/deferred/obsolete items")
    bl_list.add_argument("--json", action="store_true")

    bl_ship = backlog_sub.add_parser(
        "ship", help="Manually mark a backlog item shipped (for backfill)",
    )
    bl_ship.add_argument("--app", required=True, metavar="APP_ID")
    bl_ship.add_argument("item_id")
    bl_ship.add_argument("--pr", default="manual",
                          help="PR url/ref recorded as shipped_in")

    bl_defer = backlog_sub.add_parser(
        "defer", help="Defer or obsolete a backlog item",
    )
    bl_defer.add_argument("--app", required=True, metavar="APP_ID")
    bl_defer.add_argument("item_id")
    bl_defer.add_argument(
        "--status", default="deferred", choices=("deferred", "obsolete", "queued"),
    )

    summary_p = sub.add_parser(
        "summary",
        help="Print a human-readable session summary for an app",
    )
    summary_p.add_argument("--app", required=True, metavar="APP_ID")
    summary_p.add_argument(
        "--since", default="24h",
        help="Time window: '24h', '7d', '30m', or 'all' (default 24h)",
    )
    summary_p.add_argument("--json", action="store_true",
                            help="Output the full structured summary as JSON")

    ideate_p = sub.add_parser(
        "ideate",
        help="Phase B — auto-generate feature backlog candidates from research",
    )
    ideate_p.add_argument("--app", required=True, metavar="APP_ID")
    ideate_p.add_argument("--repo", default=None, metavar="PATH",
                           help="Repo path; defaults to registry entry. Used to refresh "
                                "research if needed.")
    ideate_p.add_argument("--refresh-research", action="store_true",
                           help="Force a fresh research pass before ideating")
    ideate_p.add_argument("--dry-run", action="store_true",
                           help="Print candidates as JSON; don't write to backlog")
    ideate_p.add_argument("--model", default="opus",
                           choices=("opus", "haiku", "sonnet"))

    scorecard_p = sub.add_parser(
        "scorecard",
        help="Print one app's fresh scorecard (baseline dims + extras)",
    )
    scorecard_p.add_argument("--app", required=True, metavar="APP_ID")
    scorecard_p.add_argument(
        "--repo", default=None, metavar="PATH",
        help="Override repo_path (default: registry lookup)",
    )
    scorecard_p.add_argument(
        "--json", action="store_true", help="Print full scorecard as JSON",
    )

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
        "scorecard": cmd_scorecard,
        "research": cmd_research,
        "replan": cmd_replan,
        "tick": cmd_tick,
        "tick-all": cmd_tick_all,
        "unpause": cmd_unpause,
        "register": cmd_register,
        "install-plists": cmd_install_plists,
        "init-workspace": cmd_init_workspace,
        "backlog": cmd_backlog,
        "ideate": cmd_ideate,
        "summary": cmd_summary,
    }
    dispatch[args.command](args)


if __name__ == "__main__":
    main()
