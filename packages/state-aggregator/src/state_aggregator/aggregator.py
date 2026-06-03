"""Aggregates state from multiple sources into a single FleetState snapshot."""

from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime

from .sources import (
    CalendarSource,
    EmailSource,
    InboxSource,
    ObsessiveLoopSource,
    RegistrySource,
    SessionsSource,
    StateSource,
    ToolsSource,
)
from .types import (
    AppSnapshot,
    CalendarEvent,
    EmailMessage,
    FleetState,
    InboxItem,
    SessionSnapshot,
    ToolSnapshot,
)

_UNMATCHED_ID = "_unmatched"


class Aggregator:
    """Composes apps, obsessive-loop runs, and inbox into a FleetState snapshot."""

    def __init__(self, sources: list[StateSource] | None = None) -> None:
        if sources is None:
            sources = [
                RegistrySource(),
                ObsessiveLoopSource(),
                InboxSource(),
                SessionsSource(),
                ToolsSource(),
                EmailSource(),
                CalendarSource(),
            ]
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

        sessions_src = self._source("sessions")
        raw_sessions: list[dict] = []
        if sessions_src is not None:
            raw_sessions = sessions_src.fetch().get("sessions", [])

        tools_src = self._source("tools")
        raw_tools: list[dict] = []
        if tools_src is not None:
            raw_tools = tools_src.fetch().get("tools", [])

        email_src = self._source("email")
        raw_email: list[dict] = []
        if email_src is not None:
            raw_email = email_src.fetch().get("email", [])

        calendar_src = self._source("calendar")
        raw_calendar: list[dict] = []
        if calendar_src is not None:
            raw_calendar = calendar_src.fetch().get("calendar", [])

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

        # --- sessions (all runtimes) ---
        sessions: list[SessionSnapshot] = []
        for s in raw_sessions:
            try:
                sessions.append(SessionSnapshot.model_validate(s))
            except Exception:
                pass

        # --- tools (registered MCP servers) ---
        tools: list[ToolSnapshot] = []
        for t in raw_tools:
            try:
                tools.append(ToolSnapshot.model_validate(t))
            except Exception:
                pass

        # --- email (recent inbox via the connector) ---
        email: list[EmailMessage] = []
        for m in raw_email:
            try:
                email.append(EmailMessage.model_validate(m))
            except Exception:
                pass

        # --- calendar (upcoming events via the connector) ---
        calendar: list[CalendarEvent] = []
        for ev in raw_calendar:
            try:
                calendar.append(CalendarEvent.model_validate(ev))
            except Exception:
                pass

        # --- summary counts ---
        by_state: dict[str, int] = defaultdict(int)
        blocked_count = 0
        for app in apps:
            by_state[app.state] += 1
            if app.state == "blocked":
                blocked_count += 1

        sessions_by_source: dict[str, int] = defaultdict(int)
        for s in sessions:
            sessions_by_source[s.source] += 1

        summary = {
            "total_apps": len(apps),
            "by_state": dict(by_state),
            "blocked_count": blocked_count,
            "total_runs": len(ol_runs),
            "unmatched_runs": len(unmatched_runs),
            "inbox_count": len(inbox_recent),
            "session_count": len(sessions),
            "sessions_by_source": dict(sessions_by_source),
            "tool_count": len(tools),
            "email_count": len(email),
            "email_unread": sum(1 for m in email if m.unread),
            "calendar_count": len(calendar),
        }

        return FleetState(
            generated_at=datetime.now(UTC),
            apps=apps,
            inbox_recent=inbox_recent,
            sessions=sessions,
            tools=tools,
            email=email,
            calendar=calendar,
            summary=summary,
        )
