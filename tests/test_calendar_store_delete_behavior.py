"""Tests for Phase 3 Part A's calendar_store changes: purge_expired_soft_
deletes() no longer physically removes soft-deleted events, and
save_event()'s reminder handling diffs against existing rows instead of
DELETE-then-reinsert on every edit."""
import pytest

import calendar_store
import device_id


@pytest.fixture
def isolate_calendar(isolate_calendar_db, tmp_path, monkeypatch):
    monkeypatch.setattr(device_id, "DEVICE_ID_PATH", str(tmp_path / "device_id.txt"))
    monkeypatch.setattr(device_id, "_cached_id", None)
    yield


def _make_event(**overrides):
    event = {
        "title": "Standup", "start": "2026-08-10T09:00:00", "end": "2026-08-10T09:15:00",
        "reminderOffsets": [10, 30],
    }
    event.update(overrides)
    return event


def test_purge_expired_soft_deletes_does_not_remove_rows(isolate_calendar):
    event_id = calendar_store.save_event(_make_event())
    calendar_store.soft_delete_event(event_id)

    calendar_store.purge_expired_soft_deletes()

    assert calendar_store.get_event(event_id) is not None
    assert calendar_store.list_events(include_deleted=True)


def test_undo_still_works_after_purge_runs(isolate_calendar):
    event_id = calendar_store.save_event(_make_event())
    calendar_store.soft_delete_event(event_id)
    calendar_store.purge_expired_soft_deletes()

    assert calendar_store.undo_delete_event(event_id) is True
    assert calendar_store.get_event(event_id)["deletedAt"] is None
    assert any(e["id"] == event_id for e in calendar_store.list_events())


def test_unchanged_reminder_is_not_rewritten_on_edit(isolate_calendar):
    event_id = calendar_store.save_event(_make_event())
    conn = calendar_store._get_conn()
    before = {
        r["offset_minutes"]: r["updated_at"]
        for r in conn.execute("SELECT offset_minutes, updated_at FROM reminders WHERE event_id = ?", (event_id,))
    }

    # Re-save with the same reminder offsets but a different title -- only
    # the event row should change, not the reminder rows.
    calendar_store.save_event(_make_event(id=event_id, title="Standup (renamed)"))

    conn = calendar_store._get_conn()
    after = {
        r["offset_minutes"]: r["updated_at"]
        for r in conn.execute("SELECT offset_minutes, updated_at FROM reminders WHERE event_id = ?", (event_id,))
    }
    assert after == before


def test_removed_reminder_is_tombstoned_not_deleted(isolate_calendar):
    event_id = calendar_store.save_event(_make_event(reminderOffsets=[10, 30]))

    calendar_store.save_event(_make_event(id=event_id, reminderOffsets=[10]))

    conn = calendar_store._get_conn()
    rows = conn.execute("SELECT offset_minutes, is_deleted FROM reminders WHERE event_id = ?", (event_id,)).fetchall()
    by_offset = {r["offset_minutes"]: r["is_deleted"] for r in rows}
    assert by_offset[10] == 0
    assert by_offset[30] == 1
    # The row physically still exists (not removed) -- just flagged.
    assert len(rows) == 2

    event = calendar_store.get_event(event_id)
    assert event["reminderOffsets"] == [10]


def test_added_reminder_is_inserted(isolate_calendar):
    event_id = calendar_store.save_event(_make_event(reminderOffsets=[10]))

    calendar_store.save_event(_make_event(id=event_id, reminderOffsets=[10, 60]))

    event = calendar_store.get_event(event_id)
    assert event["reminderOffsets"] == [10, 60]


def test_re_adding_a_removed_reminder_reuses_the_tombstoned_row(isolate_calendar):
    event_id = calendar_store.save_event(_make_event(reminderOffsets=[10, 30]))
    calendar_store.save_event(_make_event(id=event_id, reminderOffsets=[10]))  # tombstones 30

    calendar_store.save_event(_make_event(id=event_id, reminderOffsets=[10, 30]))  # brings 30 back

    conn = calendar_store._get_conn()
    rows = conn.execute("SELECT offset_minutes, is_deleted FROM reminders WHERE event_id = ?", (event_id,)).fetchall()
    # Still only 2 rows total -- the revived offset reused its old tombstoned
    # row instead of a fresh insert accumulating duplicates.
    assert len(rows) == 2
    assert {r["offset_minutes"]: r["is_deleted"] for r in rows} == {10: 0, 30: 0}
