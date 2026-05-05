"""T3 Chadacys marketing custom validator.

Wraps the engine's default ``validate_app_completion`` with a Django fixture
FK gate (Cycle H) that fires whenever the slice diff includes any path
matching the configured fixtures glob.

Configuration lives in the TARGET REPO at ``.chad-captain.t3.json`` (NOT the
captain workspace) so it's code-reviewed alongside the fixtures it gates.
Required fields:

    {
      "settings_module": "config.settings.test",
      "fixtures_glob": "**/fixtures/marketing_*.json",
      "python_bin": ".venv/bin/python"   // optional — defaults to sys.executable
    }

Failure modes are FAIL-CLOSED. If the contract is unmet (missing config,
malformed JSON, missing required keys) the validator emits ``escalate`` AND
writes an admiral_note explaining how to fix it. It NEVER silently delegates
to the default chain when the contract is unmet — that would silently
disable the FK gate this validator exists to enforce.
"""

from __future__ import annotations

import fnmatch
import json
from pathlib import Path

from chad_captain.fixture_validator import validate_django_fixtures
from chad_captain.protocol import (
    AdmiralNote,
    AppWorkspace,
    CurrentSlice,
    SliceComplete,
    write_admiral_note,
)
from chad_captain.validator import (
    ValidationResult,
    validate_app_completion as _default_validate,
)

CONFIG_FILENAME = ".chad-captain.t3.json"
REQUIRED_KEYS: tuple[str, ...] = ("settings_module", "fixtures_glob")


def _load_repo_config(repo_path: str) -> tuple[dict | None, str | None]:
    """Return ``(config, error)``. Exactly one is non-None."""
    p = Path(repo_path).expanduser() / CONFIG_FILENAME
    if not p.exists():
        return None, f"missing {CONFIG_FILENAME} in repo root"
    try:
        cfg = json.loads(p.read_text())
    except (OSError, json.JSONDecodeError) as e:
        return None, f"malformed {CONFIG_FILENAME}: {e}"
    if not isinstance(cfg, dict):
        return None, f"{CONFIG_FILENAME} must be a JSON object"
    missing = [k for k in REQUIRED_KEYS if k not in cfg]
    if missing:
        return None, f"{CONFIG_FILENAME} missing required keys: {missing}"
    for key in REQUIRED_KEYS:
        if not isinstance(cfg[key], str) or not cfg[key].strip():
            return None, f"{CONFIG_FILENAME}.{key} must be a non-empty string"
    return cfg, None


def _emit_config_escalation(
    ws: AppWorkspace, slice_id: str, err: str
) -> ValidationResult:
    write_admiral_note(
        ws,
        AdmiralNote(
            note_id=f"t3-config-error-{slice_id}",
            app_id=ws.app_id,
            body=(
                f"T3 marketing validator could not run: {err}\n\n"
                f"Add a `{CONFIG_FILENAME}` to the repo root with:\n"
                '  {"settings_module": "...", "fixtures_glob": "...", '
                '"python_bin": "..."}\n\n'
                "Until this is fixed, every slice will escalate (fail-closed)."
            ),
            expects_response=False,
        ),
    )
    return ValidationResult(
        verdict="escalate",
        rationale=f"T3 validator config error: {err}",
    )


def validate_app_completion(
    *,
    ws: AppWorkspace,
    complete: SliceComplete,
    dispatched_slice: CurrentSlice,
    repo_path: str,
    reg_app,  # RegisteredApp | None
    score_delta,
    was_retry: bool,
    use_baseline_scorecard: bool,
) -> ValidationResult:
    """T3 marketing chain: fixture FK gate → default chain.

    1. Load ``.chad-captain.t3.json`` from repo root. Missing/malformed → escalate.
    2. If the slice diff includes any file matching ``fixtures_glob``, run
       ``validate_django_fixtures`` over those files. FK violation → reject.
    3. Otherwise (or on success), delegate to the default chain.
    """
    cfg, err = _load_repo_config(repo_path)
    if err:
        return _emit_config_escalation(ws, complete.slice_id, err)

    fixture_files = [
        f
        for f in (complete.files_changed or [])
        if fnmatch.fnmatch(f, cfg["fixtures_glob"])
    ]
    if fixture_files:
        # Resolve fixture paths relative to the repo so `manage.py loaddata`
        # finds them regardless of cwd discrepancies.
        fv = validate_django_fixtures(
            repo_path=repo_path,
            fixture_paths=fixture_files,
            settings_module=cfg["settings_module"],
            python=cfg.get("python_bin"),
        )
        if not fv.ok:
            verdict = "reject_hard" if was_retry else "reject_retry"
            return ValidationResult(
                verdict=verdict,
                rationale=f"fixture validation failed: {fv.summary}",
                retry_context=(
                    "Re-run with valid FK references — listed fixtures "
                    "failed loaddata in a rolled-back transaction. "
                    f"Fixtures: {fixture_files}"
                ),
            )

    return _default_validate(
        ws=ws,
        complete=complete,
        dispatched_slice=dispatched_slice,
        repo_path=repo_path,
        reg_app=reg_app,
        score_delta=score_delta,
        was_retry=was_retry,
        use_baseline_scorecard=use_baseline_scorecard,
    )


__all__ = [
    "CONFIG_FILENAME",
    "REQUIRED_KEYS",
    "validate_app_completion",
]
