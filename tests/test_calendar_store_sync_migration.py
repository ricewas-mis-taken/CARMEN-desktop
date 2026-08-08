"""Tests for the multi-device sync scaffolding migration added to
calendar_store's tables -- non-destructiveness against a DB that predates
it (events gains device_id only, reusing its existing updated_at/deleted_at;
reminders/focus_profiles gain the full updated_at/device_id/is_deleted set,
since they never had timestamps before)."""
import sqlite3

import calendar_store


def test_events_gain_device_id_without_losing_data(isolate_calendar_db):
    conn = sqlite3.connect(calendar_store.DB_PATH)
    conn.execute(
        """
        CREATE TABLE events (
            id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            start TEXT NOT NULL,
            end TEXT NOT NULL,
            all_day INTEGER NOT NULL DEFAULT 0,
            color TEXT NOT NULL DEFAULT '#2d8cff',
            notes TEXT NOT NULL DEFAULT '',
            rrule TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            deleted_at TEXT
        )
        """
    )
    conn.execute(
        "INSERT INTO events (id, title, start, end, created_at, updated_at) "
        "VALUES ('evt1', 'Standup', '2020-01-01T09:00:00', '2020-01-01T09:15:00', "
        "'2020-01-01T00:00:00', '2020-01-01T00:00:00')"
    )
    conn.commit()
    conn.close()

    events = calendar_store.list_events()
    assert len(events) == 1
    assert events[0]["title"] == "Standup"

    conn = calendar_store._get_conn()
    row = conn.execute("SELECT device_id, updated_at, deleted_at FROM events WHERE id = 'evt1'").fetchone()
    assert row["device_id"] is None
    assert row["updated_at"] == "2020-01-01T00:00:00"  # untouched, not duplicated/reset
    assert row["deleted_at"] is None


def test_reminders_and_focus_profiles_gain_full_sync_columns(isolate_calendar_db):
    conn = sqlite3.connect(calendar_store.DB_PATH)
    conn.execute(
        """
        CREATE TABLE events (
            id TEXT PRIMARY KEY, title TEXT NOT NULL, start TEXT NOT NULL, end TEXT NOT NULL,
            all_day INTEGER NOT NULL DEFAULT 0, color TEXT NOT NULL DEFAULT '#2d8cff',
            notes TEXT NOT NULL DEFAULT '', rrule TEXT,
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL, deleted_at TEXT
        )
        """
    )
    conn.execute(
        "INSERT INTO events (id, title, start, end, created_at, updated_at) "
        "VALUES ('evt1', 'Standup', '2020-01-01T09:00:00', '2020-01-01T09:15:00', "
        "'2020-01-01T00:00:00', '2020-01-01T00:00:00')"
    )
    conn.execute(
        "CREATE TABLE reminders (id TEXT PRIMARY KEY, event_id TEXT NOT NULL, offset_minutes INTEGER NOT NULL)"
    )
    conn.execute("INSERT INTO reminders (id, event_id, offset_minutes) VALUES ('rem1', 'evt1', 10)")
    conn.execute(
        """
        CREATE TABLE focus_profiles (
            event_id TEXT PRIMARY KEY, enabled INTEGER NOT NULL DEFAULT 0,
            lock_mode TEXT NOT NULL DEFAULT 'soft', process_blocklist TEXT NOT NULL DEFAULT '[]',
            domain_whitelist TEXT NOT NULL DEFAULT '[]', warning_minutes INTEGER
        )
        """
    )
    conn.execute(
        "INSERT INTO focus_profiles (event_id, enabled, lock_mode) VALUES ('evt1', 1, 'hard')"
    )
    conn.commit()
    conn.close()

    event = calendar_store.get_event("evt1")
    assert event["reminderOffsets"] == [10]
    assert event["focusProfile"]["lockMode"] == "hard"

    conn = calendar_store._get_conn()
    rem = conn.execute(
        "SELECT updated_at, device_id, is_deleted FROM reminders WHERE id = 'rem1'"
    ).fetchone()
    assert rem["updated_at"] is not None
    assert rem["device_id"] is None
    assert rem["is_deleted"] == 0

    focus = conn.execute(
        "SELECT updated_at, device_id, is_deleted FROM focus_profiles WHERE event_id = 'evt1'"
    ).fetchone()
    assert focus["updated_at"] is not None
    assert focus["device_id"] is None
    assert focus["is_deleted"] == 0
