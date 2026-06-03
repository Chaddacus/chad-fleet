"""Validate the SMTP send path WITHOUT sending: connect (SMTP_SSL) + auth + NOOP + quit.

Proves credentials + transport for backend.send() without mutating anything or emitting mail.
Run under bws so EMAIL_* are present:
    bws run --project-id <id> --shell bash -- "uv run python scripts/smtp_validate_probe.py"
"""

from __future__ import annotations

import smtplib
import sys

from email_mcp.accounts import load_account


def main() -> int:
    acct = load_account()
    if acct is None:
        print("FAIL: email unconfigured (EMAIL_IMAP_HOST/USER/PASSWORD)")
        return 1
    print(f"smtp target: {acct.smtp_host}:{acct.smtp_port}  user={acct.user}")
    try:
        with smtplib.SMTP_SSL(acct.smtp_host, acct.smtp_port, timeout=20) as smtp:
            smtp.login(acct.user, acct.password)
            smtp.noop()
    except Exception as exc:  # noqa: BLE001 - probe surfaces the real failure
        print(f"FAIL: {type(exc).__name__}: {exc}")
        return 1
    print("SMTP AUTH VALID — login succeeded, no message sent")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
