"""End-to-end test: route_item is idempotent when called twice for the same item.

The crash-recovery scenario:
  1. route_item files admiral_note, returns updated WeekItem
  2. process dies before folder.upsert_item(updated) persists
  3. on restart, route is called again with the same item — must NOT
     double-file the admiral_note.

This is enforced by deriving a deterministic note_id from (week, item_id)
and short-circuiting in file_admiral_note when that id already exists.
"""

from __future__ import annotations

from unittest.mock import patch

from week_intake.router import route_item
from week_intake.types import WeekItem


def test_route_item_twice_files_only_one_admiral_note(tmp_path) -> None:
    """Simulate a crash between note write and item persist; retry must dedupe."""
    item = WeekItem(
        item_id="wk-001",
        week="2026-W19",
        raw_text="rewrite docs",
        title="rewrite docs",
        kind="wip",
        state="ready",
        confidence=0.9,
    )
    fleet_base = tmp_path / "fleet"
    (fleet_base / "chad-agent").mkdir(parents=True)

    with patch("week_intake.router.register_app_http"):
        # First attempt — crashes after route_item returns but before
        # the caller persists the updated item. Item state is "ready" again.
        updated_1 = route_item(
            item.model_copy(deep=True),
            app_id="chad-agent",
            fleet_base=fleet_base,
        )

    notes_dir = fleet_base / "chad-agent" / "admiral_notes"
    after_first = list(notes_dir.glob("*.json"))
    assert len(after_first) == 1
    first_note_path = after_first[0]
    first_mtime = first_note_path.stat().st_mtime

    # Retry on a fresh copy of item (state still "ready" because the upsert
    # never landed) — deterministic note_id should detect the existing note.
    with patch("week_intake.router.register_app_http"):
        updated_2 = route_item(
            item.model_copy(deep=True),
            app_id="chad-agent",
            fleet_base=fleet_base,
        )

    after_second = list(notes_dir.glob("*.json"))
    assert len(after_second) == 1, "retry must not create a second admiral_note"
    assert after_second[0] == first_note_path
    assert after_second[0].stat().st_mtime == first_mtime, "existing note must not be overwritten"

    assert updated_1.captain_note_id == updated_2.captain_note_id
    assert updated_2.captain_note_id == "chad-week-2026-W19-wk-001"
