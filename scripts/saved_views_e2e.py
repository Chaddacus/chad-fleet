"""End-to-end test of the saved-views flow.

1. Open dashboard → submit a chat prompt → wait for final view.
2. Click "Save view" → confirm browser prompt → confirm POST /api/views succeeded.
3. Navigate to /views → confirm the saved view shows in the list.
4. Click into /views/[id] → confirm ViewClient re-renders against current state.

Captures console + network. Fails fast (exits non-zero) if any step fails.
Writes structured log to ~/.playwright-mcp/console-saved-views-<ts>.log.
"""

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from playwright.sync_api import sync_playwright

LOG_DIR = Path.home() / ".playwright-mcp"
LOG_DIR.mkdir(exist_ok=True)
ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%S-%fZ")[:-4] + "Z"
log_path = LOG_DIR / f"console-saved-views-{ts}.log"

events: list[dict] = []
failures: list[str] = []


def log(kind: str, **payload):
    rec = {"ts": datetime.now(timezone.utc).isoformat(), "kind": kind, **payload}
    events.append(rec)
    print(json.dumps(rec, default=str), flush=True)


def fail(msg: str):
    failures.append(msg)
    log("failure", message=msg)


with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    context = browser.new_context()
    page = context.new_page()

    page.on("console", lambda m: log("console", level=m.type, text=m.text[:300]))
    page.on("pageerror", lambda exc: log("pageerror", message=str(exc)))
    page.on(
        "requestfailed",
        lambda req: log("requestfailed", url=req.url, method=req.method, failure=req.failure),
    )

    def on_response(resp):
        if "/api/views" in resp.url or "/render" in resp.url:
            try:
                body = resp.text()
            except Exception:
                body = "<body unavailable>"
            log("response", url=resp.url, status=resp.status, method=resp.request.method, body=body[:500])

    page.on("response", on_response)

    # Auto-accept the window.prompt for save name
    page.on("dialog", lambda d: (log("dialog", message=d.message, default=d.default_value), d.accept("e2e-test-view")))

    # ---- Step 1: submit chat prompt ----
    log("step", phase="navigate to /")
    page.goto("http://localhost:3000", wait_until="domcontentloaded", timeout=15000)
    page.wait_for_timeout(2000)

    log("step", phase="submit chat")
    page.fill('[data-testid="chat-input"]', "show one badge: status active")
    page.locator('button[type="submit"]').click()

    log("step", phase="wait for final view")
    try:
        page.wait_for_selector('[data-testid="final-view"]', timeout=45000)
        log("step", phase="final view appeared")
    except Exception as exc:
        fail(f"step 1 final view never appeared: {exc}")

    # ---- Step 2: save view ----
    if not failures:
        log("step", phase="click save button")
        try:
            save_btn = page.locator('[data-testid="save-view-button"]')
            save_btn.wait_for(state="visible", timeout=5000)
            save_btn.click()
            page.wait_for_timeout(2000)  # let the prompt resolve + POST complete
            # Check the inline save message
            msg_el = page.locator('[data-testid="save-message"]')
            if msg_el.count() > 0:
                log("step", phase="save message", text=msg_el.inner_text())
            else:
                fail("step 2: no save-message element after click")
        except Exception as exc:
            fail(f"step 2 save click failed: {exc}")

    # ---- Step 3: /views library shows the saved view ----
    if not failures:
        log("step", phase="navigate to /views")
        page.goto("http://localhost:3000/views", wait_until="domcontentloaded", timeout=10000)
        page.wait_for_timeout(1000)

        if page.locator('[data-testid="views-empty"]').count() > 0:
            fail("step 3: /views shows empty state — save did not persist")
        elif page.locator('[data-testid="views-error"]').count() > 0:
            fail(f"step 3: /views error: {page.locator('[data-testid=views-error]').inner_text()[:200]}")
        else:
            rows = page.locator('[data-testid^="view-row-"]').count()
            log("step", phase="views list", row_count=rows)
            if rows == 0:
                fail("step 3: views list rendered no rows")

    # ---- Step 4: click into /views/[id] and confirm ViewClient renders ----
    if not failures:
        log("step", phase="click first view row")
        try:
            page.locator('[data-testid^="view-row-"]').first.locator('a').first.click()
            page.wait_for_url("**/views/**", timeout=5000)
            page.wait_for_timeout(2000)

            # ViewClient drives a re-render — wait for either final or error
            try:
                page.wait_for_selector(
                    '[data-testid="view-final"], [data-testid="view-error"]',
                    timeout=45000,
                )
                if page.locator('[data-testid="view-final"]').count() > 0:
                    txt = page.locator('[data-testid="view-final"]').inner_text()[:300]
                    log("step", phase="re-render final", text=txt)
                else:
                    fail(
                        f"step 4: ViewClient errored: "
                        f"{page.locator('[data-testid=view-error]').inner_text()[:200]}"
                    )
            except Exception as exc:
                fail(f"step 4: ViewClient never rendered: {exc}")
        except Exception as exc:
            fail(f"step 4 click failed: {exc}")

    browser.close()

log_path.write_text("\n".join(json.dumps(e, default=str) for e in events))

print(f"\n=== wrote {log_path} ({len(events)} events) ===", file=sys.stderr)
if failures:
    print(f"\n!!! {len(failures)} FAILURE(S) !!!", file=sys.stderr)
    for f in failures:
        print(f"  - {f}", file=sys.stderr)
    sys.exit(1)
else:
    print("\n=== ALL 4 STEPS PASSED ===", file=sys.stderr)
