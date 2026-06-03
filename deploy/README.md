# Deploying the chad-fleet hub

Self-contained Docker packaging for the hub: admiral, state-aggregator, view-registry,
genui-renderer, and the dashboard. Runs **without Claude Code** — the renderer uses the `api`
LLM provider (`GENUI_LLM_PROVIDER=api`).

## One-command bring-up

```bash
cd deploy
cp .env.example .env        # then fill in (see below)
docker compose up --build   # builds 5 images, starts in health-gated order
```

The dashboard waits for admiral + aggregator + genui to report healthy before starting. Open
http://localhost:3000.

## Configuration (`deploy/.env`)

`.env` is gitignored — never commit the filled-in file. Keys (full list + comments in
`.env.example`):

| Group | Keys | Notes |
|---|---|---|
| Service URLs | `NEXT_PUBLIC_{ADMIRAL,AGGREGATOR,GENUI}_URL` | compose service DNS; the browser only hits the dashboard |
| Auth | `HUB_AUTH_SECRET`, `HUB_AUTH_PASSWORD_HASH`, `HUB_AUTH_TTL_SECONDS` | empty hash ⇒ gate is **open** (local only) |
| LLM | `GENUI_LLM_PROVIDER=api`, `LLM_API_KEY`, `LLM_BASE_URL`, `LLM_MODEL` | OpenAI-compatible endpoint |
| Email | `EMAIL_IMAP_*`, `EMAIL_SMTP_*` | leave blank to run the hub without email |

### Auth setup (do this before exposing the hub)

```bash
openssl rand -hex 32                                              # -> HUB_AUTH_SECRET
node ../apps/chad-dashboard/features/auth/hash-password.mjs 'your-password'   # -> HUB_AUTH_PASSWORD_HASH
```

Stored hash form is `scrypt$<salt>$<derived>`. With `HUB_AUTH_PASSWORD_HASH` set, every route
except `/login` and `/api/auth/*` requires the signed `httpOnly` session cookie.

## Hardening checklist (S1)

- **Set `HUB_AUTH_PASSWORD_HASH`.** Empty = open gate; fine for localhost, not for any exposed host.
- **Terminate TLS at a reverse proxy** (Caddy/nginx/Cloudflare) in front of the dashboard. The
  session cookie is `httpOnly`+`SameSite=Lax` and is marked `Secure` — it requires HTTPS to be sent,
  so the hub must sit behind TLS in production.
- **Keep service ports internal.** Only the dashboard (`:3000`) needs to be reachable; admiral,
  aggregator, genui, and view-registry talk over the compose network. Drop their `ports:` mappings
  (or bind to `127.0.0.1`) when deploying behind a proxy.
- **Rotate `HUB_AUTH_SECRET`** to invalidate all sessions.

## External dependency: omni-mem

The truth spine (omni-mem) is **not** built here — run it separately and point `OMNI_MEM_URL` at
it. See the omni-mem deployment docs.

## Product boundary

The Python images install only their own packages via `uv pip install --no-sources` (e.g. the
aggregator gets `state-aggregator` + `email-mcp` + `tracked-app-registry`). The workspace is never
`uv sync`'d, so the proprietary `captain-core` engine is never bundled. The admiral dispatches
captains over the CLI/file protocol — a runtime boundary, not an import — so the hub boots without
the engine present.

## Verification (run on a clean host)

```bash
docker compose up --build -d
docker compose ps           # all 5 healthy
curl -s localhost:3000/         # 200 (or 307 -> /login if auth on)
curl -s localhost:3000/api/state | jq keys   # dashboard -> aggregator over compose DNS
```
