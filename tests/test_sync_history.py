"""Finished sessions travel between computers within seconds, so a task's
logged minutes (and the history list) are the same on every machine."""
from datetime import datetime, timedelta

import pytest

import auth_manager
import calendar_store
import review_store
import device_id
import session_history
import session_manager as sm
import sync_client
import sync_cloud
import sync_live
import tasks_store
from fake_supabase import FakeSupabase

from test_sync_client import fake_logged_in  # noqa: F401  (shared fixture)


@pytest.fixture
def server(monkeypatch):
    s = FakeSupabase(user_id="user-1")
    s.install(monkeypatch, sync_client)
    return s


@pytest.fixture
def device(tmp_path, monkeypatch):
    """Switches this process between simulated computers: each has its own files."""
    def _switch(name):
        base = tmp_path / name
        base.mkdir(exist_ok=True)
        monkeypatch.setattr(session_history, "HISTORY_PATH", str(base / "session_history.json"))
        monkeypatch.setattr(sync_live, "CURSOR_PATH", str(base / "live_cursor.txt"))
        monkeypatch.setattr(sync_live, "HISTORY_PUSHED_PATH", str(base / "live_history_pushed.txt"))
        monkeypatch.setattr(sync_client, "SYNC_OWNER_PATH", str(base / "owner.txt"))
        monkeypatch.setattr(device_id, "DEVICE_ID_PATH", str(base / "device_id.txt"))
        monkeypatch.setattr(device_id, "_cached_id", None)
        monkeypatch.setattr(calendar_store, "DB_PATH", str(base / "calendar.db"))
        monkeypatch.setattr(calendar_store, "_conn", None)
        monkeypatch.setattr(review_store, "_schema_ready", False)
        monkeypatch.setattr(tasks_store, "TASKS_PATH", str(base / "tasks.json"))
        monkeypatch.setattr(sync_cloud, "_verified", False)
    return _switch


def _entry(session_id, start, minutes, task="task-1", **extra):
    begin = datetime.fromisoformat(start)
    return {
        "startTime": begin.isoformat(), "endTime": (begin + timedelta(minutes=minutes)).isoformat(),
        "endType": "manual", "lockMode": "soft", "processBlocklist": ["discord.exe"], "domainWhitelist": [],
        "violationCount": 0, "violationLog": [{"kind": "pause", "timestamp": begin.isoformat()}],
        "source": "task", "eventId": task, "eventTitle": "usaco", "sessionId": session_id, **extra,
    }


def test_a_session_finished_on_one_computer_appears_on_the_other(device, fake_logged_in, server):
    device("a")
    session_history.append_entry(_entry("s1", "2026-10-10T09:00:00", 30))
    assert sync_live.tick() is None
    device("b")
    assert sync_live.tick() is None
    entries = session_history.load_all()
    assert [e["sessionId"] for e in entries] == ["s1"]
    assert entries[0]["startTime"] == "2026-10-10T09:00:00"       # local clock time round-trips
    assert entries[0]["violationLog"][0]["timestamp"] == "2026-10-10T09:00:00"
    assert entries[0]["processBlocklist"] == ["discord.exe"]


def test_task_minutes_are_the_same_on_both_computers(device, fake_logged_in, server):
    task = {"id": "task-1", "name": "usaco", "targetMinutes": 60, "recurrence": "daily", "weekdays": [],
            "targetMinutesHistory": []}
    day = datetime(2026, 10, 10).date()
    device("a")
    session_history.append_entry(_entry("s1", "2026-10-10T09:00:00", 30))
    sync_live.tick()
    device("b")
    session_history.append_entry(_entry("s2", "2026-10-10T14:00:00", 20))
    sync_live.tick()
    sync_live.tick()
    device("a")
    sync_live.tick()
    for name in ("a", "b"):
        device(name)
        assert tasks_store.logged_seconds_for_date(task, day, session_history.load_all()) == 50 * 60


def test_the_same_session_ending_on_both_is_filed_once(device, fake_logged_in, server):
    """Both computers run out the clock on a synced session: one entry, not two."""
    for name in ("a", "b"):
        device(name)
        session_history.append_entry(_entry("same", "2026-10-10T09:00:00", 25, endType="natural"))
    device("a")
    sync_live.tick()
    device("b")
    sync_live.tick()
    sync_live.tick()
    assert len(session_history.load_all()) == 1
    device("a")
    sync_live.tick()
    assert len(session_history.load_all()) == 1


def test_a_remote_copy_arriving_first_stops_the_local_natural_end_double_filing(device, fake_logged_in, server):
    device("a")
    session_history.append_entry(_entry("x", "2026-10-10T09:00:00", 25))
    sync_live.tick()
    device("b")
    sync_live.tick()
    session_history.append_entry(_entry("x", "2026-10-10T09:00:00", 25))  # b's own natural end, late
    assert len(session_history.load_all()) == 1


def test_old_entries_without_a_session_id_get_one_stable_identity(device):
    device("a")
    old = {"startTime": "2026-09-01T10:00:00", "endTime": "2026-09-01T10:30:00", "source": "manual"}
    assert session_history.entry_id(old) == session_history.entry_id(dict(old))
    assert session_history.entry_id(old).startswith("legacy-")


def test_existing_history_is_uploaded_once_and_not_repeated(device, fake_logged_in, server):
    device("a")
    for i in range(3):
        session_history.append_entry(_entry(f"s{i}", f"2026-10-0{i + 1}T09:00:00", 10))
    sync_live.tick()
    first = server.writes
    sync_live.tick()
    assert [m for m, _ in server.requests].count("POST") == 1
    assert server.writes == first


def test_history_rows_do_not_reach_the_slow_lane(device, fake_logged_in, server):
    device("a")
    session_history.append_entry(_entry("s1", "2026-10-10T09:00:00", 30))
    sync_live.tick()
    device("b")
    result = sync_client.sync_now()
    assert result.success and result.pulled == 0


def test_a_malformed_remote_entry_is_skipped_not_fatal(device, fake_logged_in, server):
    server[("session_history", "bad")] = {
        "table_name": "session_history", "sync_id": "bad", "device_id": "x", "is_deleted": False,
        "updated_at": datetime.now().astimezone().isoformat(), "data": {"nonsense": True},
    }
    device("a")
    assert sync_live.tick() is None
    assert session_history.load_all() == []


def test_calendar_event_sessions_keep_their_shared_event_id(device, fake_logged_in, server):
    device("a")
    session_history.append_entry(_entry("ev", "2026-10-10T09:00:00", 30, source="calendar-event", eventId="evt-uuid"))
    sync_live.tick()
    device("b")
    sync_live.tick()
    assert session_history.load_all()[0]["eventId"] == "evt-uuid"


def test_failures_report_a_reason_instead_of_raising(device, fake_logged_in, server):
    device("a")
    server.token = "something-else"          # every request now answers 401, refresh fails
    assert sync_live.tick() is not None
    server.token = "fake-token"
    assert sync_live.tick() is None


def test_nothing_runs_when_logged_out(device, monkeypatch):
    device("a")
    monkeypatch.setattr(auth_manager, "is_logged_in", lambda: False)
    assert sync_live.tick() == "not logged in"
