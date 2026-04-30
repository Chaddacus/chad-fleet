"""Captain HTTP API (FastAPI on :8109).

Read-mostly endpoints serving the dashboard L1/L2/L3 views, plus the few
write endpoints the admiral needs to steer captains:

    GET  /health
    GET  /apps                        — list registered captain workspaces
    GET  /apps/{app_id}                — bundle of current state
    GET  /apps/{app_id}/roadmap        — the live roadmap
    GET  /apps/{app_id}/log            — captain log entries (tail by default)
    GET  /apps/{app_id}/scorecard      — fresh scorecard (with extras)
    GET  /apps/{app_id}/research       — cached app profile (read-only)
    POST /apps/{app_id}/note           — admiral writes a note for the captain
    POST /apps/{app_id}/replan         — force a replan (with a trigger)
    POST /apps/{app_id}/tick           — manually run one captain tick

Per-app workspaces live under CHAD_FLEET_APPS_DIR (default ~/.chad/fleet/apps).
The API discovers apps by scanning that directory for subdirs that contain
at least a roadmap.json or current_slice.json.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from chad_captain.apps_registry import load_registry
from chad_captain.extras import get_extras
from chad_captain.protocol import (
    AdmiralNote,
    AppWorkspace,
    fleet_base,
    list_unread_admiral_notes,
    read_captain_log,
    read_current_slice,
    read_roadmap,
    write_admiral_note,
)
from chad_captain.research import load_profile
from chad_captain.scorecard import score_repo
from chad_captain.validator import captain_tick

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Request / response shapes
# ---------------------------------------------------------------------------


class NoteIn(BaseModel):
    body: str
    note_id: str | None = None
    expects_response: bool = True


class ReplanIn(BaseModel):
    trigger: str = "manual"
    repo_path: str
    refresh_research: bool = False
    no_llm: bool = False


class TickIn(BaseModel):
    repo_path: str


# ---------------------------------------------------------------------------
# App discovery
# ---------------------------------------------------------------------------


def _list_app_ids() -> list[str]:
    base = fleet_base()
    if not base.exists():
        return []
    out: list[str] = []
    for child in sorted(base.iterdir()):
        if not child.is_dir():
            continue
        # Anything with at least one captain artifact is "registered".
        if any((child / name).exists() for name in (
            "roadmap.json", "current_slice.json", "captain_log.jsonl",
        )):
            out.append(child.name)
        elif (child / "admiral_notes").exists():
            out.append(child.name)
    return out


def _ws_or_404(app_id: str) -> AppWorkspace:
    if app_id not in _list_app_ids():
        # Allow reads against newly-created workspaces too.
        if not (fleet_base() / app_id).exists():
            raise HTTPException(404, f"unknown app: {app_id}")
    return AppWorkspace(app_id)


# ---------------------------------------------------------------------------
# Builder
# ---------------------------------------------------------------------------


def create_app() -> FastAPI:
    app = FastAPI(title="chad-captain API", version="0.1.0")

    @app.get("/health")
    def health() -> dict:
        return {
            "ok": True,
            "fleet_base": str(fleet_base()),
            "registered_apps": len(_list_app_ids()),
        }

    @app.get("/apps")
    def apps() -> dict:
        """List apps. Merges filesystem-discovered IDs with the registry so
        the dashboard sees registered-but-not-yet-scaffolded apps too."""
        fs_ids = _list_app_ids()
        reg = load_registry()
        reg_ids = {a.app_id for a in reg.apps}
        all_ids = sorted(set(fs_ids) | reg_ids)
        out: list[dict] = []
        for app_id in all_ids:
            entry = reg.by_id(app_id)
            out.append({
                "app_id": app_id,
                "name": entry.name if entry else app_id,
                "mode": entry.mode if entry else "autonomous",
                "repo_path": entry.repo_path if entry else None,
                "schedule_hour": entry.schedule_hour if entry else None,
            })
        return {"count": len(out), "apps": out}

    def _bundle(app_id: str, *, include_scorecard: bool = True) -> dict[str, Any]:
        """Build the full per-app state bundle. Optionally attaches the live
        scorecard when the app's repo_path is registered. Tolerates apps
        that exist in the registry but haven't been scaffolded yet — those
        return a bundle with empty roadmap/log/progress."""
        ws = AppWorkspace(app_id)
        roadmap = read_roadmap(ws) if ws.root.exists() else None
        current = read_current_slice(ws) if ws.root.exists() else None
        log = read_captain_log(ws, limit=20) if ws.root.exists() else []
        unread = [p.name for p in list_unread_admiral_notes(ws)] if ws.root.exists() else []
        progress_tail: list[dict] = []
        if ws.progress_path.exists():
            for line in ws.progress_path.read_text().splitlines()[-10:]:
                if line.strip():
                    try:
                        import json
                        progress_tail.append(json.loads(line))
                    except Exception:
                        continue
        reg = load_registry()
        entry = reg.by_id(app_id)
        bundle: dict[str, Any] = {
            "app_id": app_id,
            "name": entry.name if entry else app_id,
            "mode": entry.mode if entry else "autonomous",
            "repo_path": entry.repo_path if entry else None,
            "current_slice": current.model_dump(mode="json") if current else None,
            "roadmap": roadmap.model_dump(mode="json") if roadmap else None,
            "captain_log_tail": [e.model_dump(mode="json") for e in log],
            "progress_tail": progress_tail,
            "unread_admiral_notes": unread,
            "scorecard": None,
        }
        if include_scorecard and entry and entry.repo_path and Path(entry.repo_path).exists():
            try:
                sc = score_repo(entry.repo_path, extras=get_extras(app_id))
                bundle["scorecard"] = sc.model_dump(mode="json")
            except Exception as e:
                logger.warning("inline scorecard for %s failed: %s", app_id, e)
        return bundle

    @app.get("/apps/{app_id}")
    def app_state(app_id: str) -> dict[str, Any]:
        # 404 if neither the filesystem nor the registry knows this app.
        reg = load_registry()
        if app_id not in _list_app_ids() and reg.by_id(app_id) is None:
            raise HTTPException(404, f"unknown app: {app_id}")
        return _bundle(app_id, include_scorecard=True)

    @app.get("/fleet")
    def fleet() -> dict[str, Any]:
        """One-shot bundle for the dashboard L1 view. Returns every registered
        app with its full state + scorecard so the dashboard can render
        without N+1 fetches."""
        reg = load_registry()
        fs_ids = _list_app_ids()
        all_ids = sorted({a.app_id for a in reg.apps} | set(fs_ids))
        bundles: list[dict[str, Any]] = []
        for app_id in all_ids:
            try:
                bundles.append(_bundle(app_id, include_scorecard=True))
            except HTTPException:
                continue
            except Exception as e:
                logger.warning("fleet bundle for %s failed: %s", app_id, e)
                bundles.append({"app_id": app_id, "error": str(e)})
        return {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "count": len(bundles),
            "apps": bundles,
        }

    @app.get("/apps/{app_id}/roadmap")
    def app_roadmap(app_id: str) -> dict:
        ws = _ws_or_404(app_id)
        rm = read_roadmap(ws)
        if rm is None:
            raise HTTPException(404, "no roadmap on file")
        return rm.model_dump(mode="json")

    @app.get("/apps/{app_id}/log")
    def app_log(app_id: str, limit: int = 50) -> dict:
        ws = _ws_or_404(app_id)
        entries = read_captain_log(ws, limit=limit)
        return {"count": len(entries),
                "entries": [e.model_dump(mode="json") for e in entries]}

    @app.get("/apps/{app_id}/scorecard")
    def app_scorecard(app_id: str, repo_path: str) -> dict:
        if not Path(repo_path).exists():
            raise HTTPException(400, f"repo_path does not exist: {repo_path}")
        sc = score_repo(repo_path, extras=get_extras(app_id))
        return sc.model_dump(mode="json")

    @app.get("/apps/{app_id}/research")
    def app_research(app_id: str) -> dict:
        ws = _ws_or_404(app_id)
        profile = load_profile(ws)
        if profile is None:
            raise HTTPException(404, "no cached research profile")
        return profile.model_dump(mode="json")

    @app.post("/apps/{app_id}/note")
    def app_note(app_id: str, payload: NoteIn) -> dict:
        ws = AppWorkspace(app_id)
        ws.ensure()
        from datetime import datetime, timezone
        note = AdmiralNote(
            note_id=payload.note_id or f"note-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}",
            app_id=app_id,
            body=payload.body,
            expects_response=payload.expects_response,
        )
        path = write_admiral_note(ws, note)
        return {"note_id": note.note_id, "path": str(path)}

    @app.post("/apps/{app_id}/replan")
    def app_replan(app_id: str, payload: ReplanIn) -> dict:
        from chad_captain.replanner import replan

        ws = AppWorkspace(app_id)
        ws.ensure()
        roadmap = replan(
            ws,
            payload.repo_path,
            trigger=payload.trigger,
            refresh_research=payload.refresh_research,
            use_llm=not payload.no_llm,
        )
        return roadmap.model_dump(mode="json")

    @app.post("/apps/{app_id}/tick")
    def app_tick(app_id: str, payload: TickIn) -> dict:
        ws = AppWorkspace(app_id)
        ws.ensure()
        status = captain_tick(ws, repo_path=payload.repo_path, auto_replan=True)
        return {"app_id": app_id, "status": status}

    return app


def main() -> None:
    """Run the API with uvicorn (blocking)."""
    import argparse

    import uvicorn

    parser = argparse.ArgumentParser(description="chad-captain API server")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8109)
    parser.add_argument("--reload", action="store_true")
    args = parser.parse_args()

    uvicorn.run(
        "chad_captain.api:create_app",
        host=args.host,
        port=args.port,
        reload=args.reload,
        factory=True,
    )


if __name__ == "__main__":
    main()


__all__ = ["create_app", "main"]
