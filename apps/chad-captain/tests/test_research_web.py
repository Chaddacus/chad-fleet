"""Tests for the web research stub — verifies skip/error semantics without
hitting a real CLI."""

from __future__ import annotations

import pytest

from chad_captain.research import web as web_mod
from chad_captain.research.web import WebProfile, research_web


def test_web_skipped_when_claude_missing(monkeypatch) -> None:
    monkeypatch.setattr(web_mod, "CLAUDE_BIN", "/no/such/binary/here")
    monkeypatch.setattr(web_mod.shutil, "which", lambda _name: None)
    profile = research_web(name="x", summary="y")
    assert profile.status == "skipped"
    assert "claude CLI not found" in profile.reason


def test_web_error_returned_on_llm_failure(monkeypatch) -> None:
    """When the CLI exists but ``claude_complete`` raises, return error status."""
    monkeypatch.setattr(web_mod.Path, "exists", lambda self: True)

    def boom(*_args, **_kwargs):
        raise web_mod.LLMError("boom")

    monkeypatch.setattr(web_mod, "claude_complete", boom)
    profile = research_web(name="x", summary="y")
    assert profile.status == "error"
    assert "boom" in profile.reason


def test_web_ok_passes_through_text(monkeypatch) -> None:
    monkeypatch.setattr(web_mod.Path, "exists", lambda self: True)
    monkeypatch.setattr(web_mod, "claude_complete", lambda *_a, **_kw: "## Positioning\nA project for X.\n")
    profile = research_web(name="x", summary="y")
    assert profile.status == "ok"
    assert "Positioning" in profile.landscape_md


def test_web_profile_factory_helpers() -> None:
    a = WebProfile.skipped("missing")
    b = WebProfile.errored("blew up")
    assert a.status == "skipped" and a.reason == "missing"
    assert b.status == "error" and b.reason == "blew up"
