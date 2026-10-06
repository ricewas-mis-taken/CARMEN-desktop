"""Regression: editing an event's focus profile must stamp the focus_profiles
row (updated_at/device_id), otherwise sync_client never pushes the edit (after
a pull stamped the row) or re-pushes it with a fresh 'now' on every sync
(NULL updated_at), clobbering newer edits from other devices."""
import time

import calendar_store
import pytest
import sync_client


@pytest.fixture
def cal(isolate_calendar_db):
    yield


def _event(**kw):
    e = {"title": "Study", "start": "2026-08-10T09:00:00", "end": "2026-08-10T10:00:00",
         "focusProfile": {"enabled": True, "lockMode": "soft", "processBlocklist": [], "domainWhitelist": []}}
    e.update(kw)
    return e


def _profile_row(event_id):
    return calendar_store._get_conn().execute(
        "SELECT * FROM focus_profiles WHERE event_id=?", (event_id,)).fetchone()


def _gathered(cutoff):
    with calendar_store._lock:
        return sync_client._gather_focus_profiles(calendar_store._get_conn(), cutoff)


def test_new_focus_profile_row_is_stamped(cal):
    event_id = calendar_store.save_event(_event())
    row = _profile_row(event_id)
    assert row["updated_at"] is not None
    assert row["device_id"]


def test_focus_profile_edit_after_last_sync_is_gathered(cal):
    event_id = calendar_store.save_event(_event())
    conn = calendar_store._get_conn()
    # simulate the row having been stamped by an earlier sync/pull
    conn.execute("UPDATE focus_profiles SET updated_at='2026-01-01T00:00:00' WHERE event_id=?", (event_id,))
    conn.commit()
    cutoff = "2026-06-01T00:00:00"
    assert _gathered(cutoff) == []
    time.sleep(0.01)
    ev = _event(id=event_id)
    ev["focusProfile"]["lockMode"] = "hard"
    calendar_store.save_event(ev)
    recs = _gathered(cutoff)
    assert [r["sync_id"] for r in recs] == [event_id]
    assert recs[0]["data"]["lockMode"] == "hard"
