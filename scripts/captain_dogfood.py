"""Captain-fleet dogfood: drive Spark + author-toolkit captain output through the dashboard.

Verifies that captain.next_actions surfaces playbook-grounded recommendations,
and that the dashboard's chat surface can render them as a useful UI.

Cases:
  C1 "What's the captain's brief for today?"
       → expect a Card or Timeline citing real captain output
  C2 "What does the indie-author-launch playbook recommend for author-toolkit?"
       → expect a Card+Table with multiple recommendation paragraphs
  C3 "What launch tasks are on the plate this week for the book?"
       → expect actions referring to launch / ARC / Author Central / Amazon

For each case: assert the rendered DOM mentions at least one playbook-grounded
keyword (cover|blurb|ARC|Amazon|Author Central|Rocket|launch). Anti-assert
raw JSON leak.
"""

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from playwright.sync_api import sync_playwright

ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%S")
out_dir = Path.home() / ".playwright-mcp" / f"captain-dogfood-{ts}"
out_dir.mkdir(parents=True, exist_ok=True)

results: list[dict] = []
events: list[dict] = []


def log(kind: str, **payload):
    rec = {"ts": datetime.now(timezone.utc).isoformat(), "kind": kind, **payload}
    events.append(rec)
    print(json.dumps(rec, default=str), flush=True)


def submit_and_wait(page, prompt: str, timeout_ms: int = 120000) -> dict:
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
        return {"status": "final", "html": el.inner_html(), "text": el.inner_text()}
    if page.locator('[data-testid="error-view"]').count() > 0:
        return {"status": "error", "text": page.locator('[data-testid="error-view"]').inner_text()}
    return {"status": "neither"}


# Keywords that indicate captain/playbook content actually surfaced
PLAYBOOK_KEYWORDS = {
    "cover", "blurb", "arc", "amazon", "author central", "rocket",
    "launch", "kdp", "review", "kindle", "preorder", "categor",
}


def case(name: str, prompt: str):
    log("case_start", name=name, prompt=prompt)
    p = ctx.new_page()
    p.goto("http://localhost:3000", wait_until="networkidle", timeout=15000)
    p.wait_for_selector('[data-testid="chat-input"]', timeout=10000)
    res = submit_and_wait(p, prompt)
    log("case_response", name=name, status=res["status"])

    failures = []
    text_lower = res.get("text", "").lower()
    matched_kws = [kw for kw in PLAYBOOK_KEYWORDS if kw in text_lower]

    if res["status"] != "final":
        failures.append(f"expected final, got {res['status']}: {res.get('text','')[:200]}")
    else:
        if not matched_kws:
            failures.append(
                f"none of the playbook keywords matched. text_excerpt={res['text'][:300]!r}"
            )
        if '"primitive"' in res["html"]:
            failures.append("raw JSON 'primitive' leaked into DOM")

    p.screenshot(path=str(out_dir / f"{name}.png"), full_page=True)
    results.append(
        {
            "name": name,
            "status": "PASS" if not failures else "FAIL",
            "failures": failures,
            "matched_keywords": matched_kws,
            "prompt": prompt,
            "text_excerpt": res.get("text", "")[:400],
        }
    )
    log("case_done", name=name, ok=not failures, failures=failures, matched=matched_kws)
    p.close()


with sync_playwright() as pw:
    browser = pw.chromium.launch(headless=True)
    ctx = browser.new_context()

    case(
        "C1_captain_brief",
        "What's the captain's brief for today? Show me the headline and key apps.",
    )
    case(
        "C2_playbook_for_author_toolkit",
        "What does the indie author launch playbook recommend for the author-toolkit app? "
        "Author-toolkit is at T-32 days from a book launch (Spark of Defiance, June 1).",
    )
    case(
        "C3_this_week_for_book",
        "What launch tasks are on my plate this week for Spark of Defiance? Just the top 3 actions.",
    )

    browser.close()

# Report
log_path = out_dir / "events.jsonl"
log_path.write_text("\n".join(json.dumps(e, default=str) for e in events))

passed = sum(1 for r in results if r["status"] == "PASS")
total = len(results)

print(f"\n=== CAPTAIN DOGFOOD — {passed}/{total} PASS ===\n", file=sys.stderr)
for r in results:
    mark = "✅" if r["status"] == "PASS" else "❌"
    print(f"\n  {mark} {r['name']}", file=sys.stderr)
    print(f"     prompt: {r['prompt']}", file=sys.stderr)
    print(f"     matched keywords: {r['matched_keywords']}", file=sys.stderr)
    print(f"     text excerpt: {r['text_excerpt'][:300]!r}", file=sys.stderr)
    for f in r.get("failures", []):
        print(f"     - {f}", file=sys.stderr)

print(f"\nScreenshots + log: {out_dir}", file=sys.stderr)
sys.exit(0 if passed == total else 1)
