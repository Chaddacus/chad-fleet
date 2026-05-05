# T4 — ES Bots Captain Bootstrap Runbook

## Goal

Stand up two cutting-edge ES bots (dev_es_bot + prod_es_bot) on the
noob-root VPS as systemd services, behind cw-gateway, with page-on-demand
HTML rendering. Captain dispatches the work via goose into a new
`Chaddacus/es-bots` repo.

## Decisions (per Chad's "decide for me")

- **Hosting**: same noob-root VPS, two systemd services.
- **LLM**: Claude haiku via cw-gateway (consistent with existing chat_responder bots).
- **Cache**: in-memory LRU, 5min TTL. No Redis.
- **Repo**: `~/code/Chaddacus/es-bots` (new, brand-aligned).
- **Captain validator**: default chain (no Cycle C custom validator).
- **Auto-merge**: False — admiral reviews each PR.

## Pre-captain bootstrap (admiral runs ONCE)

This is the bootstrap the captain CANNOT do (it doesn't exist yet for
this app).

```bash
# 1. Create greenfield repo
gh repo create Chaddacus/es-bots --private --clone --add-readme \
    --description "ES bots — page-on-demand log Q&A for noob ES + production ES"
cd ~/code/Chaddacus/es-bots  # or wherever gh clones

# 2. Skeleton
cat > pyproject.toml <<'EOF'
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[project]
name = "es-bots"
version = "0.0.0"
requires-python = ">=3.11"
dependencies = [
    "fastapi>=0.115",
    "uvicorn>=0.30",
    "elasticsearch>=8",
    "anthropic>=0.40",
    "jinja2>=3",
    "pydantic>=2",
]

[project.optional-dependencies]
dev = ["pytest>=8", "pytest-asyncio>=0.23", "ruff>=0.5", "httpx>=0.27"]

[tool.hatch.build.targets.wheel]
packages = ["src/es_bot"]
EOF

mkdir -p src/es_bot tests
cat > src/es_bot/__init__.py <<'EOF'
"""ES Bot — page-on-demand for Elasticsearch.

Two deploy targets behind one codebase:
  - dev_es_bot  → noob ES (development logs)
  - prod_es_bot → production ES (live logs)

Configuration via env (see README + ops/<env>.env templates):
  ES_URL, ES_INDEX, ES_USER, ES_PASSWORD
  CLAUDE_API_KEY (or CW_GATEWAY_URL + CW_GATEWAY_TOKEN)
  CACHE_TTL_SECONDS=300
"""
__version__ = "0.0.0"
EOF

cat > Makefile <<'EOF'
.PHONY: check
check:
	uv run ruff check src tests
	uv run python -m pytest tests/ -q

.PHONY: install
install:
	uv sync --all-extras
EOF

cat > tests/test_smoke.py <<'EOF'
def test_imports():
    import es_bot
    assert es_bot.__version__
EOF

cat > .gitignore <<'EOF'
__pycache__/
*.pyc
.venv/
ops/*.env
.coverage
EOF

# 3. Verify the skeleton builds + tests pass
uv sync --all-extras
make check  # must exit 0

# 4. Initial commit + push to main
git add .
git commit -m "feat(es-bots): initial skeleton + smoke test"
git push -u origin main

# 5. Create captain branch on origin
git checkout -b codex/es-bots-captain-main
git push -u origin codex/es-bots-captain-main
git checkout main

# 6. Register the captain (auto_replan=False initially per the bootstrap pattern)
python3 - <<'PYEOF'
from chad_captain.apps_registry import (
    AppsRegistry, RegisteredApp, load_registry, save_registry,
)
reg = load_registry()
reg.upsert(RegisteredApp(
    app_id="es-bots",
    name="ES Bots",
    repo_path="~/code/Chaddacus/es-bots",
    mode="autonomous",
    auto_replan=False,           # admiral pulls trigger after bootstrap
    schedule_hour=11,
    verify_cmd="make check",
    verify_timeout_seconds=300,
    captain_branch="codex/es-bots-captain-main",
    pr_base_branch="main",
    auto_push=True,
    auto_open_pr=True,
    auto_merge=False,            # admiral reviews each PR
    notes="Two cutting-edge ES bots (dev_es_bot + prod_es_bot) deployed on noob-root via systemd, behind cw-gateway, with page-on-demand HTML.",
))
save_registry(reg)
print("Registered es-bots.")
PYEOF

# 7. Workspace + backlog
mkdir -p ~/.chad/fleet/apps/es-bots/{admiral_notes,research}
cp ~/code/chad-fleet/apps/chad-captain/seeds/T4-es-bots-feature-backlog.json \
   ~/.chad/fleet/apps/es-bots/research/feature_backlog.json

# 8. First replan — admiral pulls trigger explicitly
chad-captain replan --app es-bots --trigger initial

# 9. Inspect the roadmap; if it looks right, flip auto_replan to True
chad-captain scorecard --app es-bots
cat ~/.chad/fleet/apps/es-bots/roadmap.json | jq '.slices[] | {id: .slice_id, title}'

# 10. Flip auto_replan and install plists
python3 -c "
from chad_captain.apps_registry import load_registry, save_registry
reg = load_registry()
reg.by_id('es-bots').auto_replan = True
save_registry(reg)
"
chad-captain install-plists  # writes BOTH the tick plist AND the goose-runner plist
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.chadcaptain.es-bots.plist
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.chadcaptain.es-bots.goose-runner.plist

# 11. Daemon picks up on the next tick (or run one immediately)
chad-captain tick --app es-bots
```

## Captain shape after bootstrap

```
app_id          es-bots
mode            autonomous
auto_replan     True (after step 10)
verify_cmd      make check
captain_branch  codex/es-bots-captain-main
auto_open_pr    True
auto_merge      False  ← admiral reviews each PR
validator_module None (default chain)
extras          (none — score_repo baseline dims only)
```

## Backlog (8 items, see seeds/T4-es-bots-feature-backlog.json)

| id     | title                                                     | priority |
|--------|-----------------------------------------------------------|----------|
| fb-001 | Sanitized chat_responder import + parameterize ES target  | 0.95     |
| fb-002 | /render?topic=X page-on-demand FastAPI route              | 0.90     |
| fb-003 | cw-gateway route contract test                            | 0.85     |
| fb-004 | systemd unit templates (dev + prod) with op:// env refs   | 0.80     |
| fb-005 | Deploy runbook (op:// → /etc/...env → systemctl)          | 0.70     |
| fb-006 | Per-bot ES query expansion config                         | 0.60     |
| fb-007 | /health endpoint with ES + Claude + cache stats           | 0.55     |
| fb-008 | Log retention monitor + systemd journal rotation          | 0.50     |

## Manual deploy gate

The captain only ships local repo changes. Actual VPS deploy is admiral
+ runbook (fb-005). Captain landing fb-004 + fb-005 means the systemd
units + runbook are in main; admiral then:

```bash
ssh noob-root
cd /opt/es-bots && git pull
op inject -i ops/dev.env.tpl -o /etc/es-bots/dev.env
op inject -i ops/prod.env.tpl -o /etc/es-bots/prod.env
sudo systemctl daemon-reload
sudo systemctl enable --now dev_es_bot.service prod_es_bot.service
sudo systemctl status dev_es_bot prod_es_bot
```

## Recovery

If captain goes off-script (e.g. fb-002 keeps failing verify):

```bash
# Pause captain dispatch
chad-captain unpause --app es-bots --invert  # pause for 60min

# Or flip back to observe_only while admiral fixes manually
python3 -c "
from chad_captain.apps_registry import load_registry, save_registry
a = load_registry().by_id('es-bots')
a.mode = 'observe_only'
save_registry(load_registry())  # re-fetch + persist
"
```

## When deploy succeeds

Each bot exposes:
- `https://chadacys.com/dev-es-bot/render?topic=X` (via cw-gateway routing)
- `https://chadacys.com/prod-es-bot/render?topic=X`
- `/health` for monitoring

cw-gateway routes by path prefix to the appropriate systemd-managed bot.
