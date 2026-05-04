"""llm.py error-path tests — ensure subprocess failures map to LLMError."""

from __future__ import annotations

import subprocess
from unittest.mock import patch

import pytest

from week_intake.llm import LLMError, claude_complete


def test_claude_complete_translates_filenotfound() -> None:
    """Missing claude binary must raise LLMError, not bubble FileNotFoundError."""
    with patch("week_intake.llm.subprocess.run", side_effect=FileNotFoundError("no claude")):
        with pytest.raises(LLMError) as exc_info:
            claude_complete("hi", timeout=5)
    assert "claude binary not runnable" in str(exc_info.value)


def test_claude_complete_translates_permission_error() -> None:
    with patch("week_intake.llm.subprocess.run", side_effect=PermissionError("no exec")):
        with pytest.raises(LLMError) as exc_info:
            claude_complete("hi", timeout=5)
    assert "claude binary not runnable" in str(exc_info.value)


def test_claude_complete_translates_timeout() -> None:
    with patch(
        "week_intake.llm.subprocess.run",
        side_effect=subprocess.TimeoutExpired(cmd="claude", timeout=5),
    ):
        with pytest.raises(LLMError) as exc_info:
            claude_complete("hi", timeout=5)
    assert "timeout" in str(exc_info.value)
