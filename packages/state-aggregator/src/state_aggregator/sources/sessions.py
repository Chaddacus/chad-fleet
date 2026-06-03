"""Unified agent-sessions source (Claude, auto-runtime captain tracks, Codex)."""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path

_DEFAULT_CLAUDE_PROJECTS = Path.home() / ".claude" / "projects"
_DEFAULT_AUTORUNTIME_ROOT = Path.home() / ".claude" / "state" / "autonomy"
_DEFAULT_CODEX_INDEX = Path.home() / ".Codex" / "session_index.jsonl"


def _as_utc(dt: datetime) -> datetime:
    """Normalize to tz-aware UTC so heterogeneous sources sort together."""
    return dt.astimezone(UTC) if dt.tzinfo else dt.replace(tzinfo=UTC)


class SessionsSource:
    """Normalizes agent sessions across the runtimes Chad owns into one list:
      - Claude     — `~/.claude/projects/<encoded-cwd>/<uuid>.jsonl`
      - auto-runtime — `~/.claude/state/autonomy/<track>/objective.state.json`
      - Codex      — `~/.Codex/session_index.jsonl`
    Cheap by construction: stat-only to rank by recency, content read only for the
    last_n most recent Claude files (to recover a real cwd/title). Third-party app
    sessions (e.g. a foreign sqlite schema) would be a later adapter."""

    name = "sessions"

    def __init__(
        self,
        claude_projects: Path | None = None,
        autoruntime_root: Path | None = None,
        codex_index: Path | None = None,
        last_n: int = 50,
    ) -> None:
        self._claude_projects = claude_projects
        self._autoruntime_root = autoruntime_root
        self._codex_index = codex_index
        self._last_n = last_n

    def _claude(self) -> list[dict]:
        root = self._claude_projects
        if root is None:
            env = os.environ.get("CHAD_CLAUDE_PROJECTS_DIR")
            root = Path(env) if env else _DEFAULT_CLAUDE_PROJECTS
        if not root.exists():
            return []
        # Rank all session files by mtime (stat-only), then read just the newest.
        files = sorted(root.glob("*/*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)
        out: list[dict] = []
        for f in files[: self._last_n]:
            cwd = self._read_claude_cwd(f)
            out.append({
                "id": f.stem,
                "source": "claude",
                "title": cwd or f.parent.name.lstrip("-").replace("-", "/"),
                "cwd": cwd,
                "updated_at": _as_utc(datetime.fromtimestamp(f.stat().st_mtime)),
            })
        return out

    @staticmethod
    def _read_claude_cwd(f: Path) -> str | None:
        try:
            with f.open() as fh:
                first = fh.readline()
            return json.loads(first).get("cwd")
        except (OSError, json.JSONDecodeError, ValueError):
            return None

    def _tracks(self) -> list[dict]:
        root = self._autoruntime_root
        if root is None:
            env = os.environ.get("CHAD_AUTORUNTIME_ROOT")
            root = Path(env) if env else _DEFAULT_AUTORUNTIME_ROOT
        if not root.exists():
            return []
        out: list[dict] = []
        for state_path in root.glob("*/objective.state.json"):
            try:
                d = json.loads(state_path.read_text())
            except (OSError, json.JSONDecodeError):
                continue
            ts = d.get("updated_at") or d.get("created_at")
            try:
                updated = _as_utc(datetime.fromisoformat(ts)) if ts else _as_utc(
                    datetime.fromtimestamp(state_path.stat().st_mtime))
            except ValueError:
                updated = _as_utc(datetime.fromtimestamp(state_path.stat().st_mtime))
            out.append({
                "id": d.get("track_id", state_path.parent.name),
                "source": "auto-runtime",
                "title": (d.get("task") or "(captain track)")[:120],
                "cwd": d.get("cwd"),
                "updated_at": updated,
                "status": d.get("phase") or d.get("state"),
            })
        return out

    def _codex(self) -> list[dict]:
        path = self._codex_index
        if path is None:
            env = os.environ.get("CHAD_CODEX_INDEX")
            path = Path(env) if env else _DEFAULT_CODEX_INDEX
        if not path.exists():
            return []
        out: list[dict] = []
        for line in path.read_text().splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
            except json.JSONDecodeError:
                continue
            raw = d.get("updated_at")
            updated = self._parse_codex_ts(raw)
            if updated is None:
                continue
            out.append({
                "id": str(d.get("id", "")),
                "source": "codex",
                "title": d.get("thread_name") or "(codex session)",
                "cwd": d.get("cwd"),
                "updated_at": updated,
            })
        return out

    @staticmethod
    def _parse_codex_ts(raw) -> datetime | None:
        if raw is None:
            return None
        if isinstance(raw, (int, float)):
            return _as_utc(datetime.fromtimestamp(raw))
        try:
            return _as_utc(datetime.fromisoformat(str(raw).replace("Z", "+00:00")))
        except ValueError:
            return None

    def fetch(self) -> dict:
        """Returns {"sessions": [...]} — most-recent across all runtimes, capped."""
        sessions = self._claude() + self._tracks() + self._codex()
        sessions.sort(key=lambda s: s["updated_at"], reverse=True)
        return {"sessions": sessions[: self._last_n]}
