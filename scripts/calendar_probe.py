"""Live calendar connection probe. Prints which backend is configured, then lists upcoming events.

    bws run --project-id <id> --shell bash -- "uv run python scripts/calendar_probe.py"
"""

from __future__ import annotations

import sys

from calendar_mcp.accounts import load_account, load_google_account
from calendar_mcp.backend import get_backend


def main() -> int:
    g = load_google_account()
    c = load_account()
    print(f"google configured: {g is not None} (calendar_id={g.calendar_id if g else '-'})")
    print(f"caldav configured: {c is not None}")
    backend = get_backend()
    if backend is None:
        print("FAIL: no calendar backend configured")
        return 1
    print(f"backend: {type(backend).__name__}")
    try:
        events = backend.list_events(days=30, limit=5)
    except Exception as exc:  # noqa: BLE001 - surface the real failure
        print(f"FAIL: {type(exc).__name__}: {exc}")
        return 1
    print(f"OK — {len(events)} upcoming event(s):")
    for e in events:
        print(f"  • {e.get('start','')}  {e.get('summary','(untitled)')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
