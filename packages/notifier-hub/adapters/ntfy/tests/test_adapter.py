"""Tests for NtfyAdapter."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import httpx
import pytest

from notifier_hub_core.models import Notification
from notifier_hub_ntfy import NtfyAdapter


def _make_notification(severity: str = "info") -> Notification:
    return Notification(
        title="Alert",
        body="Something happened",
        severity=severity,
        channel="ops",
    )


def _mock_response(status_code: int = 200) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status_code
    resp.is_success = status_code < 300
    return resp


def test_post_url_and_body(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = []

    def fake_post(url, *, content, headers, timeout):
        calls.append({"url": url, "content": content, "headers": headers})
        return _mock_response(200)

    monkeypatch.setattr(httpx, "post", fake_post)
    adapter = NtfyAdapter(topic="my-topic", server="https://ntfy.sh")
    n = _make_notification()
    result = adapter.send(n)
    assert result.ok is True
    assert len(calls) == 1
    assert calls[0]["url"] == "https://ntfy.sh/my-topic"
    assert calls[0]["content"] == b"Something happened"


def test_priority_mapping_info(monkeypatch: pytest.MonkeyPatch) -> None:
    captured = {}

    def fake_post(url, *, content, headers, timeout):
        captured["headers"] = headers
        return _mock_response(200)

    monkeypatch.setattr(httpx, "post", fake_post)
    adapter = NtfyAdapter(topic="t")
    adapter.send(_make_notification(severity="info"))
    assert captured["headers"]["Priority"] == "3"


def test_priority_mapping_warn(monkeypatch: pytest.MonkeyPatch) -> None:
    captured = {}

    def fake_post(url, *, content, headers, timeout):
        captured["headers"] = headers
        return _mock_response(200)

    monkeypatch.setattr(httpx, "post", fake_post)
    adapter = NtfyAdapter(topic="t")
    adapter.send(_make_notification(severity="warn"))
    assert captured["headers"]["Priority"] == "4"


def test_priority_mapping_critical(monkeypatch: pytest.MonkeyPatch) -> None:
    captured = {}

    def fake_post(url, *, content, headers, timeout):
        captured["headers"] = headers
        return _mock_response(200)

    monkeypatch.setattr(httpx, "post", fake_post)
    adapter = NtfyAdapter(topic="t")
    adapter.send(_make_notification(severity="critical"))
    assert captured["headers"]["Priority"] == "5"


def test_auth_header_included_when_token_set(monkeypatch: pytest.MonkeyPatch) -> None:
    captured = {}

    def fake_post(url, *, content, headers, timeout):
        captured["headers"] = headers
        return _mock_response(200)

    monkeypatch.setattr(httpx, "post", fake_post)
    adapter = NtfyAdapter(topic="t", auth_token="secret-token")
    adapter.send(_make_notification())
    assert captured["headers"].get("Authorization") == "Bearer secret-token"


def test_auth_header_omitted_when_no_token(monkeypatch: pytest.MonkeyPatch) -> None:
    captured = {}

    def fake_post(url, *, content, headers, timeout):
        captured["headers"] = headers
        return _mock_response(200)

    monkeypatch.setattr(httpx, "post", fake_post)
    adapter = NtfyAdapter(topic="t")
    adapter.send(_make_notification())
    assert "Authorization" not in captured["headers"]


def test_ok_false_on_500(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(httpx, "post", lambda *a, **kw: _mock_response(500))
    adapter = NtfyAdapter(topic="t")
    result = adapter.send(_make_notification())
    assert result.ok is False
    assert "500" in result.detail


def test_ok_false_on_request_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_post(url, *, content, headers, timeout):
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(httpx, "post", fake_post)
    adapter = NtfyAdapter(topic="t")
    result = adapter.send(_make_notification())
    assert result.ok is False
    assert result.detail is not None


def test_title_in_header(monkeypatch: pytest.MonkeyPatch) -> None:
    captured = {}

    def fake_post(url, *, content, headers, timeout):
        captured["headers"] = headers
        return _mock_response(200)

    monkeypatch.setattr(httpx, "post", fake_post)
    adapter = NtfyAdapter(topic="t")
    n = Notification(title="My Title", body="body", severity="info", channel="c")
    adapter.send(n)
    assert captured["headers"]["Title"] == "My Title"
