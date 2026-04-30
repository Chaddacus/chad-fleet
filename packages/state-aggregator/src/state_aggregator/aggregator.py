"""Aggregates state from multiple sources into a single FleetState snapshot."""

from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime

from .sources import InboxSource, ObsessiveLoopSource, RegistrySource, StateSource
from .types import AppSnapshot, FleetState, InboxItem

_UNMATCHED_ID = "_unmatched"


class Aggregator:
    """Composes apps, obsessive-loop runs, and inbox into a FleetState snapshot."""

    def __init__(self, sources: list[StateSource] | None = None) -> None:
        if sources is None:
            sources = [RegistrySource(), ObsessiveLoopSource(), InboxSource()]
        self._sources = sources

    def _source(self, name: str) -> StateSource | None:
        for s in self._sources:
            if s.name == name:
                return s
        return None

    def snapshot(self) -> FleetState:
        # --- fetch from each source ---
        registry_src = self._source("tracked-app-registry")
        ol_src = self._source("obsessive-loop")
        inbox_src = self._source("notifier-inbox")

        raw_apps: list[dict] = []
        if registry_src is not None:
            raw_apps = registry_src.fetch().get("apps", [])

        ol_runs: list[dict] = []
        if ol_src is not None:
            ol_runs = ol_src.fetch().get("runs", [])

        raw_inbox_items: list[dict] = []
        if inbox_src is not None:
            raw_inbox_items = inbox_src.fetch().get("items", [])

        # --- pair runs to apps by repo_path ---
        # Build index: repo_path -> app_id
        repo_to_app: dict[str, str] = {}
        for app in raw_apps:
            rp = app.get("repo_path")
            if rp:
                repo_to_app[rp] = app["id"]

        # Collect runs per app_id; unmatched go to _UNMATCHED_ID
        runs_by_app: dict[str, list[dict]] = defaultdict(list)
        for run in ol_runs:
            run_meta = run.get("run_meta") or {}
            repo_path = (
                run.get("repo_path")
                or run.get("repo")
                or run_meta.get("repo_path")
                or run_meta.get("repo")
            )
            if repo_path and repo_path in repo_to_app:
                app_id = repo_to_app[repo_path]
            else:
                app_id = _UNMATCHED_ID
            runs_by_app[app_id].append(run)

        # --- build AppSnapshot list ---
        apps: list[AppSnapshot] = []
        for raw in raw_apps:
            app_id = raw["id"]
            app_runs = runs_by_app.get(app_id, [])

            # Latest baseline from runs
            baseline: dict | None = None
            for run in reversed(app_runs):
                b = run.get("baseline") or run.get("baseline_scorecard")
                if b:
                    baseline = b
                    break

            snapshot = AppSnapshot(
                id=app_id,
                name=raw.get("name", app_id),
                state=raw.get("state", "unknown"),
                mode=raw.get("mode", ""),
                cadence=raw.get("cadence", ""),
                owner_brand=raw.get("owner_brand", ""),
                last_progress_at=raw.get("last_progress_at", datetime.now(UTC)),
                blocked_reason=raw.get("blocked_reason"),
                obsessive_loop_runs=app_runs,
                baseline=baseline,
                metadata=raw.get("metadata", {}),
            )
            apps.append(snapshot)

        # Synthetic _unmatched app for runs with no registry match
        unmatched_runs = runs_by_app.get(_UNMATCHED_ID, [])
        if unmatched_runs:
            apps.append(
                AppSnapshot(
                    id=_UNMATCHED_ID,
                    name="(unmatched)",
                    state="unknown",
                    mode="",
                    cadence="",
                    owner_brand="",
                    last_progress_at=datetime.now(UTC),
                    obsessive_loop_runs=unmatched_runs,
                )
            )

        # --- inbox items ---
        inbox_recent: list[InboxItem] = []
        for item in raw_inbox_items:
            try:
                inbox_recent.append(InboxItem.model_validate(item))
            except Exception:
                pass

        # --- summary counts ---
        by_state: dict[str, int] = defaultdict(int)
        blocked_count = 0
        for app in apps:
            by_state[app.state] += 1
            if app.state == "blocked":
                blocked_count += 1

        summary = {
            "total_apps": len(apps),
            "by_state": dict(by_state),
            "blocked_count": blocked_count,
            "total_runs": len(ol_runs),
            "unmatched_runs": len(unmatched_runs),
            "inbox_count": len(inbox_recent),
        }

        return FleetState(
            generated_at=datetime.now(UTC),
            apps=apps,
            inbox_recent=inbox_recent,
            summary=summary,
        )
