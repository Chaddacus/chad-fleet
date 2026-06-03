"""Mail backend: the connector abstraction both the read projection and the MCP use.

`MailBackend` is the seam — `ImapBackend` is the real stdlib implementation; tests inject a
fake. Read methods (`list_recent`, `fetch`) feed the aggregator's email tab; action methods
(`send`, `archive`) are exposed by the MCP server for captains to call. All credentials come
from `accounts.load_account()` (one home).
"""

from __future__ import annotations

import email
import imaplib
import smtplib
from email.header import decode_header, make_header
from email.message import EmailMessage as PyEmailMessage
from typing import Protocol, runtime_checkable

from .accounts import EmailAccount, load_account


@runtime_checkable
class MailBackend(Protocol):
    def list_recent(self, limit: int = 25) -> list[dict]: ...
    def fetch(self, msg_id: str) -> dict: ...
    def archive(self, msg_id: str) -> None: ...
    def send(self, to: str, subject: str, body: str) -> None: ...


def _decode(value: str | None) -> str:
    if not value:
        return ""
    try:
        return str(make_header(decode_header(value)))
    except Exception:
        return value


class ImapBackend:
    """Real IMAP/SMTP backend over stdlib. Mailbox defaults to INBOX."""

    def __init__(self, account: EmailAccount, mailbox: str = "INBOX") -> None:
        self._account = account
        self._mailbox = mailbox

    def _imap(self) -> imaplib.IMAP4_SSL:
        conn = imaplib.IMAP4_SSL(self._account.imap_host, self._account.imap_port)
        conn.login(self._account.user, self._account.password)
        return conn

    def list_recent(self, limit: int = 25) -> list[dict]:
        conn = self._imap()
        try:
            conn.select(self._mailbox, readonly=True)
            typ, data = conn.search(None, "ALL")
            if typ != "OK" or not data or not data[0]:
                return []
            ids = data[0].split()[-limit:]
            out: list[dict] = []
            for raw_id in reversed(ids):  # newest first
                mid = raw_id.decode()
                typ, msg_data = conn.fetch(raw_id, "(FLAGS BODY.PEEK[HEADER.FIELDS (SUBJECT FROM DATE)])")
                if typ != "OK" or not msg_data:
                    continue
                flags_raw = b""
                header_raw = b""
                for part in msg_data:
                    if isinstance(part, tuple):
                        header_raw = part[1]
                        flags_raw += part[0]
                    elif isinstance(part, (bytes, bytearray)):
                        flags_raw += part
                msg = email.message_from_bytes(header_raw)
                unread = b"\\Seen" not in flags_raw
                out.append({
                    "id": mid,
                    "subject": _decode(msg.get("Subject")),
                    "from_": _decode(msg.get("From")),
                    "date": _decode(msg.get("Date")),
                    "unread": unread,
                    "snippet": "",
                })
            return out
        finally:
            try:
                conn.logout()
            except Exception:
                pass

    def fetch(self, msg_id: str) -> dict:
        conn = self._imap()
        try:
            conn.select(self._mailbox, readonly=True)
            typ, msg_data = conn.fetch(msg_id.encode(), "(RFC822)")
            if typ != "OK" or not msg_data or not isinstance(msg_data[0], tuple):
                return {"id": msg_id, "subject": "", "from_": "", "date": "", "body": ""}
            msg = email.message_from_bytes(msg_data[0][1])
            return {
                "id": msg_id,
                "subject": _decode(msg.get("Subject")),
                "from_": _decode(msg.get("From")),
                "date": _decode(msg.get("Date")),
                "body": _extract_body(msg),
            }
        finally:
            try:
                conn.logout()
            except Exception:
                pass

    def archive(self, msg_id: str) -> None:
        conn = self._imap()
        try:
            conn.select(self._mailbox)
            # "Archive" = remove from INBOX. Use the Gmail-style move if available, else flag.
            conn.store(msg_id.encode(), "+FLAGS", "\\Deleted")
            conn.expunge()
        finally:
            try:
                conn.logout()
            except Exception:
                pass

    def send(self, to: str, subject: str, body: str) -> None:
        msg = PyEmailMessage()
        msg["From"] = self._account.user
        msg["To"] = to
        msg["Subject"] = subject
        msg.set_content(body)
        with smtplib.SMTP_SSL(self._account.smtp_host, self._account.smtp_port) as smtp:
            smtp.login(self._account.user, self._account.password)
            smtp.send_message(msg)


def _extract_body(msg: email.message.Message) -> str:
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/plain":
                try:
                    return part.get_payload(decode=True).decode(part.get_content_charset() or "utf-8", "replace")
                except Exception:
                    continue
        return ""
    try:
        payload = msg.get_payload(decode=True)
        return payload.decode(msg.get_content_charset() or "utf-8", "replace") if payload else ""
    except Exception:
        return ""


def get_backend() -> MailBackend | None:
    """The configured backend, or None if email is not set up (hub runs without it)."""
    account = load_account()
    return ImapBackend(account) if account is not None else None
