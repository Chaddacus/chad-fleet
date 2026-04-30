"""Drive the dashboard chat submit and capture EVERYTHING.

Console messages, page errors, network failures, response bodies for /render.
Writes a structured log to ~/.playwright-mcp/console-<ts>.log and stdout summary.
"""

import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from playwright.sync_api import sync_playwright

LOG_DIR = Path.home() / ".playwright-mcp"
LOG_DIR.mkdir(exist_ok=True)
ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%S-%fZ")[:-4] + "Z"
log_path = LOG_DIR / f"console-{ts}.log"

events: list[dict] = []


def log(kind: str, **payload):
    rec = {"ts": datetime.now(timezone.utc).isoformat(), "kind": kind, **payload}
    events.append(rec)
    print(json.dumps(rec, default=str), flush=True)


with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    context = browser.new_context()
    page = context.new_page()

    page.on("console", lambda msg: log("console", level=msg.type, text=msg.text, url=msg.location.get("url", "")))
    page.on("pageerror", lambda exc: log("pageerror", message=str(exc)))
    page.on("requestfailed", lambda req: log("requestfailed", url=req.url, method=req.method, failure=req.failure))

    def on_response(resp):
        # Capture all responses; record body for /render specifically
        rec = {"url": resp.url, "status": resp.status, "method": resp.request.method}
        if "/render" in resp.url or "/api/" in resp.url:
            try:
                rec["body_preview"] = resp.text()[:1500]
            except Exception as exc:
                rec["body_error"] = str(exc)
        log("response", **rec)

    page.on("response", on_response)

    log("phase", message="navigating to http://localhost:3000")
    try:
        page.goto("http://localhost:3000", wait_until="networkidle", timeout=15000)
    except Exception as exc:
        log("nav_error", message=str(exc))

    # Wait briefly for the status-bar fetch to /api/state
    page.wait_for_timeout(2000)

    log("phase", message="filling chat input + clicking Send")
    try:
        page.fill('[data-testid="chat-input"]', "show one badge: status active")
        page.locator('button[type="submit"]').click()
    except Exception as exc:
        log("interaction_error", message=str(exc))

    # Wait up to 30s for either a final view or an error
    log("phase", message="waiting up to 30s for response")
    try:
        page.wait_for_selector(
            '[data-testid="final-view"], [data-testid="error-view"]',
            timeout=30000,
        )
    except Exception as exc:
        log("wait_timeout", message=str(exc))

    # Snapshot current visible state
    try:
        if page.locator('[data-testid="final-view"]').count() > 0:
            log("result", status="final", text=page.locator('[data-testid="final-view"]').inner_text()[:500])
        elif page.locator('[data-testid="error-view"]').count() > 0:
            log("result", status="error", text=page.locator('[data-testid="error-view"]').inner_text()[:500])
        else:
            log("result", status="neither", text="no final or error testid present")
    except Exception as exc:
        log("snapshot_error", message=str(exc))

    browser.close()

log_path.write_text("\n".join(json.dumps(e, default=str) for e in events))
print(f"\n=== wrote {log_path} ({len(events)} events) ===", file=sys.stderr)
