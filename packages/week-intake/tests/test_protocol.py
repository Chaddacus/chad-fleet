"""Folder + JSONL protocol tests."""

from __future__ import annotations

from datetime import date

import pytest

from week_intake.protocol import (
    WeekFolder,
    iso_week_for,
    next_item_id,
    parse_iso_week,
)
from week_intake.types import WeekItem


def test_iso_week_round_trip() -> None:
    # 2026-05-04 is a Monday in ISO-week 2026-W19.
    tag = iso_week_for(date(2026, 5, 4))
    assert tag == "2026-W19"
    assert parse_iso_week(tag) == date(2026, 5, 4)


def test_parse_iso_week_rejects_garbage() -> None:
    with pytest.raises(ValueError):
        parse_iso_week("not-a-week")


def test_weekfolder_append_and_list(tmp_path) -> None:
    f = WeekFolder(week="2026-W19", base=tmp_path)
    assert not f.items_path.exists()
    a = WeekItem(item_id="wk-001", week="2026-W19", raw_text="task A")
    b = WeekItem(item_id="wk-002", week="2026-W19", raw_text="task B")
    f.append_item(a)
    f.append_item(b)
    assert f.items_path.exists()
    items = f.list_items()
    assert {it.item_id for it in items} == {"wk-001", "wk-002"}


def test_weekfolder_upsert_collapses_to_latest(tmp_path) -> None:
    f = WeekFolder(week="2026-W19", base=tmp_path)
    a = WeekItem(item_id="wk-001", week="2026-W19", raw_text="orig")
    f.append_item(a)
    a2 = a.model_copy(update={"raw_text": "updated", "state": "ready"})
    f.upsert_item(a2)
    items = f.list_items()
    assert len(items) == 1
    assert items[0].raw_text == "updated"
    assert items[0].state == "ready"


def test_get_item_returns_latest(tmp_path) -> None:
    f = WeekFolder(week="2026-W19", base=tmp_path)
    f.append_item(WeekItem(item_id="wk-001", week="2026-W19", raw_text="v1"))
    f.append_item(WeekItem(item_id="wk-001", week="2026-W19", raw_text="v2"))
    got = f.get_item("wk-001")
    assert got is not None
    assert got.raw_text == "v2"
    assert f.get_item("wk-missing") is None


def test_next_item_id_increments(tmp_path) -> None:
    f = WeekFolder(week="2026-W19", base=tmp_path)
    assert next_item_id(f) == "wk-001"
    f.append_item(WeekItem(item_id="wk-001", week="2026-W19", raw_text="x"))
    assert next_item_id(f) == "wk-002"
    f.append_item(WeekItem(item_id="wk-007", week="2026-W19", raw_text="x"))
    assert next_item_id(f) == "wk-008"


def test_driver_log_appends(tmp_path) -> None:
    f = WeekFolder(week="2026-W19", base=tmp_path)
    f.log_driver("first")
    f.log_driver("second")
    txt = f.drivers_log_path.read_text(encoding="utf-8")
    lines = [l for l in txt.splitlines() if l]
    assert len(lines) == 2
    assert lines[0].endswith("\tfirst")
    assert lines[1].endswith("\tsecond")
