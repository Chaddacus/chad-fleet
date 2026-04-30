"""LLM-driven traffic dogfood for chad-fleet.

Unlike scripts/dogfood_matrix.py (which uses synthetic 'show ONLY a Badge' prompts),
this exercises the LLM with REAL fleet questions that require it to read live
state and render useful UI. If the model can't pick the right primitives + cite
real fields from /api/state, the pipeline isn't actually doing what it claims.

Cases:
  Q1 "Which apps are currently active?" — expects Card+Table OR row of Stats with
     real app ids/states from /api/state.
  Q2 "What is the baseline rubric score for author-toolkit?" — expects Stat with
     value 66.31 (from author-toolkit.metadata.baseline_weighted_avg).
  Q3 "Give me a fleet status overview" — expects multi-primitive composition
     (Card containing Stat or Table).

For each: assert the rendered DOM contains the expected real-state value, AND
asserts no raw JSON leak ('"primitive"' must not appear in output).

Output: PASS/FAIL + screenshots in ~/.playwright-mcp/llm-traffic-<ts>/.
Exits non-zero on any failure.
"""

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from playwright.sync_api import sync_playwright

ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%S")
out_dir = Path.home() / ".playwright-mcp" / f"llm-traffic-{ts}"
out_dir.mkdir(parents=True, exist_ok=True)

results: list[dict] = []
events: list[dict] = []


def log(kind: str, **payload):
    rec = {"ts": datetime.now(timezone.utc).isoformat(), "kind": kind, **payload}
    events.append(rec)
    print(json.dumps(rec, default=str), flush=True)


def submit_and_wait(page, prompt: str, timeout_ms: int = 120000) -> dict:
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


def case(name: str, prompt: str, asserts_any_of: list[list[str]], anti_asserts: list[str] | None = None):
    """Run a single LLM-driven case.

    asserts_any_of: list of OR-groups. Each inner list is AND-asserted (all must
    be present). The case passes if any one group's tokens are ALL present.
    anti_asserts: tokens that must NOT appear in the rendered HTML.
    """
    anti_asserts = anti_asserts or []
    log("case_start", name=name, prompt=prompt)

    p = ctx.new_page()
    p.goto("http://localhost:3000", wait_until="networkidle", timeout=15000)
    p.wait_for_selector('[data-testid="chat-input"]', timeout=10000)

    res = submit_and_wait(p, prompt)
    log("case_response", name=name, status=res["status"])

    failures = []
    if res["status"] != "final":
        failures.append(f"expected final, got {res['status']}: {res.get('text', '')[:200]}")
    else:
        text = res["text"].lower()
        html = res["html"]
        # Any-of OR-groups
        group_pass = False
        for group in asserts_any_of:
            if all(needle.lower() in text or needle in html for needle in group):
                group_pass = True
                break
        if not group_pass:
            failures.append(
                f"none of the assert groups matched. groups={asserts_any_of}, text_excerpt={res['text'][:300]!r}"
            )
        for forbidden in anti_asserts:
            if forbidden in html:
                failures.append(f"forbidden token '{forbidden}' present in DOM")

    p.screenshot(path=str(out_dir / f"{name}.png"), full_page=True)
    results.append({"name": name, "status": "PASS" if not failures else "FAIL", "failures": failures, "prompt": prompt})
    log("case_done", name=name, ok=not failures, failures=failures)
    p.close()


with sync_playwright() as pw:
    browser = pw.chromium.launch(headless=True)
    ctx = browser.new_context()

    case(
        "Q1_which_apps_active",
        "Which apps are currently active in the fleet? Show me an at-a-glance list.",
        # LLM may use the slug id ('author-toolkit') OR the display name
        # ('Author Toolkit') from /api/state. Accept either.
        asserts_any_of=[
            ["author-toolkit", "chad-agent"],
            ["Author Toolkit", "Chad Agent"],
        ],
        anti_asserts=['"primitive"'],
    )

    case(
        "Q2_baseline_score",
        "What is the baseline rubric score for author-toolkit?",
        # Expect the actual numeric value from state metadata.
        asserts_any_of=[
            ["66.31"],
            ["66.3"],  # tolerate rounding
        ],
        anti_asserts=['"primitive"'],
    )

    case(
        "Q3_fleet_overview",
        "Give me a fleet status overview — total apps, active count, and any blocked items.",
        # Expect at least a count of 2 (active apps) AND mention of unmatched OR blocked.
        asserts_any_of=[
            ["2", "active"],
            ["2", "unmatched"],
            ["author-toolkit", "chad-agent"],
        ],
        anti_asserts=['"primitive"'],
    )

    browser.close()

# ---- Report ----
log_path = out_dir / "events.jsonl"
log_path.write_text("\n".join(json.dumps(e, default=str) for e in events))

passed = sum(1 for r in results if r["status"] == "PASS")
total = len(results)

print(f"\n=== LLM-DRIVEN TRAFFIC RESULTS — {passed}/{total} PASS ===\n", file=sys.stderr)
for r in results:
    mark = "✅" if r["status"] == "PASS" else "❌"
    print(f"  {mark} {r['name']}", file=sys.stderr)
    print(f"     prompt: {r['prompt']}", file=sys.stderr)
    for f in r.get("failures", []):
        print(f"     - {f}", file=sys.stderr)

print(f"\nScreenshots + log: {out_dir}", file=sys.stderr)

if passed != total:
    sys.exit(1)
