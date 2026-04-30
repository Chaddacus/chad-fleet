"""CLI entry point for state-aggregator."""

from __future__ import annotations

import argparse
import json
import sys


def cmd_snapshot(_args: argparse.Namespace) -> None:
    from .aggregator import Aggregator

    agg = Aggregator()
    state = agg.snapshot()
    print(state.model_dump_json(indent=2))


def cmd_serve(args: argparse.Namespace) -> None:
    import uvicorn

    from .server import app, get_port

    port = args.port if args.port else get_port()
    uvicorn.run(app, host="0.0.0.0", port=port)


def main() -> None:
    parser = argparse.ArgumentParser(prog="state-aggregator")
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("snapshot", help="Print FleetState JSON to stdout")

    serve_p = sub.add_parser("serve", help="Start the HTTP service")
    serve_p.add_argument("--port", type=int, default=None, help="Port (default 8106)")

    args = parser.parse_args()
    if args.command == "snapshot":
        cmd_snapshot(args)
    elif args.command == "serve":
        cmd_serve(args)
    else:
        parser.print_help()
        sys.exit(1)
