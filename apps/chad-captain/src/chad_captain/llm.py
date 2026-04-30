"""Subprocess adapter for Pro/Max + ChatGPT Plus subscription CLIs.

Mirror of `servers/marketing/llm.py` from chad-agent — same shape, distinct env
prefix so the two projects don't fight over CLI overrides. Two providers, no
API keys:

- ``claude -p`` uses ``~/.claude/auth.json`` (Pro/Max OAuth)
- ``codex exec`` uses ``~/.codex/auth.json`` (ChatGPT Plus OAuth)

Public surface: ``claude_complete``, ``claude_json``, ``codex_complete``,
``LLMError``. All raise ``LLMError`` on any failure path.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

logger = logging.getLogger("chad-captain.llm")


def _resolve_bin(env_var: str, default_name: str, fallback_path: Path) -> str:
    val = os.environ.get(env_var)
    if val:
        return val
    found = shutil.which(default_name)
    if found:
        return found
    return str(fallback_path)


CLAUDE_BIN = _resolve_bin(
    "CHAD_CAPTAIN_CLAUDE_BIN", "claude", Path.home() / ".local" / "bin" / "claude"
)
CODEX_BIN = _resolve_bin(
    "CHAD_CAPTAIN_CODEX_BIN", "codex", Path.home() / ".npm-global" / "bin" / "codex"
)


class LLMError(RuntimeError):
    """Any failure mode from the subprocess adapter."""


def claude_complete(
    prompt: str,
    *,
    model: str = "opus",
    system: str | None = None,
    timeout: int = 90,
) -> str:
    """Run ``claude -p`` non-interactively and return the assistant's text."""
    text_mode = os.environ.get("CHAD_CAPTAIN_CLAUDE_TEXT_MODE", "").strip() == "1"
    cmd: list[str] = [CLAUDE_BIN, "-p", "--model", model]
    if not text_mode:
        cmd.extend(["--output-format", "json"])
    if system:
        cmd.extend(["--system-prompt", system])

    logger.debug("claude_complete: model=%s timeout=%d prompt_len=%d", model, timeout, len(prompt))
    try:
        proc = subprocess.run(
            cmd,
            input=prompt,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as e:
        raise LLMError(f"claude timeout after {timeout}s") from e

    if proc.returncode != 0:
        stderr = (proc.stderr or "").strip()[:500]
        raise LLMError(f"claude exit {proc.returncode}: {stderr}")

    if text_mode:
        text = (proc.stdout or "").strip()
    else:
        try:
            payload = json.loads(proc.stdout or "")
        except json.JSONDecodeError as e:
            raise LLMError(f"claude returned non-JSON: {(proc.stdout or '')[:300]}") from e
        if payload.get("is_error"):
            raise LLMError(f"claude is_error=true: {payload.get('result') or payload}")
        text = (payload.get("result") or "").strip()

    if not text:
        raise LLMError("claude returned empty result")
    return text


def claude_json(
    prompt: str,
    schema: dict,
    *,
    model: str = "haiku",
    system: str | None = None,
    timeout: int = 60,
) -> dict:
    """Call Claude expecting a JSON object conforming to ``schema``."""
    schema_str = json.dumps(schema, separators=(",", ":"))
    base_system = (
        f"{system}\n\n" if system else ""
    ) + (
        "Respond with valid JSON ONLY. No prose, no markdown fences, "
        "no explanation, no code blocks. The response must be a single "
        "JSON object conforming to this schema:\n" + schema_str
    )

    last_err: Exception | None = None
    for _attempt in range(2):
        try:
            text = claude_complete(prompt, model=model, system=base_system, timeout=timeout)
        except LLMError as e:
            last_err = e
            continue
        cleaned = _strip_fences(text)
        try:
            return json.loads(cleaned)
        except json.JSONDecodeError as e:
            last_err = e
            base_system = (
                "JSON ONLY. Your previous response was not valid JSON. "
                "Return a single JSON object with no surrounding text.\n"
                + base_system
            )
    raise LLMError(f"claude_json failed after retries: {last_err}")


def _strip_fences(text: str) -> str:
    s = text.strip()
    if not s.startswith("```"):
        return s
    s = s.split("\n", 1)[1] if "\n" in s else s[3:]
    if s.rstrip().endswith("```"):
        s = s.rstrip()[:-3]
    return s.strip()


def codex_complete(
    prompt: str,
    *,
    model: str | None = None,
    timeout: int = 180,
    cwd: str | None = None,
) -> str:
    """Run ``codex exec`` non-interactively and return the final assistant message."""
    out_fd, out_path = tempfile.mkstemp(suffix="-codex.txt")
    os.close(out_fd)
    try:
        cmd: list[str] = [
            CODEX_BIN,
            "exec",
            "--skip-git-repo-check",
            "--ephemeral",
            "--sandbox",
            "read-only",
            "--output-last-message",
            out_path,
            "--color",
            "never",
        ]
        if model:
            cmd.extend(["-m", model])
        cmd.append(prompt)

        logger.debug("codex_complete: model=%s timeout=%d prompt_len=%d", model, timeout, len(prompt))
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
                cwd=cwd,
            )
        except subprocess.TimeoutExpired as e:
            raise LLMError(f"codex timeout after {timeout}s") from e

        if proc.returncode != 0:
            stderr = (proc.stderr or "").strip()[:500]
            raise LLMError(f"codex exit {proc.returncode}: {stderr}")

        try:
            text = Path(out_path).read_text(encoding="utf-8").strip()
        except OSError as e:
            raise LLMError(f"codex output file unreadable: {e}") from e

        if not text:
            raise LLMError("codex returned empty output-last-message")
        return text
    finally:
        try:
            Path(out_path).unlink()
        except OSError:
            pass


__all__ = [
    "LLMError",
    "claude_complete",
    "claude_json",
    "codex_complete",
    "CLAUDE_BIN",
    "CODEX_BIN",
]
