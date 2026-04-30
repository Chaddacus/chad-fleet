"""CLI entry point for voice-drafter.

Commands:
  voice-drafter draft --signal-file <path.json> --voice <path.yaml> --candidate-id <id>
  voice-drafter list-voices
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime
from pathlib import Path


def _cmd_draft(args: argparse.Namespace) -> int:
    from voice_drafter.drafter import Drafter
    from voice_drafter.types import SignalItem, SignalPack
    from voice_drafter.voice_config import load_voice_config

    try:
        raw = json.loads(Path(args.signal_file).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"ERROR reading signal file: {exc}", file=sys.stderr)
        return 1

    # Parse signal pack from raw dict.
    raw_items = raw.get("items") or []
    items = [SignalItem(**it) if isinstance(it, dict) else it for it in raw_items]
    gen_at = raw.get("generated_at")
    if isinstance(gen_at, str):
        generated_at = datetime.fromisoformat(gen_at)
    else:
        from datetime import timezone
        generated_at = datetime.now(timezone.utc)

    sp = SignalPack(
        items=items,
        source=raw.get("source", "unknown"),
        generated_at=generated_at,
    )

    try:
        voice = load_voice_config(Path(args.voice))
    except ValueError as exc:
        print(f"ERROR loading voice config: {exc}", file=sys.stderr)
        return 1

    candidate = next((it for it in sp.items if it.id == args.candidate_id), None)
    if candidate is None:
        print(f"ERROR: candidate_id {args.candidate_id!r} not found in signal pack", file=sys.stderr)
        return 1

    drafter = Drafter(voice)
    drafts = drafter.draft_variations(candidate, sp)

    for d in drafts:
        print("=" * 72)
        header = (
            f"# V{d.variation_index}: {args.candidate_id}\n"
            f"_provider: {d.provider} | opener: {d.opener_shape}_\n\n"
        )
        if d.body:
            print(header + d.body)
        else:
            print(header + f"_SKIPPED: {d.skip_reason}_")
        print()

    return 0


def _cmd_list_voices(_args: argparse.Namespace) -> int:
    from voice_drafter.voice_config import bundled_voice_configs

    voices = bundled_voice_configs()
    if not voices:
        print("(no bundled voice configs)")
    else:
        for name, path in voices.items():
            print(f"{name}: {path}")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="voice-drafter",
        description="Draft content variations using a voice config and signal pack.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    draft_p = sub.add_parser("draft", help="Draft variations for a candidate signal item")
    draft_p.add_argument("--signal-file", required=True, help="Path to JSON signal pack")
    draft_p.add_argument("--voice", required=True, help="Path to YAML voice config")
    draft_p.add_argument("--candidate-id", required=True, help="ID of the candidate SignalItem")

    sub.add_parser("list-voices", help="List bundled voice configs")

    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    if args.command == "draft":
        sys.exit(_cmd_draft(args))
    elif args.command == "list-voices":
        sys.exit(_cmd_list_voices(args))
    else:
        parser.print_help()
        sys.exit(1)


__all__ = ["main"]
