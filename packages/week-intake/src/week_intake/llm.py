"""Subprocess adapter for ``claude -p`` (Pro/Max OAuth) — JSON-only mode.

Mirrors the shape of voice-drafter/llm.py and chad-captain/llm.py. We don't
import either because they live in different packages with their own
release cadences; copy-paste of ~60 LOC is preferred over a premature
shared-llm package. If a 4th copy appears, hoist this to a shared
package.

Public surface: ``claude_json`` and ``LLMError``. No API keys are used —
the CLI consumes ``~/.claude/auth.json`` directly.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
from pathlib import Path

logger = logging.getLogger("week-intake.llm")


def _resolve_bin(env_var: str, default_name: str, fallback_path: Path) -> str:
    val = os.environ.get(env_var)
    if val:
        return val
    found = shutil.which(default_name)
    if found:
        return found
    return str(fallback_path)


CLAUDE_BIN = _resolve_bin(
    "WEEK_INTAKE_CLAUDE_BIN", "claude", Path.home() / ".local" / "bin" / "claude"
)


class LLMError(RuntimeError):
    """Any failure from the subprocess adapter."""


def _strip_fences(text: str) -> str:
    s = text.strip()
    if not s.startswith("```"):
        return s
    s = s.split("\n", 1)[1] if "\n" in s else s[3:]
    if s.rstrip().endswith("```"):
        s = s.rstrip()[:-3]
    return s.strip()


def claude_complete(
    prompt: str,
    *,
    model: str = "haiku",
    system: str | None = None,
    timeout: int = 90,
) -> str:
    """Run ``claude -p`` non-interactively and return the assistant's text."""
    cmd: list[str] = [CLAUDE_BIN, "-p", "--model", model, "--output-format", "json"]
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
    except (FileNotFoundError, PermissionError, OSError) as e:
        raise LLMError(
            f"claude binary not runnable at {CLAUDE_BIN!r}: {e}. "
            "Set WEEK_INTAKE_CLAUDE_BIN or install `claude` on PATH."
        ) from e

    if proc.returncode != 0:
        stderr = (proc.stderr or "").strip()[:500]
        raise LLMError(f"claude exit {proc.returncode}: {stderr}")

    try:
        payload = json.loads(proc.stdout or "")
    except json.JSONDecodeError as e:
        raise LLMError(f"claude returned non-JSON envelope: {(proc.stdout or '')[:300]}") from e

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
    timeout: int = 90,
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


__all__ = ["LLMError", "claude_complete", "claude_json"]
