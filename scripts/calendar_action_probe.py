"""Live calendar CREATE round-trip self-test (authorized): create a tagged throwaway event,
confirm it reads back, then delete it (no residue left on the real calendar).

    bws run --project-id <id> --shell bash -- "uv run python scripts/calendar_action_probe.py"
"""

from __future__ import annotations

import sys
import uuid
from datetime import datetime, timedelta, timezone

from calendar_mcp.accounts import load_google_account
from calendar_mcp.backend import GoogleCalendarBackend


def main() -> int:
    acct = load_google_account()
    if acct is None:
        print("FAIL: google calendar not configured")
        return 1
    backend = GoogleCalendarBackend(acct)

    token = uuid.uuid4().hex[:8]
    summary = f"[hub-selftest {token}]"
    start = (datetime.now(timezone.utc) + timedelta(days=1)).replace(microsecond=0)
    end = start + timedelta(minutes=30)

    print(f"create -> {summary} @ {start.isoformat()}")
    backend.create_event(summary, start.isoformat(), end.isoformat(), location="hub self-test")
    print("  created OK")

    match = [e for e in backend.list_events(days=2, limit=100) if token in (e.get("summary") or "")]
    print(f"  read-back: {len(match)} matching event(s)")

    svc = backend._service()
    for e in match:
        svc.events().delete(calendarId=acct.calendar_id, eventId=e["id"]).execute()
        print(f"  deleted {e['id']}")

    print("CREATE ROUND-TRIP OK" if match else "WARN: created but not found on read-back")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
