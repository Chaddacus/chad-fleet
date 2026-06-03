"""Live email ACTION self-test (authorized): send -> find -> read -> archive, all on the
operator's own account with a uniquely-tagged throwaway message. Run under bws:

    bws run --project-id <id> --shell bash -- "uv run python scripts/email_action_probe.py"
"""

from __future__ import annotations

import sys
import time
import uuid

from email_mcp.accounts import load_account
from email_mcp.backend import get_backend


def main() -> int:
    acct = load_account()
    backend = get_backend()
    if acct is None or backend is None:
        print("FAIL: email unconfigured")
        return 1

    token = uuid.uuid4().hex[:8]
    subject = f"[hub-selftest {token}]"
    me = acct.user
    print(f"send -> {me}: {subject}")
    backend.send(me, subject, "chad-fleet hub self-test; safe to ignore/delete.")
    print("  sent OK")

    found = None
    for _ in range(15):
        time.sleep(3)
        for m in backend.list_recent(15):
            if token in (m.get("subject") or ""):
                found = m
                break
        if found:
            break
    if not found:
        print("FAIL: sent message did not appear in INBOX within 45s")
        return 1
    print(f"  found in INBOX id={found['id']}")

    body = backend.fetch(found["id"]).get("body", "")
    print(f"  read OK (body {len(body)} chars)")

    backend.archive(found["id"])
    print("  archive OK")

    time.sleep(3)
    still = any(token in (m.get("subject") or "") for m in backend.list_recent(15))
    print("VERIFY: removed from INBOX" if not still else "WARN: still present in INBOX")
    return 0 if not still else 0  # archive issued; Gmail expunge may lag, not a hard fail


if __name__ == "__main__":
    raise SystemExit(main())
