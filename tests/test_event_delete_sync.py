"""Regression: deleting/undoing an event must bump updated_at, otherwise
sync_client._gather_events (WHERE updated_at > last_sync) never pushes it."""
import time

import calendar_store
import device_id
import pytest
import sync_client


@pytest.fixture
def cal(isolate_calendar_db):
    yield


def _event():
    return {"title": "Standup", "start": "2026-08-10T09:00:00", "end": "2026-08-10T09:15:00"}


def _gathered_events(cutoff):
    with calendar_store._lock:
        return sync_client._gather_events(calendar_store._get_conn(), cutoff)


def test_soft_delete_after_last_sync_is_gathered_as_deleted(cal):
    event_id = calendar_store.save_event(_event())
    cutoff = calendar_store._get_conn().execute(
        "SELECT updated_at FROM events WHERE id=?", (event_id,)).fetchone()["updated_at"]
    time.sleep(0.01)
    calendar_store.soft_delete_event(event_id)
    recs = _gathered_events(cutoff)
    assert [r["sync_id"] for r in recs] == [event_id]
    assert recs[0]["is_deleted"] is True


def test_undo_delete_after_last_sync_is_gathered_as_live(cal):
    event_id = calendar_store.save_event(_event())
    calendar_store.soft_delete_event(event_id)
    cutoff = calendar_store._get_conn().execute(
        "SELECT updated_at FROM events WHERE id=?", (event_id,)).fetchone()["updated_at"]
    time.sleep(0.01)
    calendar_store.undo_delete_event(event_id)
    recs = _gathered_events(cutoff)
    assert [r["sync_id"] for r in recs] == [event_id]
    assert recs[0]["is_deleted"] is False
