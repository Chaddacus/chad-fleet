---
name: hub-capability-pattern
description: How a hub capability is built as a vertical triple (connector/source/feature) + the email-mcp credential rule
metadata:
  type: project
---

A hub capability (Tools, Email, later Slack/Notion/Calendar) is a **vertical triple**, each layer
swappable:

1. **Connector** — `packages/<x>-mcp/` library + stdio MCP. Credentials load in ONE place
   (`<x>_mcp/accounts.py`); a `MailBackend`-style Protocol is the test seam (inject a fake).
2. **Projection** — `state_aggregator/sources/<x>.py` reads **through the connector** (imports the
   connector lib, never opens its own IMAP/CalDAV — Codex review #5, one credential home), returns
   one snapshot key. Add the model to `state_aggregator.types` + wire into `aggregator.py`
   (fetch → validate → FleetState field + summary counts), then regenerate hub-contracts.
3. **Presentation** — `apps/chad-dashboard/features/<x>/{client.ts,ui.tsx}` + thin `app/<x>/page.tsx`
   + one nav entry in `app/layout.tsx`. Re-export the contract type in `lib/types.ts`.

**email-mcp specifics:** env `EMAIL_IMAP_HOST/PORT/USER/PASSWORD`, `EMAIL_SMTP_HOST/PORT`. Unconfigured
⇒ `get_backend()` returns None ⇒ source returns [] (hub runs without email). Run email-mcp tests with
`cd packages/email-mcp && uv run --extra dev python -m pytest tests/` (its own env). `mcp` is an
optional extra (`server`), lazy-imported in server.py, kept off the aggregator's read-path install.
**MCP wire is now PROVEN** (2026-06-02 via `scripts/email_mcp_wire_probe.py`): init→list_tools→
call_tool over stdio returns live messages. Gotcha: `StdioServerParameters` does NOT inherit
`os.environ` — a captain dispatching the MCP must pass `env=dict(os.environ)` so EMAIL_* reach the
spawned server. SMTP send path validated (`scripts/smtp_validate_probe.py`: SMTP_SSL:465 login OK, no
send). STILL boundary-gated: actual send + archive (mutate/emit on the live account).

**calendar-mcp (S5):** the SAME triple. **Primary backend = Google Calendar API via a SERVICE
ACCOUNT** (Chad chose Google directly over CalDAV, 2026-06-02). env `GOOGLE_CALENDAR_SA_JSON` (full
SA key JSON) + `GOOGLE_CALENDAR_ID` (his gmail = the calendar shared with the SA). `CalDavBackend` is
a demoted fallback behind the same `CalendarBackend` seam; `get_backend()` prefers Google. google
client libs (`google-api-python-client`, `google-auth`) are BASE deps of calendar-mcp (the real
connector dep); `caldav` is an optional extra. stdio MCP `calendar_list`/`calendar_create`.
**LIVE-VERIFIED 2026-06-02**: read (real event off chad3124@gmail.com) + create round-trip
(`scripts/calendar_action_probe.py`: insert→read-back→delete, no residue), and the running launchd
aggregator surfaces `calendar_count` from the live calendar. Setup that worked: GCP project
`chad-fleet-calendar` → enable Calendar API → SA `hub-calendar@...iam.gserviceaccount.com` → JSON key
→ share the calendar with the SA email ("Make changes to events") → 2 vault secrets. Service accounts
read/write a shared consumer-Gmail calendar fine; they CANNOT invite guests on consumer Gmail.

**Gmail app-password gotcha (live-verified fix):** Google's UI copies app passwords with
non-breaking spaces (U+00A0) between the 4-char groups; imaplib's ascii LOGIN encoder throws
`UnicodeEncodeError`. `accounts._clean_password` does `"".join(raw.split())` (str.split treats
U+00A0 as whitespace) to normalize to the bare 16-char token. App passwords are whitespace-free,
so stripping is safe. Read path is live-verified against Gmail (chad3124@gmail.com) as of
2026-06-02 via `scripts/email_probe.py` + `bws run --project-id <dream_home>`.

**Secrets:** stored in Bitwarden Secrets Manager (`bws`, NOT the `bw` password CLI which isn't
installed). `BWS_ACCESS_TOKEN` is set in Chad's env; project `dream_home`
(af464c2e-8a7c-40f8-b515-b455013c9ca7). `bws run --project-id <id> -- <cmd>` injects secrets as env
vars by key name. `scripts/launch-with-secrets.sh` wraps this (project id from `$BWS_PROJECT_ID` or
gitignored `.bws-project`). email-mcp tests run in their own env:
`cd packages/email-mcp && uv run --extra dev python -m pytest tests/`.

See [[hub-contracts-pattern]] (regenerate after types change) and [[chad-admiral-not-in-uv-workspace]].
Boundary guard `scripts/check_boundaries.py` must list each new hub module's src + manifest.
