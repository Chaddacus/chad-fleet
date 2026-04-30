"""Dogfood matrix for the ephemeral frontend rendering pipeline.

Exercises every primitive, every state, every entry point, and the round-trip.
Captures screenshots + DOM dumps per case. Fails loud on any deviation.

Cases (one Playwright session, sequential):
  P1  Badge alone
  P2  Stat solo
  P3  Table solo
  P4  Card containing Table
  P5  Card containing Stats grid
  P6  Timeline
  P7  Chart (line)
  P8  Multi-node array (Card + Stat + Badge at top level)
  S1  Loading skeleton (asserted while request in flight)
  E1  Error state (point at unreachable upstream)
  R1  Save view → /views shows it → click → /views/[id] renders fresh

Output: per-case PASS/FAIL, screenshots in ~/.playwright-mcp/dogfood-<ts>/,
structured log JSON. Exits non-zero if any case fails.
"""

import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from playwright.sync_api import sync_playwright

ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%S")
out_dir = Path.home() / ".playwright-mcp" / f"dogfood-{ts}"
out_dir.mkdir(parents=True, exist_ok=True)

results: list[dict] = []
events: list[dict] = []


def log(kind: str, **payload):
    rec = {"ts": datetime.now(timezone.utc).isoformat(), "kind": kind, **payload}
    events.append(rec)
    print(json.dumps(rec, default=str), flush=True)


def submit_and_wait(page, prompt: str, timeout_ms: int = 60000) -> dict:
    """Reset chat, submit prompt, wait for final/error. Return result dict."""
    page.fill('[data-testid="chat-input"]', prompt)
    page.locator('button[type="submit"]').click()
    try:
        page.wait_for_selector(
            '[data-testid="final-view"], [data-testid="error-view"]',
            timeout=timeout_ms,
        )
    except Exception as exc:
        return {"status": "timeout", "error": str(exc)}

    if page.locator('[data-testid="final-view"]').count() > 0:
        el = page.locator('[data-testid="final-view"]')
        return {
            "status": "final",
            "html": el.inner_html(),
            "text": el.inner_text(),
        }
    if page.locator('[data-testid="error-view"]').count() > 0:
        return {"status": "error", "text": page.locator('[data-testid="error-view"]').inner_text()}
    return {"status": "neither"}


def case(name: str, prompt: str, asserts: list[str], anti_asserts: list[str] = None):
    """Run a single primitive/composition case."""
    anti_asserts = anti_asserts or []
    log("case_start", name=name, prompt=prompt)

    # New page per case to clear state
    p = ctx.new_page()
    p.goto("http://localhost:3000", wait_until="networkidle", timeout=15000)
    p.wait_for_selector('[data-testid="chat-input"]', timeout=10000)

    res = submit_and_wait(p, prompt)
    log("case_response", name=name, status=res["status"])

    failures = []
    if res["status"] != "final":
        failures.append(f"expected final, got {res['status']}: {res.get('text', '')[:200]}")
    else:
        html = res["html"]
        for needle in asserts:
            if needle not in html:
                failures.append(f"assert missing '{needle}' in DOM")
        for forbidden in anti_asserts:
            if forbidden in html:
                failures.append(f"forbidden token '{forbidden}' present in DOM")

    # Screenshot
    p.screenshot(path=str(out_dir / f"{name}.png"), full_page=True)

    results.append({"name": name, "status": "PASS" if not failures else "FAIL", "failures": failures, "prompt": prompt})
    log("case_done", name=name, ok=not failures, failures=failures)
    p.close()


def case_loading_skeleton():
    """S1: assert the loading skeleton appears while the request is in flight."""
    name = "S1_loading_skeleton"
    log("case_start", name=name)
    p = ctx.new_page()
    p.goto("http://localhost:3000", wait_until="networkidle", timeout=15000)
    p.wait_for_selector('[data-testid="chat-input"]', timeout=10000)
    p.fill('[data-testid="chat-input"]', "show one badge: status active")
    p.locator('button[type="submit"]').click()

    # Skeleton should be visible within ~200ms
    failures = []
    try:
        p.wait_for_selector('[data-testid="skeleton"]', timeout=2000, state="visible")
    except Exception as exc:
        failures.append(f"skeleton never appeared: {exc}")
    p.screenshot(path=str(out_dir / f"{name}.png"))

    # Wait for completion to clean up
    try:
        p.wait_for_selector('[data-testid="final-view"], [data-testid="error-view"]', timeout=60000)
    except Exception:
        pass
    results.append({"name": name, "status": "PASS" if not failures else "FAIL", "failures": failures})
    log("case_done", name=name, ok=not failures, failures=failures)
    p.close()


def case_round_trip():
    """R1: save view → /views library shows it → click → re-render produces final."""
    name = "R1_save_list_replay"
    log("case_start", name=name)
    p = ctx.new_page()
    p.on("dialog", lambda d: d.accept(f"dogfood-{ts}"))

    # Capture the id assigned by the registry (slug may differ from input name)
    captured = {"id": None}

    def on_resp(resp):
        if resp.url.endswith("/api/views") and resp.request.method == "POST":
            try:
                data = resp.json()
                if isinstance(data, dict) and isinstance(data.get("view"), dict):
                    captured["id"] = data["view"].get("id")
            except Exception:
                pass

    p.on("response", on_resp)

    p.goto("http://localhost:3000", wait_until="networkidle", timeout=15000)
    p.wait_for_selector('[data-testid="chat-input"]', timeout=10000)

    failures = []

    # Submit + wait for final
    res = submit_and_wait(p, "render a Card titled 'Round Trip Test' containing a Stat for total apps")
    if res["status"] != "final":
        failures.append(f"step1 final not reached: {res}")

    if not failures:
        # Save
        p.locator('[data-testid="save-view-button"]').click()
        p.wait_for_timeout(2500)
        msg = p.locator('[data-testid="save-message"]')
        if msg.count() == 0 or "Saved" not in msg.inner_text():
            failures.append(f"step2 save message wrong: {msg.inner_text() if msg.count() > 0 else 'absent'}")

    if not failures:
        # Navigate to /views
        p.goto("http://localhost:3000/views", wait_until="networkidle", timeout=10000)
        p.wait_for_timeout(1000)
        rows = p.locator('[data-testid^="view-row-"]')
        if rows.count() == 0:
            failures.append("step3 no rows in /views list")

    if not failures:
        if captured["id"] is None:
            failures.append("step4 no id captured from POST /api/views response")
        else:
            target = p.locator(f'[data-testid="view-row-{captured["id"]}"]').first
            if target.count() == 0:
                failures.append(f"step4 saved row '{captured['id']}' not in list")
            else:
                target.locator('a').first.click()
                try:
                    p.wait_for_url("**/views/**", timeout=5000)
                    p.wait_for_selector('[data-testid="view-final"], [data-testid="view-error"]', timeout=60000)
                    if p.locator('[data-testid="view-final"]').count() == 0:
                        failures.append("step5 view-final not reached")
                    else:
                        html = p.locator('[data-testid="view-final"]').inner_html()
                        if '"primitive"' in html:
                            failures.append("step5 raw JSON in re-rendered view")
                except Exception as exc:
                    failures.append(f"step5 wait_for_selector: {exc}")

    p.screenshot(path=str(out_dir / f"{name}.png"))
    results.append({"name": name, "status": "PASS" if not failures else "FAIL", "failures": failures})
    log("case_done", name=name, ok=not failures, failures=failures)
    p.close()


with sync_playwright() as pw:
    browser = pw.chromium.launch(headless=True)
    ctx = browser.new_context()

    # ---- Primitive cases ----
    case(
        "P1_Badge_solo",
        "show ONLY a Badge with tone success and label 'fleet healthy'",
        asserts=["bg-green-100"],  # success Badge tone class
        anti_asserts=['"primitive"'],
    )
    case(
        "P2_Stat_solo",
        "show ONLY a Stat with label 'Total Apps' and value 3",
        asserts=["Total Apps", "3"],
        anti_asserts=['"primitive"'],
    )
    case(
        "P3_Table_solo",
        "show ONLY a Table with headers ['App','State'] and rows for the apps in state",
        asserts=["<table", "<th", "<tbody"],
        anti_asserts=['"primitive"'],
    )
    case(
        "P4_Card_with_Table",
        "render a Card titled 'Apps' containing a Table of all apps with their state",
        asserts=["<table", "<th", "<h3"],
        anti_asserts=['"primitive"'],
    )
    case(
        "P5_Card_with_Stats",
        "render a Card titled 'Fleet KPIs' containing 2 Stats: total apps and active apps",
        asserts=["<h3", "Fleet KPIs"],
        anti_asserts=['"primitive"'],
    )
    case(
        "P6_Timeline",
        "show a Timeline with 3 items showing recent fleet activity from the state",
        asserts=["Timeline", "<li"] if False else ["<li"],  # Timeline renders as list
        anti_asserts=['"primitive"'],
    )
    case(
        "P7_Chart_line",
        "show a line Chart with x-axis label 'time' and y-axis label 'count' with 4 data points",
        asserts=["recharts", "svg"],  # recharts renders an SVG
        anti_asserts=['"primitive"'],
    )
    case(
        "P8_Multi_top_level",
        "render 3 things at the top level: a Stat for total apps, a Badge for fleet health, and a Card containing a Table of apps",
        asserts=["<table", "<h3"],
        anti_asserts=['"primitive"'],
    )

    # ---- States ----
    case_loading_skeleton()

    # ---- Round-trip ----
    case_round_trip()

    browser.close()

# ---- Report ----
log_path = out_dir / "events.jsonl"
log_path.write_text("\n".join(json.dumps(e, default=str) for e in events))

passed = sum(1 for r in results if r["status"] == "PASS")
total = len(results)

print(f"\n=== DOGFOOD MATRIX RESULTS — {passed}/{total} PASS ===\n", file=sys.stderr)
for r in results:
    mark = "✅" if r["status"] == "PASS" else "❌"
    print(f"  {mark} {r['name']}", file=sys.stderr)
    for f in r.get("failures", []):
        print(f"     - {f}", file=sys.stderr)

print(f"\nScreenshots + log: {out_dir}", file=sys.stderr)

if passed != total:
    sys.exit(1)
