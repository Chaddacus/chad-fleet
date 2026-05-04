"""CLI integration tests — invoke ``main`` directly with mocked LLM."""

from __future__ import annotations

import json
import os
from unittest.mock import patch

import pytest

from week_intake.cli import main


@pytest.fixture
def tmp_week_base(tmp_path, monkeypatch):
    monkeypatch.setenv("CHAD_WEEK_DIR", str(tmp_path))
    yield tmp_path


def _payload():
    return {
        "items": [
            {
                "title": "ship the thing",
                "raw_text": "ship the thing this week",
                "kind": "wip",
                "confidence": 0.85,
                "candidate_app_id": "author-toolkit",
                "first_question": None,
            }
        ]
    }


def test_intake_writes_jsonl_and_prints_json(tmp_week_base, capsys, monkeypatch):
    md_path = tmp_week_base / "week.md"
    md_path.write_text("- ship the thing this week\n", encoding="utf-8")

    with patch("week_intake.parser.claude_json", return_value=_payload()):
        rc = main(["intake", "--week", "2026-W19", "--from", str(md_path), "--format", "json"])
    assert rc == 0

    out = capsys.readouterr().out
    parsed = json.loads(out)
    assert isinstance(parsed, list)
    assert parsed[0]["item_id"] == "wk-001"
    assert parsed[0]["state"] == "parsed"

    items_path = tmp_week_base / "2026-W19" / "items.jsonl"
    assert items_path.exists()
    assert "wk-001" in items_path.read_text(encoding="utf-8")


def test_list_filters_by_state(tmp_week_base, capsys):
    from week_intake.protocol import WeekFolder
    from week_intake.types import WeekItem

    folder = WeekFolder(week="2026-W19")
    folder.append_item(WeekItem(item_id="wk-001", week="2026-W19", raw_text="a", state="parsed"))
    folder.append_item(WeekItem(item_id="wk-002", week="2026-W19", raw_text="b", state="needs_clarification"))

    rc = main(["list", "--week", "2026-W19", "--state", "needs_clarification", "--format", "json"])
    assert rc == 0
    parsed = json.loads(capsys.readouterr().out)
    assert {it["item_id"] for it in parsed} == {"wk-002"}


def test_intake_rejects_empty_input(tmp_week_base, capsys, monkeypatch):
    monkeypatch.setattr("sys.stdin", _StringStdin(""))
    rc = main(["intake", "--week", "2026-W19"])
    assert rc == 1
    err = capsys.readouterr().err
    assert "empty input" in err


class _StringStdin:
    def __init__(self, s: str) -> None:
        self._s = s

    def read(self) -> str:
        return self._s
