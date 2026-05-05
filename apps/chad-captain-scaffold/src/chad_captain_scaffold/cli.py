"""chad-captain-scaffold CLI.

Subcommands:
  artifacts list     enumerate artifacts for a task_id
  artifacts schema   print the JSON Schema for a registered schema_id

Future: ``scaffold`` (RESEARCH → REGISTER → ACTIVATE phases).
"""

from __future__ import annotations

import argparse
import json
import sys


def cmd_artifacts_list(args: argparse.Namespace) -> None:
    from chad_captain_scaffold.artifacts import list_artifacts
    metas = list_artifacts(args.task)
    print(json.dumps([m.model_dump() for m in metas], indent=2))


def cmd_artifacts_schema(args: argparse.Namespace) -> None:
    from chad_captain_scaffold.artifacts import (
        ArtifactSchemaNotRegistered, schema_json_schema,
    )
    try:
        sch = schema_json_schema(args.schema_id)
    except ArtifactSchemaNotRegistered:
        print(json.dumps({"error": f"schema_id {args.schema_id!r} not registered"}))
        sys.exit(1)
    print(json.dumps(sch, indent=2))


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="chad-captain-scaffold")
    sub = parser.add_subparsers(dest="command", required=True)

    artifacts = sub.add_parser("artifacts", help="Cross-task artifact bus operations")
    artifacts_sub = artifacts.add_subparsers(dest="artifacts_cmd", required=True)

    list_p = artifacts_sub.add_parser("list", help="List artifacts for a task")
    list_p.add_argument("--task", required=True, metavar="TASK_ID")

    schema_p = artifacts_sub.add_parser("schema", help="Print JSON Schema for a registered id")
    schema_p.add_argument("--schema-id", required=True, dest="schema_id")

    args = parser.parse_args(argv)
    if args.command == "artifacts":
        if args.artifacts_cmd == "list":
            cmd_artifacts_list(args)
        elif args.artifacts_cmd == "schema":
            cmd_artifacts_schema(args)


if __name__ == "__main__":
    main()
