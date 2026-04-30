"""EmailAdapter — sends notifications via SMTP using starttls."""

from __future__ import annotations

import os
import smtplib
from email.message import EmailMessage

from notifier_hub_core.models import Notification, SendResult


class EmailAdapter:
    name = "email"

    def __init__(
        self,
        from_addr: str | None = None,
        to_addr: str | None = None,
        smtp_host: str | None = None,
        smtp_port: int | None = None,
        smtp_user: str | None = None,
        smtp_password: str | None = None,
    ) -> None:
        self._from_addr = from_addr or os.environ.get("CHAD_EMAIL_FROM")
        self._to_addr = to_addr or os.environ.get("CHAD_EMAIL_TO")
        self._smtp_host = smtp_host or os.environ.get("CHAD_EMAIL_SMTP_HOST")
        port_env = os.environ.get("CHAD_EMAIL_SMTP_PORT")
        self._smtp_port = smtp_port if smtp_port is not None else (int(port_env) if port_env else 587)
        self._smtp_user = smtp_user or os.environ.get("CHAD_EMAIL_SMTP_USER")
        self._smtp_password = smtp_password or os.environ.get("CHAD_EMAIL_SMTP_PASSWORD")

    def send(self, notification: Notification) -> SendResult:
        try:
            if notification.severity == "info":
                subject = notification.title
            else:
                subject = f"[{notification.severity.upper()}] {notification.title}"

            msg = EmailMessage()
            msg["From"] = self._from_addr
            msg["To"] = self._to_addr
            msg["Subject"] = subject
            msg.set_content(notification.body)

            with smtplib.SMTP(self._smtp_host, self._smtp_port) as smtp:
                smtp.starttls()
                smtp.login(self._smtp_user, self._smtp_password)
                smtp.sendmail(self._from_addr, [self._to_addr], msg.as_string())

            return SendResult(adapter="email", ok=True)
        except smtplib.SMTPException as e:
            return SendResult(
                adapter="email",
                ok=False,
                detail=f"{type(e).__name__}: {e}",
            )
