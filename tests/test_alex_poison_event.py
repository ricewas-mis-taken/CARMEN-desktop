"""Alex-5 regression: a synced event with a malformed start/end must not be
stored, and views must survive one if it is already in the DB."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from datetime import datetime

import calendar_recurrence
import calendar_store
import sync_client
from tests.test_sync_client import isolate_device, fake_logged_in, fake_server  # noqa: F401


def test_sync_rejects_event_with_unparsable_start(isolate_device, fake_logged_in, fake_server):
    isolate_device("b")
    fake_server[("events", "poison-1")] = {
        "table_name": "events", "sync_id": "poison-1",
        "data": {"title": "x", "start": "not-a-date", "end": "also-bad"},
        "device_id": "other", "updated_at": "2026-08-16T12:00:00+00:00", "is_deleted": False,
    }
    result = sync_client.sync_now()
    assert result.failed == 1
    assert calendar_store.list_events() == []


def test_expand_occurrences_survives_malformed_row():
    ev = {"id": "e", "title": "x", "start": "not-a-date", "end": "also-bad"}
    assert calendar_recurrence.expand_occurrences(ev, datetime(2026, 1, 1), datetime(2026, 2, 1)) == []
