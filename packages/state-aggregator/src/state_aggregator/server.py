"""FastAPI HTTP service exposing the fleet state snapshot."""

from __future__ import annotations

import os

from fastapi import FastAPI, HTTPException

from .aggregator import Aggregator
from .types import AppSnapshot, FleetState

app = FastAPI(title="state-aggregator", version="0.0.0")

_aggregator = Aggregator()


@app.get("/api/health")
def health() -> dict:
    return {"ok": True}


@app.get("/api/state", response_model=FleetState)
def get_state() -> FleetState:
    return _aggregator.snapshot()


@app.get("/api/apps/{app_id}", response_model=AppSnapshot)
def get_app(app_id: str) -> AppSnapshot:
    state = _aggregator.snapshot()
    for a in state.apps:
        if a.id == app_id:
            return a
    raise HTTPException(status_code=404, detail=f"App '{app_id}' not found")


def get_port() -> int:
    return int(os.environ.get("CHAD_AGGREGATOR_PORT", "8106"))
