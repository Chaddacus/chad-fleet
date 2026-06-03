"""Live email-connection probe. Run via the email-mcp/aggregator env with EMAIL_IMAP_* in env:

    bws run --project-id <PROJECT_ID> -- \
      uv run --project packages/state-aggregator python scripts/email_probe.py

Prints the recent inbox subjects, or the full traceback if the IMAP backend errors. Never
prints the password. This is the first real-world exercise of email_mcp.ImapBackend.
"""

from __future__ import annotations

import os
import sys
import traceback


def main() -> int:
    host = os.environ.get("EMAIL_IMAP_HOST")
    user = os.environ.get("EMAIL_IMAP_USER")
    pw_set = bool(os.environ.get("EMAIL_IMAP_PASSWORD"))
    print(f"config: host={host} user={user} password={'SET' if pw_set else 'MISSING'}")

    from email_mcp import get_backend

    backend = get_backend()
    if backend is None:
        print("get_backend() -> None: EMAIL_IMAP_HOST/USER/PASSWORD not all set in this env.")
        return 1

    try:
        msgs = backend.list_recent(5)
    except Exception:
        print("\n--- IMAP error (full traceback) ---")
        traceback.print_exc()
        return 2

    print(f"\nOK — {len(msgs)} message(s):")
    for m in msgs:
        unread = "•" if m.get("unread") else " "
        print(f"  {unread} {m.get('subject', '')[:60]:60}  <{m.get('from_', '')[:40]}>")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
