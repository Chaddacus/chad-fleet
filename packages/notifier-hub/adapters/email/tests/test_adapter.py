"""Tests for EmailAdapter."""

from __future__ import annotations

import smtplib
from unittest.mock import MagicMock, patch, call

import pytest

from notifier_hub_core.models import Notification
from notifier_hub_email import EmailAdapter


def _make_adapter(**kwargs) -> EmailAdapter:
    defaults = {
        "from_addr": "from@example.com",
        "to_addr": "to@example.com",
        "smtp_host": "smtp.example.com",
        "smtp_port": 587,
        "smtp_user": "user",
        "smtp_password": "pass",
    }
    defaults.update(kwargs)
    return EmailAdapter(**defaults)


def _make_notification(severity: str = "info", title: str = "Hello") -> Notification:
    return Notification(
        title=title,
        body="Body text",
        severity=severity,
        channel="chan",
    )


def test_smtp_connection_and_calls() -> None:
    mock_smtp_instance = MagicMock()
    mock_smtp_instance.__enter__ = MagicMock(return_value=mock_smtp_instance)
    mock_smtp_instance.__exit__ = MagicMock(return_value=False)

    with patch("smtplib.SMTP", return_value=mock_smtp_instance) as mock_smtp_cls:
        adapter = _make_adapter()
        result = adapter.send(_make_notification())

    mock_smtp_cls.assert_called_once_with("smtp.example.com", 587)
    mock_smtp_instance.starttls.assert_called_once()
    mock_smtp_instance.login.assert_called_once_with("user", "pass")
    assert mock_smtp_instance.sendmail.called
    assert result.ok is True
    assert result.adapter == "email"


def test_sendmail_from_to_args() -> None:
    mock_smtp_instance = MagicMock()
    mock_smtp_instance.__enter__ = MagicMock(return_value=mock_smtp_instance)
    mock_smtp_instance.__exit__ = MagicMock(return_value=False)

    with patch("smtplib.SMTP", return_value=mock_smtp_instance):
        adapter = _make_adapter()
        adapter.send(_make_notification())

    args = mock_smtp_instance.sendmail.call_args
    assert args[0][0] == "from@example.com"
    assert args[0][1] == ["to@example.com"]


def test_subject_prefix_for_warn() -> None:
    mock_smtp_instance = MagicMock()
    mock_smtp_instance.__enter__ = MagicMock(return_value=mock_smtp_instance)
    mock_smtp_instance.__exit__ = MagicMock(return_value=False)

    with patch("smtplib.SMTP", return_value=mock_smtp_instance):
        adapter = _make_adapter()
        adapter.send(_make_notification(severity="warn", title="Disk Low"))

    raw_msg = mock_smtp_instance.sendmail.call_args[0][2]
    assert "[WARN] Disk Low" in raw_msg


def test_subject_prefix_for_critical() -> None:
    mock_smtp_instance = MagicMock()
    mock_smtp_instance.__enter__ = MagicMock(return_value=mock_smtp_instance)
    mock_smtp_instance.__exit__ = MagicMock(return_value=False)

    with patch("smtplib.SMTP", return_value=mock_smtp_instance):
        adapter = _make_adapter()
        adapter.send(_make_notification(severity="critical", title="Down"))

    raw_msg = mock_smtp_instance.sendmail.call_args[0][2]
    assert "[CRITICAL] Down" in raw_msg


def test_info_severity_no_prefix() -> None:
    mock_smtp_instance = MagicMock()
    mock_smtp_instance.__enter__ = MagicMock(return_value=mock_smtp_instance)
    mock_smtp_instance.__exit__ = MagicMock(return_value=False)

    with patch("smtplib.SMTP", return_value=mock_smtp_instance):
        adapter = _make_adapter()
        adapter.send(_make_notification(severity="info", title="Just Info"))

    raw_msg = mock_smtp_instance.sendmail.call_args[0][2]
    assert "[INFO]" not in raw_msg
    assert "Just Info" in raw_msg


def test_ok_false_on_smtp_auth_error() -> None:
    mock_smtp_instance = MagicMock()
    mock_smtp_instance.__enter__ = MagicMock(return_value=mock_smtp_instance)
    mock_smtp_instance.__exit__ = MagicMock(return_value=False)
    mock_smtp_instance.login.side_effect = smtplib.SMTPAuthenticationError(535, b"Auth failed")

    with patch("smtplib.SMTP", return_value=mock_smtp_instance):
        adapter = _make_adapter()
        result = adapter.send(_make_notification())

    assert result.ok is False
    assert result.adapter == "email"
    assert result.detail is not None
    assert "SMTPAuthenticationError" in result.detail
