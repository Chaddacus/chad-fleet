"""Tests for the llm subprocess adapter.

All subprocess calls are mocked — no real CLIs are invoked.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from voice_drafter import llm as _llm
from voice_drafter.llm import LLMError, _strip_fences, claude_complete, codex_complete


# ---- Helpers -----------------------------------------------------------------

def _proc(stdout: str = "", stderr: str = "", returncode: int = 0) -> MagicMock:
    m = MagicMock()
    m.stdout = stdout
    m.stderr = stderr
    m.returncode = returncode
    return m


def _claude_json_envelope(text: str) -> str:
    return json.dumps({"result": text, "is_error": False})


# ---- claude_complete ---------------------------------------------------------

class TestClaudeComplete:
    def test_success_json_envelope(self, tmp_path, monkeypatch):
        monkeypatch.delenv("VOICE_DRAFTER_CLAUDE_TEXT_MODE", raising=False)
        with patch("subprocess.run", return_value=_proc(_claude_json_envelope("hello world"))) as mock_run:
            result = claude_complete("my prompt", model="opus")
        assert result == "hello world"
        cmd = mock_run.call_args[0][0]
        assert "--output-format" in cmd
        assert "json" in cmd

    def test_success_text_mode(self, monkeypatch):
        monkeypatch.setenv("VOICE_DRAFTER_CLAUDE_TEXT_MODE", "1")
        with patch("subprocess.run", return_value=_proc("direct text")):
            result = claude_complete("prompt")
        assert result == "direct text"

    def test_nonzero_exit_raises(self, monkeypatch):
        monkeypatch.delenv("VOICE_DRAFTER_CLAUDE_TEXT_MODE", raising=False)
        with patch("subprocess.run", return_value=_proc("", "auth failed", returncode=1)):
            with pytest.raises(LLMError, match="claude exit 1"):
                claude_complete("prompt")

    def test_timeout_raises(self, monkeypatch):
        monkeypatch.delenv("VOICE_DRAFTER_CLAUDE_TEXT_MODE", raising=False)
        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="claude", timeout=10)):
            with pytest.raises(LLMError, match="timeout"):
                claude_complete("prompt", timeout=10)

    def test_empty_result_raises(self, monkeypatch):
        monkeypatch.delenv("VOICE_DRAFTER_CLAUDE_TEXT_MODE", raising=False)
        with patch("subprocess.run", return_value=_proc(_claude_json_envelope("   "))):
            with pytest.raises(LLMError, match="empty"):
                claude_complete("prompt")

    def test_is_error_true_raises(self, monkeypatch):
        monkeypatch.delenv("VOICE_DRAFTER_CLAUDE_TEXT_MODE", raising=False)
        payload = json.dumps({"result": "oops", "is_error": True})
        with patch("subprocess.run", return_value=_proc(payload)):
            with pytest.raises(LLMError, match="is_error=true"):
                claude_complete("prompt")

    def test_non_json_envelope_raises(self, monkeypatch):
        monkeypatch.delenv("VOICE_DRAFTER_CLAUDE_TEXT_MODE", raising=False)
        with patch("subprocess.run", return_value=_proc("not json at all")):
            with pytest.raises(LLMError, match="non-JSON"):
                claude_complete("prompt")

    def test_system_prompt_appended(self, monkeypatch):
        monkeypatch.delenv("VOICE_DRAFTER_CLAUDE_TEXT_MODE", raising=False)
        with patch("subprocess.run", return_value=_proc(_claude_json_envelope("ok"))) as mock_run:
            claude_complete("prompt", system="be helpful")
        cmd = mock_run.call_args[0][0]
        assert "--append-system-prompt" in cmd
        idx = cmd.index("--append-system-prompt")
        assert cmd[idx + 1] == "be helpful"


# ---- codex_complete ----------------------------------------------------------

class TestCodexComplete:
    def _patch_codex(self, text: str, returncode: int = 0, side_effect=None):
        """Return a context manager that patches subprocess.run and writes text to the temp file."""
        import tempfile

        original_mkstemp = tempfile.mkstemp

        def fake_mkstemp(suffix=""):
            fd, path = original_mkstemp(suffix=suffix)
            # Write our test text so the file has content.
            if suffix == "-codex.txt":
                import os
                os.close(fd)
                Path(path).write_text(text)
                # Return a dummy fd that won't conflict.
                fd2, _ = tempfile.mkstemp()
                return fd2, path
            return fd, path

        return fake_mkstemp, returncode, side_effect

    def test_success(self, monkeypatch, tmp_path):
        """Mock both subprocess.run and the temp file output."""
        import os
        import tempfile as _tempfile

        output_text = "codex drafted post here"
        real_mkstemp = _tempfile.mkstemp

        def fake_mkstemp(suffix=""):
            fd, path = real_mkstemp(suffix=suffix)
            if suffix == "-codex.txt":
                os.close(fd)
                Path(path).write_text(output_text)
                fd2, _ = real_mkstemp()
                return fd2, path
            return fd, path

        with patch("tempfile.mkstemp", side_effect=fake_mkstemp):
            with patch("subprocess.run", return_value=_proc("", "", 0)):
                result = codex_complete("some prompt")
        assert result == output_text

    def test_nonzero_exit_raises(self, monkeypatch, tmp_path):
        import os
        import tempfile as _tempfile

        real_mkstemp = _tempfile.mkstemp

        def fake_mkstemp(suffix=""):
            fd, path = real_mkstemp(suffix=suffix)
            if suffix == "-codex.txt":
                os.close(fd)
                Path(path).write_text("")  # empty
                fd2, _ = real_mkstemp()
                return fd2, path
            return fd, path

        with patch("tempfile.mkstemp", side_effect=fake_mkstemp):
            with patch("subprocess.run", return_value=_proc("", "codex failed", returncode=1)):
                with pytest.raises(LLMError, match="codex exit 1"):
                    codex_complete("prompt")

    def test_timeout_raises(self):
        with patch("tempfile.mkstemp", return_value=(0, "/tmp/fake-codex.txt")):
            with patch("os.close"):
                with patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="codex", timeout=5)):
                    with pytest.raises(LLMError, match="timeout"):
                        codex_complete("prompt", timeout=5)


# ---- _strip_fences -----------------------------------------------------------

class TestStripFences:
    def test_no_fence_passthrough(self):
        assert _strip_fences("plain text") == "plain text"

    def test_json_fence_stripped(self):
        fenced = "```json\n{\"key\": \"val\"}\n```"
        assert _strip_fences(fenced) == '{"key": "val"}'

    def test_plain_fence_stripped(self):
        fenced = "```\nhello\n```"
        assert _strip_fences(fenced) == "hello"

    def test_no_trailing_fence(self):
        fenced = "```\nhello"
        result = _strip_fences(fenced)
        assert "hello" in result
