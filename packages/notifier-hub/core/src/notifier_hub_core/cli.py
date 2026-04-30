"""CLI for notifier-hub-core."""

from __future__ import annotations

import argparse
import json
import sys

from notifier_hub_core.config import ConfigNotFoundError, load_config
from notifier_hub_core.hub import NotifierHub
from notifier_hub_core.models import Notification


def _cmd_send(args: argparse.Namespace) -> int:
    try:
        hub = NotifierHub()
    except ConfigNotFoundError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    notification = Notification(
        title=args.title,
        body=args.body,
        severity=args.severity,
        channel=args.channel,
    )
    results = hub.send(notification)
    for r in results:
        status = "ok" if r.ok else "FAIL"
        detail = f" ({r.detail})" if r.detail else ""
        print(f"  {r.adapter}: {status}{detail}")
    failures = [r for r in results if not r.ok]
    return 1 if failures else 0


def _cmd_routes(args: argparse.Namespace) -> int:  # noqa: ARG001
    try:
        config = load_config()
    except ConfigNotFoundError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print(json.dumps(
        {
            "routes": [
                {"channel": r.channel, "adapters": r.adapters}
                for r in config.routes
            ],
            "fallback_adapters": config.fallback_adapters,
        },
        indent=2,
    ))
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="notifier-hub",
        description="Send notifications and inspect routing config.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    send_p = sub.add_parser("send", help="Send a notification")
    send_p.add_argument("--channel", required=True, help="Logical channel name")
    send_p.add_argument("--title", required=True, help="Notification title")
    send_p.add_argument("--body", required=True, help="Notification body")
    send_p.add_argument(
        "--severity",
        choices=["info", "warn", "critical"],
        default="info",
        help="Severity level (default: info)",
    )

    sub.add_parser("routes", help="List current routing config")

    args = parser.parse_args()
    if args.command == "send":
        sys.exit(_cmd_send(args))
    elif args.command == "routes":
        sys.exit(_cmd_routes(args))
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
