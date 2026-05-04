"""End-to-end dogfood: drive intake → list → route × 3 → status with mocks.

This replaces the per-slice integration test by exercising every CLI
command back-to-back against a realistic 5-item week. Captain HTTP is
mocked at the seam (``register_app_http``, ``get_app_status_http``);
filesystem writes go through the real protocol so we verify on-disk
shape end-to-end.
"""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from week_intake.cli import main


REAL_WEEK_PAYLOAD = {
    "items": [
        {
            "title": "rewrite away-responder docs",
            "raw_text": "rewrite the away-responder docs in chad-agent",
            "kind": "wip",
            "confidence": 0.92,
            "candidate_app_id": "chad-agent",
            "first_question": None,
        },
        {
            "title": "scaffold spark-marketing site",
            "raw_text": "set up a marketing site for spark-of-defiance",
            "kind": "greenfield",
            "confidence": 0.85,
            "candidate_app_id": None,
            "first_question": None,
        },
        {
            "title": "wire up codex-zoom MCP",
            "raw_text": "fold codex-zoom from a github repo into the fleet",
            "kind": "github_repo",
            "confidence": 0.78,
            "candidate_app_id": None,
            "first_question": None,
        },
        {
            "title": "decide pricing tier",
            "raw_text": "settle on a pricing tier for chadacys SaaS",
            "kind": "decision",
            "confidence": 0.55,
            "candidate_app_id": None,
            "first_question": "Is this for the dashboard or the SaaS?",
        },
        {
            "title": "research VA RAMP timing",
            "raw_text": "look into the VA RAMP appeal window",
            "kind": "research",
            "confidence": 0.81,
            "candidate_app_id": None,
            "first_question": None,
        },
    ]
}


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("CHAD_WEEK_DIR", str(tmp_path / "week"))
    monkeypatch.setenv("CHAD_FLEET_APPS_DIR", str(tmp_path / "fleet"))
    monkeypatch.setenv("CHAD_CAPTAIN_API", "http://127.0.0.1:8109")
    # Existing-app routes require the workspace to already exist.
    (tmp_path / "fleet" / "chad-agent").mkdir(parents=True)
    yield tmp_path


def test_dogfood_full_flow(env, tmp_path, capsys):
    week = "2026-W19"

    # ---- intake ---------------------------------------------------------
    md = tmp_path / "week.md"
    md.write_text(
        "- rewrite away-responder docs\n"
        "- scaffold spark marketing site\n"
        "- wire up codex-zoom\n"
        "- decide pricing\n"
        "- research VA RAMP\n",
        encoding="utf-8",
    )
    with patch("week_intake.parser.claude_json", return_value=REAL_WEEK_PAYLOAD):
        rc = main(["intake", "--week", week, "--from", str(md), "--format", "json"])
    assert rc == 0

    capsys.readouterr()  # clear

    # ---- list -----------------------------------------------------------
    rc = main(["list", "--week", week, "--format", "json"])
    assert rc == 0
    parsed = json.loads(capsys.readouterr().out)
    assert {it["item_id"] for it in parsed} == {f"wk-{i:03d}" for i in range(1, 6)}

    # The decision item had confidence 0.55 → needs_clarification.
    rc = main(["list", "--week", week, "--state", "needs_clarification", "--format", "json"])
    assert rc == 0
    nc = json.loads(capsys.readouterr().out)
    assert {it["item_id"] for it in nc} == {"wk-004"}

    # ---- route × 3 ------------------------------------------------------
    # 1) wk-001: existing app, no register call
    with patch("week_intake.router.register_app_http") as reg:
        rc = main(["route", "wk-001", "--week", week, "--app", "chad-agent", "--format", "json"])
    assert rc == 0
    reg.assert_not_called()

    # 2) wk-003: new github repo. Mock register; pretend the repo is checked out.
    repo_path = tmp_path / "repos" / "codex-zoom"
    repo_path.mkdir(parents=True)
    (repo_path / ".git").mkdir()  # new_repo route requires a .git worktree
    with patch("week_intake.router.register_app_http", return_value={"registered": True}) as reg:
        rc = main(
            [
                "route", "wk-003", "--week", week,
                "--app", "codex-zoom",
                "--repo", str(repo_path),
                "--format", "json",
            ]
        )
    assert rc == 0
    reg.assert_called_once()

    # 3) wk-002: greenfield. Mock both scaffold and register so we don't run git.
    from week_intake.scaffold import ScaffoldResult

    fresh_repo = tmp_path / "repos" / "spark-marketing"
    fake_result = ScaffoldResult(path=fresh_repo, created_files=[], created_dirs=[])
    with (
        patch("week_intake.router.scaffold_greenfield", return_value=fake_result) as scaf,
        patch("week_intake.router.register_app_http", return_value={"registered": True}) as reg,
    ):
        rc = main(
            [
                "route", "wk-002", "--week", week,
                "--app", "spark-marketing",
                "--repo", str(fresh_repo),
                "--greenfield", "spark-marketing",
                "--format", "json",
            ]
        )
    assert rc == 0
    scaf.assert_called_once()
    reg.assert_called_once()

    capsys.readouterr()  # clear stdout

    # ---- on-disk verification ------------------------------------------
    fleet_root = tmp_path / "fleet"
    for app_id in ("chad-agent", "codex-zoom", "spark-marketing"):
        notes = list((fleet_root / app_id / "admiral_notes").glob("*.json"))
        assert len(notes) == 1, f"expected one admiral_note for {app_id}, got {len(notes)}"
        payload = json.loads(notes[0].read_text(encoding="utf-8"))
        assert payload["app_id"] == app_id
        assert payload["body"]

    # ---- status ---------------------------------------------------------
    # Pretend captain has consumed wk-001's note and queued the others.
    consumed_lookup = {"chad-agent": "consumed", "codex-zoom": "queued", "spark-marketing": "queued"}

    def fake_status(app_id, *, timeout=5.0):
        from week_intake.protocol import WeekFolder
        f = WeekFolder(week=week)
        item = next((it for it in f.list_items() if it.target.app_id == app_id), None)
        nid = item.captain_note_id if item else "missing"
        if consumed_lookup.get(app_id) == "consumed":
            return {"admiral_notes_queued": [], "admiral_notes_consumed": [{"note_id": nid}]}
        return {"admiral_notes_queued": [{"note_id": nid}], "admiral_notes_consumed": []}

    with patch("week_intake.status.get_app_status_http", side_effect=fake_status):
        rc = main(["status", "--week", week, "--format", "json"])
    assert rc == 0
    report = json.loads(capsys.readouterr().out)
    assert report["totals"]["items"] == 5
    assert report["totals"]["routed"] == 3
    assert report["totals"]["captain_unreachable"] == 0

    statuses = {r["item_id"]: r["captain_note_status"] for r in report["items"]}
    assert statuses["wk-001"] == "consumed"
    assert statuses["wk-003"] == "queued"
    assert statuses["wk-002"] == "queued"
    # Non-routed items report not_routed.
    assert statuses["wk-004"] == "not_routed"
    assert statuses["wk-005"] == "not_routed"


def test_dogfood_status_handles_captain_unreachable(env, tmp_path, capsys):
    """If captain API is down, status still works and flags unreachable."""
    week = "2026-W19"
    md = tmp_path / "week.md"
    md.write_text("- ship it\n", encoding="utf-8")

    one_item = {
        "items": [
            {
                "title": "ship it",
                "raw_text": "ship it",
                "kind": "wip",
                "confidence": 0.9,
                "candidate_app_id": "chad-agent",
                "first_question": None,
            }
        ]
    }
    with patch("week_intake.parser.claude_json", return_value=one_item):
        assert main(["intake", "--week", week, "--from", str(md), "--format", "json"]) == 0
    capsys.readouterr()

    with patch("week_intake.router.register_app_http") as reg:
        assert main(["route", "wk-001", "--week", week, "--app", "chad-agent", "--format", "json"]) == 0
    reg.assert_not_called()
    capsys.readouterr()

    from week_intake.captain_client import CaptainError

    with patch(
        "week_intake.status.get_app_status_http",
        side_effect=CaptainError("connection refused"),
    ):
        assert main(["status", "--week", week, "--format", "json"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["totals"]["captain_unreachable"] == 1
    assert report["items"][0]["captain_note_status"] == "unreachable"
