# T3 — Chadacys Marketing Discovery Runbook

## Why this comes first

The T3 captain MUST NOT be registered until admiral knows what repo it's
operating against. The `chadacys.com` deployed surface today is undefined
in code — there's a `chadacus.dev` static-site repo and a
`personal/author_toolkit/apps/marketing/` Django app, but neither is the
production marketing surface for the Chadacys author brand. Discovery
resolves "where does the captain land code?" before the captain exists.

Until this runbook completes and Section 4 records a decision, do NOT:
- Create an `apps_registry.json` entry for `t3-chadacys-marketing`.
- Drop a `.chad-captain.t3.json` config file anywhere.
- Run `chad-captain replan --app t3-chadacys-marketing`.

## 1. Identify the deployed surface

```bash
# What's actually serving chadacys.com?
curl -sI https://chadacys.com/ | grep -iE 'server|x-powered-by|via|content-type'
curl -s https://chadacys.com/ | head -100   # peek at the markup
curl -sI https://chadacys.com/posts/ 2>&1   # is /posts/ even routed?
curl -sI https://chadacys.com/pricing 2>&1  # author-toolkit Django pricing surface?

# DNS + cert chain
dig chadacys.com +short
echo | openssl s_client -connect chadacys.com:443 -servername chadacys.com 2>/dev/null \
  | openssl x509 -noout -issuer -subject -dates
```

## 2. Find every candidate repo

```bash
gh repo list Chaddacus --json name,description,url,updatedAt \
  | jq '.[] | select(.description | test("chadacys|marketing|website|spark|inkborn"; "i"))'

ls ~/code/ | grep -iE 'chadacy|chadac|marketing|spark|inkborn'
ls ~/code/personal/ | grep -iE 'chadacy|spark|book'
```

For each candidate, record:
- Path
- `git remote -v`
- Last commit (`git log -1 --format='%h %s %ar'`)
- Whether it deploys to `chadacys.com` (look for `deploy/`, `Dockerfile`, GH Action, CI workflow)

## 3. Inspect deploy mechanism

```bash
# If a noob-root VPS is the deploy target:
ssh noob-root "systemctl list-units --type=service --state=running | grep -iE 'chadacys|nginx|caddy|django|marketing'"
ssh noob-root "ls /etc/nginx/sites-enabled/ 2>/dev/null"
ssh noob-root "ls /opt/ 2>/dev/null | grep -iE 'chadacys|marketing'"

# If a PaaS (Vercel/Netlify/Railway/Fly):
gh secret list -R Chaddacus/<candidate> 2>&1 | grep -iE 'vercel|netlify|railway|fly'
```

## 4. Decision: Option A vs Option B

Document outcome at `~/.chad/captain/notes/t3-discovery-YYYY-MM-DD.md` (NOT in /tmp; this becomes the captain's source-of-truth):

```markdown
# T3 discovery — <date>

## Deployed surface
- Domain serves: <static html | django | next.js | other>
- Repo behind chadacys.com: <repo URL or "no production repo yet">
- Deploy mechanism: <systemd | container | PaaS | "manual scp">
- DB backend: <postgres | sqlite | "no DB" | "fixtures-only">

## Decision
**Option <A | B>**

### Option A — build INSIDE the deployed repo
- repo_path = <absolute path>
- captain_branch = `codex/t3-marketing-captain`
- pr_base_branch = <main | master | production>
- Pros: every captain commit ships to chadacys.com via existing pipeline.
- Cons: marketing concerns mixed with whatever else lives there.

### Option B — greenfield
- New repo: `~/code/Chaddacus/marketing` (mirror of T4 bootstrap pattern)
- Integration contract: <how does chadacys.com consume what this repo emits?>
  - e.g. "main builds a static export that the chadacys.com repo pulls as
    a git submodule" or "renders directly into chadacys.com/posts/<slug>
    via cw-gateway path-prefix routing."
- Pros: clean module boundary; matches T4's approach.
- Cons: extra integration work before the first post renders publicly.

## Open risks
- <e.g. "no Django settings module exists yet — `.chad-captain.t3.json` will need a fresh test settings module before fb-002">
- <e.g. "chadacys.com is a static site; per-post fixture pattern only fits if we add a server-side renderer first">
```

## 5. Pre-bootstrap gates (before T3 bootstrap runs)

Before running the T3 bootstrap (analogous to T4's runbook), all of these
must be true:

- [ ] Section 4 outcome doc saved at `~/.chad/captain/notes/t3-discovery-*.md`.
- [ ] Target repo exists, is cloneable, has at least one green test run.
- [ ] Repo has (or will get in fb-001) a `bible/AUTHOR_VOICE_GUIDE.md`
      placeholder file — captain `voice_guide_present` extra needs SOMETHING
      to score against, even if empty (returns 0.5).
- [ ] Repo has a Django app where post fixtures will land (likely
      `apps/marketing/`), with a `Post` model and `apps/marketing/fixtures/`
      directory created (empty is fine).
- [ ] Repo root contains `.chad-captain.t3.json` with `settings_module`
      pointing at a test-config module that has Django installed and
      can run `loaddata` in a transaction. Example:
      ```json
      {
        "settings_module": "config.settings.test",
        "fixtures_glob": "apps/marketing/fixtures/marketing_posts_*.json",
        "python_bin": ".venv/bin/python"
      }
      ```
- [ ] `verify_cmd` works locally:
      `python manage.py check && python manage.py makemigrations --check --dry-run`

If any gate fails, fix BEFORE bootstrap. The T3 validator is FAIL-CLOSED
on missing/malformed config — every dispatched slice will escalate until
the gates pass.

## 6. Once gates pass

Proceed with the T3 bootstrap (sibling runbook — to be added once
discovery picks Option A vs B). Pattern mirrors T4 exactly:
register with `auto_replan=False` + `validator_module=
chad_captain.validators.t3_marketing`, run one explicit replan, inspect
the roadmap, flip `auto_replan=True`, install plists.
