"""CLI for tracked-app-registry.

Usage:
    tracked-app-registry list [--state X] [--owner Y]
    tracked-app-registry get <id>
    tracked-app-registry add [--from-json <path>]
    tracked-app-registry set-state <id> <state> [--reason TEXT]
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

from .models import TrackedApp
from .registry import AppNotFound, Registry


def _now() -> datetime:
    return datetime.now(UTC)


def cmd_list(args: argparse.Namespace) -> None:
    reg = Registry()
    apps = reg.list(state=args.state or None, owner_brand=args.owner or None)
    if not apps:
        print("No apps found.")
        return
    for app in apps:
        blocked = f"  [{app.blocked_reason}]" if app.blocked_reason else ""
        print(f"{app.id}  {app.state}  {app.owner_brand}  {app.name}{blocked}")


def cmd_get(args: argparse.Namespace) -> None:
    reg = Registry()
    app = reg.get(args.id)
    if app is None:
        print(f"Not found: {args.id}", file=sys.stderr)
        sys.exit(1)
    print(json.dumps(app.model_dump(mode="json"), indent=2))


def cmd_add(args: argparse.Namespace) -> None:
    if args.from_json:
        data = json.loads(Path(args.from_json).read_text(encoding="utf-8"))
    else:
        data = _interactive_add()
    now = _now()
    data.setdefault("created_at", now.isoformat())
    data.setdefault("updated_at", now.isoformat())
    data.setdefault("last_progress_at", now.isoformat())
    app = TrackedApp.model_validate(data)
    reg = Registry()
    reg.create(app)
    print(f"Created: {app.id}")


def _interactive_add() -> dict:
    """Prompt the user for required fields."""
    data: dict = {}
    data["id"] = input("id (slug): ").strip()
    data["name"] = input("name: ").strip()
    data["repo_path"] = input("repo_path (enter to skip): ").strip() or None
    data["repo_url"] = input("repo_url (enter to skip): ").strip() or None
    data["mode"] = input("mode [launch_driven/continuous/event_driven]: ").strip()
    data["cadence"] = input("cadence (cron/continuous/manual): ").strip()
    data["owner_brand"] = input("owner_brand [chad-simon/chadacys/internal/external]: ").strip()
    agents = input("owner_agents (comma-separated, or blank): ").strip()
    data["owner_agents"] = [a.strip() for a in agents.split(",") if a.strip()]
    return data


def cmd_set_state(args: argparse.Namespace) -> None:
    reg = Registry()
    try:
        app = reg.set_state(args.id, args.state, blocked_reason=args.reason or None)
    except AppNotFound:
        print(f"Not found: {args.id}", file=sys.stderr)
        sys.exit(1)
    print(f"Updated {app.id} -> {app.state}")


def main() -> None:
    parser = argparse.ArgumentParser(prog="tracked-app-registry")
    sub = parser.add_subparsers(dest="command", required=True)

    p_list = sub.add_parser("list", help="List tracked apps")
    p_list.add_argument("--state", default=None)
    p_list.add_argument("--owner", default=None)

    p_get = sub.add_parser("get", help="Get a tracked app by id")
    p_get.add_argument("id")

    p_add = sub.add_parser("add", help="Add a new tracked app")
    p_add.add_argument("--from-json", dest="from_json", default=None)

    p_state = sub.add_parser("set-state", help="Change app state")
    p_state.add_argument("id")
    p_state.add_argument("state")
    p_state.add_argument("--reason", default=None)

    args = parser.parse_args()
    dispatch = {
        "list": cmd_list,
        "get": cmd_get,
        "add": cmd_add,
        "set-state": cmd_set_state,
    }
    dispatch[args.command](args)


if __name__ == "__main__":
    main()
