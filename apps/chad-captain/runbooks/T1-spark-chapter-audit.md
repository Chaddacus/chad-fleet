# T1 — Spark of Defiance v2 Publish Audit Runbook

## Captain shape

- `app_id = spark-of-defiance`
- `mode = observe_only` — captain NEVER dispatches goose to mutate the manuscript.
- `auto_replan = False` — captain NEVER auto-generates roadmaps. Admiral controls every replan.
- `repo_path = ~/code/personal/spark_of_defiance`

The captain's job during T1 is to **score** the manuscript daily and surface
state via the scorecard. Every editorial decision is admiral's.

## Daily ritual

```bash
chad-captain scorecard --app spark-of-defiance
```

Reads the manuscript repo and prints baseline + Spark extras dims:

| Dimension                   | What it measures                                          |
|-----------------------------|-----------------------------------------------------------|
| voice_guide_intact          | `VOICE_GUIDE.md` present in repo root or `publishing/`    |
| chapters_word_count_target  | fraction of `chapters/*.md` in [1500, 6000] words         |
| drafts_word_count_target    | fraction of `drafts/*.md` in [500, 8000] words            |
| bible_intact                | `bible/` present with at least one populated `.md`        |
| **chapter_audit_progress**  | fraction of detected chapters with a grade entry          |

`chapter_audit_progress` is the publish-prep signal — it climbs from 0.5
(not started) to 1.0 (every chapter graded).

## Per-chapter audit ritual

For each chapter (in any order):

```bash
# 1. Read the chapter
$EDITOR ~/code/personal/spark_of_defiance/chapters/ch03.md

# 2. Codex-audit it against the voice guide + bible
codex exec --skip-git-repo-check --sandbox read-only <<'EOF'
You are an editor for a YA progression-fantasy novel.
Read /Users/chadsimon/code/personal/spark_of_defiance/chapters/ch03.md.
Audit against:
  - publishing/VOICE_GUIDE.md (target voice)
  - bible/ (worldbuilding canon — flag any contradictions)
  - quality bar: BAM-shelf-ready

Return a JSON ChapterGrade entry:
  {
    "chapter_id": "ch03",
    "last_graded_at": "<ISO timestamp>",
    "overall_score": 0.0-1.0,
    "blockers": ["specific issues"],
    "next_action": "highest-leverage rewrite"
  }
EOF

# 3. Append the grade entry to bible/chapter_grades.json
#    Format: {"last_updated": "...", "grades": [<entries>]}
```

The captain reads `bible/chapter_grades.json` on next scorecard run.
`chapter_audit_progress` reflects the new coverage automatically.

## When ready to publish

Once `chapter_audit_progress >= 0.9` and all blockers are addressed:

```bash
chad-captain replan --app spark-of-defiance --trigger publish
```

The replanner generates a publish-mode roadmap (KDP setup, cover, metadata,
launch sequence). Admiral reviews the roadmap, then either:
- Drives publish work manually (captain still observe_only — slices serve
  as a checklist), OR
- Flips `mode=autonomous` + `auto_replan=True` + `validator_module=...` so
  captain dispatches publish ops via goose.

## What the captain WILL NOT do for T1

- **Not auto-replan.** `auto_replan=False` short-circuits the observe_only
  tick at `cli.py::cmd_tick` after PR2 R3-HIGH-1 fix. Daily tick prints
  `idle (auto_replan=False)`.
- **Not respond to admiral_notes by replanning.** Admiral notes are
  read-only context for the captain in observe_only.
- **Not dispatch goose.** mode=observe_only blocks the daemon at
  `daemon.py::tick_autonomous_apps`.

## Recovery if captain goes off-script

If a future cycle accidentally flips Spark to `auto_replan=True`:

```bash
python3 -c "
from chad_captain.apps_registry import load_registry, save_registry
reg = load_registry()
reg.by_id('spark-of-defiance').auto_replan = False
save_registry(reg)
"
```

Or reseed defaults:

```bash
chad-captain register --seed-defaults --force
```

(SPARK_DEFAULT in `apps_registry.py` pins `auto_replan=False` per PR2.)

## Where the chapter grades live

`bible/chapter_grades.json` (recommended) or one of the alternate paths
checked by `find_grades_file`:
- `bible/chapter_grades.json`  ← preferred (lives with the worldbuilding canon)
- `manuscript/chapter_grades.json`
- `chapter_grades.json`        ← repo root fallback

Schema enforced by `ChapterGradesFile` Pydantic model in
`chad_captain/extras/spark_grades.py`.
