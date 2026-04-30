"""State source protocol and concrete implementations."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Protocol, runtime_checkable

from .types import InboxItem

_DEFAULT_REGISTRY_BASE = Path.home() / ".chad" / "fleet" / "registry"
_DEFAULT_OL_STATE_ROOT = Path.home() / ".claude" / "state" / "obsessive-loop"
_DEFAULT_INBOX_PATH = Path.home() / ".chad" / "notifier" / "inbox.jsonl"


@runtime_checkable
class StateSource(Protocol):
    name: str

    def fetch(self) -> dict: ...


class RegistrySource:
    """Reads tracked apps from the tracked-app-registry package."""

    name = "tracked-app-registry"

    def __init__(self, registry_dir: Path | None = None) -> None:
        self._registry_dir = registry_dir

    def fetch(self) -> dict:
        """Returns {"apps": [TrackedApp.model_dump()...]}."""
        from tracked_app_registry import Registry

        kwargs: dict = {}
        if self._registry_dir is not None:
            kwargs["view_path"] = self._registry_dir / "apps.json"
            kwargs["events_path"] = self._registry_dir / "events.jsonl"
            registry = Registry(**kwargs)
        else:
            env_dir = os.environ.get("CHAD_FLEET_REGISTRY_DIR")
            if env_dir:
                d = Path(env_dir)
                registry = Registry(
                    view_path=d / "apps.json",
                    events_path=d / "events.jsonl",
                )
            else:
                registry = Registry()

        apps = registry.list()
        return {"apps": [a.model_dump(mode="json") for a in apps]}


class ObsessiveLoopSource:
    """Reads obsessive-loop run states from the state directory."""

    name = "obsessive-loop"

    def __init__(self, state_root: Path | None = None) -> None:
        self._custom_root = state_root is not None
        self._state_root = state_root or _DEFAULT_OL_STATE_ROOT

    def _read_run(self, run_dir: Path) -> dict | None:
        """Read a single run directory; returns a summary dict or None on failure."""
        run_id = run_dir.name

        # Try obsessive_slice_state.py first (canonical read path).
        # Skip when a custom state_root was provided (e.g. in tests) because the
        # script queries the real state directory and will return exit 0 with an
        # empty payload for non-existent run IDs, shadowing local file fallbacks.
        script = Path.home() / ".claude" / "bin" / "obsessive_slice_state.py"
        if script.exists() and not self._custom_root:
            try:
                result = subprocess.run(
                    ["python3", str(script), "summary", "--run", run_id],
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
                if result.returncode == 0 and result.stdout.strip():
                    data = json.loads(result.stdout)
                    return data
            except (subprocess.TimeoutExpired, json.JSONDecodeError, OSError):
                pass

        # Fall back to summary.json
        summary_path = run_dir / "summary.json"
        if summary_path.exists():
            try:
                with summary_path.open() as f:
                    data = json.load(f)
                data.setdefault("run_id", run_id)
                return data
            except (json.JSONDecodeError, OSError):
                pass

        # Fall back to state.jsonl: derive a minimal summary from the last event
        state_jsonl = run_dir / "state.jsonl"
        if state_jsonl.exists():
            try:
                lines = state_jsonl.read_text().splitlines()
                events = []
                for line in lines:
                    line = line.strip()
                    if line:
                        try:
                            events.append(json.loads(line))
                        except json.JSONDecodeError:
                            pass
                if events:
                    last = events[-1]
                    # Look for repo path across multiple possible keys
                    repo_path = (
                        last.get("repo_path")
                        or last.get("repo")
                        or next(
                            (e.get("repo_path") or e.get("repo") for e in events
                             if e.get("repo_path") or e.get("repo")),
                            None,
                        )
                    )
                    return {
                        "run_id": run_id,
                        "status": last.get("state", "unknown"),
                        "repo_path": repo_path,
                        "branch": last.get("branch") or next(
                            (e.get("branch") for e in events if e.get("branch")),
                            None,
                        ),
                        "slice_count": len(events),
                    }
            except OSError:
                pass

        # Minimal stub: at least record the run_id
        baseline_path = run_dir / "baseline-scorecard.json"
        if not baseline_path.exists():
            baseline_path = run_dir / "baseline.json"

        baseline: dict | None = None
        if baseline_path.exists():
            try:
                with baseline_path.open() as f:
                    baseline = json.load(f)
            except (json.JSONDecodeError, OSError):
                pass

        stub: dict = {"run_id": run_id}
        if baseline:
            stub["baseline"] = baseline
        return stub

    def fetch(self) -> dict:
        """Returns {"runs": [...]}."""
        root = self._state_root
        if not root.exists():
            return {"runs": []}

        runs = []
        for entry in sorted(root.iterdir()):
            if entry.is_dir():
                run_data = self._read_run(entry)
                if run_data is not None:
                    runs.append(run_data)

        return {"runs": runs}


class InboxSource:
    """Reads notification inbox from a JSONL file."""

    name = "notifier-inbox"

    def __init__(self, inbox_path: Path | None = None, last_n: int = 50) -> None:
        self._inbox_path = inbox_path
        self._last_n = last_n

    def _resolve_path(self) -> Path:
        if self._inbox_path is not None:
            return self._inbox_path
        env = os.environ.get("CHAD_NOTIFIER_INBOX_PATH")
        if env:
            return Path(env)
        return _DEFAULT_INBOX_PATH

    def fetch(self) -> dict:
        """Returns {"items": [InboxItem...]}."""
        path = self._resolve_path()
        if not path.exists():
            return {"items": []}

        lines = []
        try:
            lines = path.read_text().splitlines()
        except OSError:
            return {"items": []}

        # Take last N non-empty lines
        raw_lines = [l.strip() for l in lines if l.strip()]
        tail = raw_lines[-self._last_n :]

        items = []
        for line in tail:
            try:
                record = json.loads(line)
                item = InboxItem.model_validate(record)
                items.append(item.model_dump(mode="json"))
            except (json.JSONDecodeError, Exception):
                pass

        return {"items": items}


class MemorySource:
    """Stub for future omni-mem integration. Returns empty list."""

    name = "omni-mem"

    def fetch(self) -> dict:
        return {"memories": []}
