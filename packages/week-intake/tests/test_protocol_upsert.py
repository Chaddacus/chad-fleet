"""Atomic upsert + strict list_items tests."""

from __future__ import annotations

import json

import pytest

from week_intake.protocol import WeekFolder
from week_intake.types import WeekItem


def _item(item_id: str, state: str = "parsed") -> WeekItem:
    return WeekItem(item_id=item_id, week="2026-W19", raw_text=f"text for {item_id}", state=state)


def test_upsert_replaces_existing(tmp_path) -> None:
    f = WeekFolder(week="2026-W19", base=tmp_path)
    f.upsert_item(_item("wk-001", state="parsed"))
    f.upsert_item(_item("wk-001", state="ready"))
    items = f.list_items()
    assert len(items) == 1
    assert items[0].state == "ready"


def test_upsert_appends_new(tmp_path) -> None:
    f = WeekFolder(week="2026-W19", base=tmp_path)
    f.upsert_item(_item("wk-001"))
    f.upsert_item(_item("wk-002"))
    f.upsert_item(_item("wk-003"))
    items = f.list_items()
    assert {it.item_id for it in items} == {"wk-001", "wk-002", "wk-003"}


def test_upsert_writes_atomic_full_file(tmp_path) -> None:
    """File contains exactly the persisted items, one per line, no duplicates."""
    f = WeekFolder(week="2026-W19", base=tmp_path)
    f.upsert_item(_item("wk-001"))
    f.upsert_item(_item("wk-001"))  # second upsert
    raw = f.items_path.read_text(encoding="utf-8")
    lines = [l for l in raw.splitlines() if l.strip()]
    assert len(lines) == 1, f"expected one line after dedup, got {lines!r}"


def test_list_items_strict_raises_on_corrupt_line(tmp_path) -> None:
    f = WeekFolder(week="2026-W19", base=tmp_path)
    f.ensure()
    # Write good item then corrupt line.
    good = _item("wk-001").model_dump(mode="json")
    f.items_path.write_text(json.dumps(good) + "\n{ this is not json\n", encoding="utf-8")

    with pytest.raises(ValueError):
        f.list_items_strict()


def test_list_items_tolerates_corrupt_line(tmp_path) -> None:
    """Read-only path skips corrupt lines so UX commands still work."""
    f = WeekFolder(week="2026-W19", base=tmp_path)
    f.ensure()
    good = _item("wk-001").model_dump(mode="json")
    f.items_path.write_text(json.dumps(good) + "\nGARBAGE\n", encoding="utf-8")

    items = f.list_items()
    assert {it.item_id for it in items} == {"wk-001"}


def test_upsert_refuses_to_silently_drop_corrupt_lines(tmp_path) -> None:
    """upsert_item rewrites the whole file; refusing strict avoids data loss."""
    f = WeekFolder(week="2026-W19", base=tmp_path)
    f.ensure()
    good = _item("wk-001").model_dump(mode="json")
    f.items_path.write_text(json.dumps(good) + "\nGARBAGE\n", encoding="utf-8")

    with pytest.raises(ValueError):
        f.upsert_item(_item("wk-002"))


def test_upsert_preserves_revision_on_disk(tmp_path) -> None:
    """upsert_item must persist the revision field set by the caller."""
    f = WeekFolder(week="2026-W19", base=tmp_path)
    item = _item("wk-001")
    item.revision = 42
    f.upsert_item(item)
    on_disk = f.get_item("wk-001")
    assert on_disk.revision == 42


def test_pre_v1_jsonl_loads_with_default_revision(tmp_path) -> None:
    """Items written before v1 (no revision field) load with revision=0."""
    f = WeekFolder(week="2026-W19", base=tmp_path)
    f.ensure()
    legacy = {
        "item_id": "wk-001",
        "week": "2026-W19",
        "raw_text": "legacy item",
        "title": "legacy",
        "kind": "wip",
        "state": "parsed",
        "confidence": 0.9,
        "target": {"app_id": None, "repo_path": None, "is_new_app": False, "greenfield_name": None},
        "clarifications": [],
        "captain_note_id": None,
        "created_at": "2025-01-01T00:00:00+00:00",
        "updated_at": "2025-01-01T00:00:00+00:00",
        # no revision, pending_refresh_question_id, refresh_warnings
    }
    f.items_path.write_text(json.dumps(legacy) + "\n", encoding="utf-8")
    item = f.get_item("wk-001")
    assert item is not None
    assert item.revision == 0
    assert item.pending_refresh_question_id is None
    assert item.refresh_warnings == []
