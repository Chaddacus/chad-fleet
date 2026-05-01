"""Human-readable session summary for one app.

Aggregates captain log + feature backlog + scorecard into a digestible
"what has the captain done for me" report. Not LLM-generated — templated
narrative built from structured event counts so it's cheap, deterministic,
and runs inline on every dashboard refresh.

Surfaced via:
  - GET /apps/{id}/summary?since=24h   (FastAPI)
  - chad-captain summary --app X [--since 24h] [--json]   (CLI)
  - Dashboard L2 "Session" panel
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Iterable

from pydantic import BaseModel, Field

from chad_captain.protocol import (
    AppWorkspace,
    CaptainLogEntry,
    read_captain_log,
    read_feature_backlog,
)


class ShippedPR(BaseModel):
    pr_url: str
    title: str
    merged_at: str | None = None
    backlog_item_ids: list[str] = Field(default_factory=list)


class ShippedFeature(BaseModel):
    id: str
    title: str
    pr_url: str | None = None
    shipped_at: str | None = None


class SessionSummary(BaseModel):
    app_id: str
    window_start: str  # ISO
    window_end: str    # ISO
    window_label: str  # e.g. "last 24h", "all time"
    prs_merged: list[ShippedPR] = Field(default_factory=list)
    features_shipped: list[ShippedFeature] = Field(default_factory=list)
    slices_total: int = 0
    slices_accepted: int = 0
    slices_soft_accepted: int = 0
    slices_rejected: int = 0
    escalations: int = 0
    saturation_events: int = 0
    circuit_breaker_trips: int = 0
    admiral_notes_received: int = 0
    rubric_delta_pp: float | None = None  # cumulative across the window
    narrative: str = ""           # one-paragraph plain-English summary
    headline: str = ""            # one-line headline


def _parse_iso(s: str) -> datetime | None:
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        return None


def _parse_window(window: str) -> timedelta | None:
    """Accept '24h', '7d', '30m', 'all'. Returns None for 'all'."""
    w = (window or "24h").strip().lower()
    if w == "all":
        return None
    if w.endswith("h"):
        return timedelta(hours=int(w[:-1]))
    if w.endswith("d"):
        return timedelta(days=int(w[:-1]))
    if w.endswith("m"):
        return timedelta(minutes=int(w[:-1]))
    raise ValueError(f"unknown window: {window!r}")


def _entries_in_window(
    log: Iterable[CaptainLogEntry], *, start: datetime,
) -> list[CaptainLogEntry]:
    out: list[CaptainLogEntry] = []
    for e in log:
        ts = _parse_iso(e.ts)
        if ts is not None and ts >= start:
            out.append(e)
    return out


def build_session_summary(
    ws: AppWorkspace, *, window: str = "24h",
) -> SessionSummary:
    """Build a one-shot summary of the captain's work for ``ws`` over
    the given time window. Pure read — no side effects."""
    delta = _parse_window(window)
    now = datetime.now(timezone.utc)
    if delta is None:
        # "all time" — start = epoch
        start = datetime.fromtimestamp(0, tz=timezone.utc)
        window_label = "all time"
    else:
        start = now - delta
        window_label = f"last {window}"

    log = read_captain_log(ws, limit=None) or []
    in_window = _entries_in_window(log, start=start)

    # PRs merged in window — pair pull_request_opened with pull_request_merged
    pr_open_by_url: dict[str, CaptainLogEntry] = {}
    prs: list[ShippedPR] = []
    for e in in_window:
        if e.kind == "pull_request_opened":
            url = (e.references or {}).get("pr_url") or ""
            if url and "captain self-merged" not in (e.rationale or ""):
                pr_open_by_url[url] = e
        elif e.kind == "pull_request_merged":
            url = (e.references or {}).get("pr_url") or ""
            if not url:
                continue
            opened = pr_open_by_url.get(url)
            title = ""
            if opened:
                # rationale is "PR opened: <url>" — we want the actual PR
                # title which isn't stored locally; fall back to the
                # roadmap_complete event preceding the open. For now use
                # the URL tail or the stored title if any.
                title = (opened.references or {}).get("pr_title") or ""
            prs.append(ShippedPR(
                pr_url=url, title=title or _pr_url_label(url),
                merged_at=e.ts,
            ))

    # Features shipped in window — read backlog and filter by shipped_at
    backlog = read_feature_backlog(ws)
    features: list[ShippedFeature] = []
    for it in backlog.items:
        if it.status != "shipped":
            continue
        ts = _parse_iso(it.shipped_at or "") or _parse_iso(it.created_at)
        if ts is not None and ts >= start:
            features.append(ShippedFeature(
                id=it.id, title=it.title, pr_url=it.shipped_in,
                shipped_at=it.shipped_at,
            ))
    # Cross-link feature ids onto PRs by shipped_in URL
    feature_by_url: dict[str, list[str]] = {}
    for f in features:
        if f.pr_url:
            feature_by_url.setdefault(f.pr_url, []).append(f.id)
    for pr in prs:
        pr.backlog_item_ids = feature_by_url.get(pr.pr_url, [])

    # Slice verdicts
    accepted = soft = rejected = 0
    cumulative_delta = 0.0
    delta_seen = False
    for e in in_window:
        if e.kind != "validate":
            continue
        if e.rubric_delta_pp is not None:
            cumulative_delta += e.rubric_delta_pp
            delta_seen = True
        if e.verdict == "accept":
            accepted += 1
        elif e.verdict == "soft_accept":
            soft += 1
        elif e.verdict in ("reject_retry", "reject_hard", "revert"):
            rejected += 1

    escalations = sum(1 for e in in_window if e.kind == "escalation_raised")
    saturation_events = sum(
        1 for e in in_window
        if e.kind == "escalation_raised"
        and (e.references or {}).get("event") == "backlog_saturated"
    )
    cb_trips = sum(
        1 for e in in_window
        if e.kind == "escalation_raised"
        and (e.references or {}).get("event") == "circuit_breaker_tripped"
    )
    notes = sum(1 for e in in_window if e.kind == "note_received")

    summary = SessionSummary(
        app_id=ws.app_id,
        window_start=start.isoformat(),
        window_end=now.isoformat(),
        window_label=window_label,
        prs_merged=prs,
        features_shipped=features,
        slices_total=accepted + soft + rejected,
        slices_accepted=accepted,
        slices_soft_accepted=soft,
        slices_rejected=rejected,
        escalations=escalations,
        saturation_events=saturation_events,
        circuit_breaker_trips=cb_trips,
        admiral_notes_received=notes,
        rubric_delta_pp=round(cumulative_delta, 2) if delta_seen else None,
    )
    summary.headline = _build_headline(summary)
    summary.narrative = _build_narrative(summary)
    return summary


def _pr_url_label(url: str) -> str:
    # Last path segment, e.g. ".../pull/150" → "PR #150"
    if "/pull/" in url:
        n = url.rsplit("/", 1)[-1]
        return f"PR #{n}"
    return url


def _build_headline(s: SessionSummary) -> str:
    """One-line summary suitable for a dashboard chip or terminal echo."""
    if s.slices_total == 0 and not s.prs_merged:
        if s.saturation_events:
            return f"Awaiting direction — backlog saturated ({s.window_label})"
        return f"No activity in {s.window_label}"
    parts: list[str] = []
    if s.prs_merged:
        parts.append(f"{len(s.prs_merged)} PR{'s' if len(s.prs_merged) != 1 else ''} merged")
    if s.features_shipped:
        parts.append(
            f"{len(s.features_shipped)} feature{'s' if len(s.features_shipped) != 1 else ''} shipped"
        )
    if s.slices_total:
        parts.append(f"{s.slices_total} slices")
    if s.rubric_delta_pp is not None and abs(s.rubric_delta_pp) >= 0.5:
        parts.append(f"rubric {s.rubric_delta_pp:+.1f}pp")
    return f"{' · '.join(parts)} ({s.window_label})"


def _build_narrative(s: SessionSummary) -> str:
    """Two-or-three-sentence plain-English narrative. Templated, no LLM."""
    if s.slices_total == 0 and not s.prs_merged:
        if s.saturation_events:
            return (
                f"Captain has shipped every feature on the backlog and is "
                f"waiting for direction. Run `chad-captain ideate` to "
                f"refill the backlog or send an admiral note to steer the "
                f"next cycle."
            )
        return f"No captain activity in the {s.window_label} window."

    sentences: list[str] = []
    if s.features_shipped:
        titles = ", ".join(
            f'"{f.title}"' for f in s.features_shipped[:3]
        )
        more = (
            f" plus {len(s.features_shipped) - 3} more"
            if len(s.features_shipped) > 3 else ""
        )
        sentences.append(
            f"Shipped {len(s.features_shipped)} feature"
            f"{'s' if len(s.features_shipped) != 1 else ''}: {titles}{more}."
        )
    if s.prs_merged:
        sentences.append(
            f"Merged {len(s.prs_merged)} pull request"
            f"{'s' if len(s.prs_merged) != 1 else ''} via captain self-merge."
        )
    if s.slices_total:
        ratio = (
            f"{s.slices_accepted} accept · "
            f"{s.slices_soft_accepted} soft_accept · "
            f"{s.slices_rejected} reject"
        )
        sentences.append(f"Ran {s.slices_total} slices ({ratio}).")
    if s.rubric_delta_pp is not None:
        if abs(s.rubric_delta_pp) >= 0.5:
            direction = "improved" if s.rubric_delta_pp > 0 else "regressed"
            sentences.append(
                f"Cumulative rubric {direction} {abs(s.rubric_delta_pp):.1f}pp."
            )
    if s.saturation_events:
        sentences.append(
            f"Hit backlog saturation {s.saturation_events}x — captain "
            f"awaiting direction."
        )
    elif s.circuit_breaker_trips:
        sentences.append(
            f"Tripped the low-yield circuit breaker {s.circuit_breaker_trips}x."
        )
    if s.admiral_notes_received:
        sentences.append(
            f"Received {s.admiral_notes_received} admiral note"
            f"{'s' if s.admiral_notes_received != 1 else ''} for steering."
        )
    return " ".join(sentences)


__all__ = [
    "ShippedPR",
    "ShippedFeature",
    "SessionSummary",
    "build_session_summary",
]
