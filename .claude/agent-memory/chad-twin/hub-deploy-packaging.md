---
name: hub-deploy-packaging
description: S6b Docker packaging for the hub — monorepo build gotchas, proprietary-boundary, distribution gate
metadata:
  type: project
---

The hub ships via `deploy/` (S6b): 5 Dockerfiles + `docker-compose.yml` (health-gated startup) +
`.env.example` (the env contract) + README. Build context for every image is the **repo root**
(`context: ..`). Verified 2026-06-03: `docker compose up` → all 5 healthy, dashboard proxies to
state-aggregator over compose DNS, genui image has **no `claude` binary** (runs on the api provider).

**Proprietary boundary in Python images (load-bearing):** never `uv sync` the workspace — it would
bundle proprietary `captain-core` (a workspace member). Instead `uv pip install --no-sources` the
specific local package dirs. `--no-sources` is REQUIRED because `state-aggregator/pyproject.toml`
has `[tool.uv.sources]` with `email-mcp = { workspace = true }`; without a workspace context uv
errors ("not a workspace member"). Pass the local dirs together (tracked-app-registry, email-mcp,
state-aggregator) so name-deps resolve from them. admiral is not a workspace member → just
`uv pip install fastapi uvicorn pydantic` + `PYTHONPATH=/app/src` (it dispatches captains over the
CLI/file protocol, a runtime boundary, so it boots without the engine).

**Dashboard image (Next 14, monorepo):** the relative layout must be preserved — copy
`packages/genui-renderer` (it's a `file:../../packages/genui-renderer` dep, npm symlinks it) AND
`packages/hub-contracts` (imported by relative path `../../../packages/hub-contracts/ts`) under
`/app/packages/`, with the app at `/app/apps/chad-dashboard`. Use `npm install --legacy-peer-deps`:
the dashboard's TEST tooling has a peer skew (`@vitejs/plugin-react@6` wants vite 8, `vitest@1`
brings vite 5) — irrelevant to `next build`, but plain `npm install` fails ERESOLVE. Keep devDeps
(`next build` needs tailwind/postcss/typescript). No `output: standalone`, so `next build` +
`next start`.

**genui distribution gate:** `GENUI_LLM_PROVIDER=api` makes the renderer use an OpenAI-compatible
endpoint (`LLM_API_KEY`+`LLM_BASE_URL`) instead of shelling to `claude` — this is what lets the
product ship without Claude Code. Default is still `claude-cli` for local dev. See
`packages/genui-renderer/src/providers/`.

**Testing compose without disturbing the launchd stack** (which owns ports 3000/8106/8107/8108/8901):
run a separate project with remapped HOST ports. compose **concatenates** `ports` across `-f` files,
so use the `!override` tag (Compose v5) to REPLACE: `ports: !override ["13000:3000"]`. Internal
service DNS is unaffected by host remapping.

**Auth hash for `.env`:** `node apps/chad-dashboard/features/auth/hash-password.mjs '<pw>'` →
`scrypt$<salt>$<derived>`. Empty `HUB_AUTH_PASSWORD_HASH` = open gate (local only).

See [[chad-admiral-not-in-uv-workspace]] and [[hub-capability-pattern]].
