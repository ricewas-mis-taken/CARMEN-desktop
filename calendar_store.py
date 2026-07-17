"""SQLite-backed store for calendar events, reminders, and per-event focus
profiles. Lives in calendar.db, sibling to config.json/session_state.json.

Chosen over another JSON file (like config.json/session_state.json) because
events scale — potentially hundreds once recurrence is expanded — and need
range queries (month grid, day view, scheduler lookahead) that a flat JSON
blob would make increasingly slow to scan.

All writes go through a single module-level lock, same pattern as
session_manager.py's _lock, since both the GUI thread (event editor) and the
background scheduler thread hit this module concurrently. Every write is
wrapped in try/except and logged to calendar_errors.log instead of crashing
the caller — the scheduler thread in particular must never die from a write
failure, matching the JSONDecodeError-corruption lesson baked into
config.py/session_manager.py's own load paths.
"""
import json
import os
import sqlite3
import threading
import time
import uuid
from datetime import datetime

from calendar_log import logger

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "calendar.db")

# How long a soft-deleted event stays recoverable via undo_delete_event()
# before being purged for good. The UI's undo toast is shown for 10s; this
# is intentionally longer so a slow click (or a toast that was momentarily
# covered by another window) still has a shot at succeeding.
SOFT_DELETE_GRACE_SECONDS = 20

_lock = threading.Lock()
_conn = None


def _get_conn():
    global _conn
    if _conn is None:
        _conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        _conn.row_factory = sqlite3.Row
        _conn.execute("PRAGMA journal_mode=WAL")
        _init_schema(_conn)
    return _conn


def _init_schema(conn):
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS events (
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
        );

        CREATE TABLE IF NOT EXISTS reminders (
            id TEXT PRIMARY KEY,
            event_id TEXT NOT NULL REFERENCES events(id) ON DELETE CASCADE,
            offset_minutes INTEGER NOT NULL
        );

        CREATE TABLE IF NOT EXISTS focus_profiles (
            event_id TEXT PRIMARY KEY REFERENCES events(id) ON DELETE CASCADE,
            enabled INTEGER NOT NULL DEFAULT 0,
            lock_mode TEXT NOT NULL DEFAULT 'soft',
            process_whitelist TEXT NOT NULL DEFAULT '[]',
            domain_whitelist TEXT NOT NULL DEFAULT '[]',
            warning_minutes INTEGER
        );

        CREATE INDEX IF NOT EXISTS idx_events_start ON events(start);
        CREATE INDEX IF NOT EXISTS idx_events_deleted ON events(deleted_at);
        """
    )
    conn.commit()


def _row_to_event(conn, row):
    reminders = [
        r["offset_minutes"]
        for r in conn.execute(
            "SELECT offset_minutes FROM reminders WHERE event_id = ? ORDER BY offset_minutes", (row["id"],)
        )
    ]
    focus = conn.execute("SELECT * FROM focus_profiles WHERE event_id = ?", (row["id"],)).fetchone()
    focus_profile = None
    if focus is not None:
        focus_profile = {
            "enabled": bool(focus["enabled"]),
            "lockMode": focus["lock_mode"],
            "processWhitelist": json.loads(focus["process_whitelist"]),
            "domainWhitelist": json.loads(focus["domain_whitelist"]),
            "warningMinutes": focus["warning_minutes"],
        }
    return {
        "id": row["id"],
        "title": row["title"],
        "start": row["start"],
        "end": row["end"],
        "allDay": bool(row["all_day"]),
        "color": row["color"],
        "notes": row["notes"],
        "rrule": row["rrule"],
        "reminderOffsets": reminders,
        "focusProfile": focus_profile,
        "deletedAt": row["deleted_at"],
    }


def list_events(include_deleted=False):
    """All events (not occurrences — recurring events appear once, with
    their rrule intact; see calendar_recurrence.py for occurrence
    expansion)."""
    with _lock:
        try:
            conn = _get_conn()
            where = "" if include_deleted else "WHERE deleted_at IS NULL"
            rows = conn.execute(f"SELECT * FROM events {where} ORDER BY start").fetchall()
            return [_row_to_event(conn, r) for r in rows]
        except Exception:
            logger.exception("list_events failed")
            return []


def get_event(event_id):
    with _lock:
        try:
            conn = _get_conn()
            row = conn.execute("SELECT * FROM events WHERE id = ?", (event_id,)).fetchone()
            return _row_to_event(conn, row) if row else None
        except Exception:
            logger.exception("get_event failed for %s", event_id)
            return None


def save_event(event):
    """Insert or update an event plus its reminders and focus profile.
    event["id"] may be None/absent for a new event — one is generated.
    Returns the saved event's id, or None on failure."""
    event_id = event.get("id") or uuid.uuid4().hex
    now = datetime.now().isoformat()

    with _lock:
        try:
            conn = _get_conn()
            existing = conn.execute("SELECT created_at FROM events WHERE id = ?", (event_id,)).fetchone()
            created_at = existing["created_at"] if existing else now

            conn.execute(
                """
                INSERT INTO events (id, title, start, end, all_day, color, notes, rrule, created_at, updated_at, deleted_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)
                ON CONFLICT(id) DO UPDATE SET
                    title=excluded.title, start=excluded.start, end=excluded.end,
                    all_day=excluded.all_day, color=excluded.color, notes=excluded.notes,
                    rrule=excluded.rrule, updated_at=excluded.updated_at, deleted_at=NULL
                """,
                (
                    event_id, event["title"], event["start"], event["end"],
                    int(bool(event.get("allDay"))), event.get("color", "#2d8cff"),
                    event.get("notes", ""), event.get("rrule"), created_at, now,
                ),
            )

            conn.execute("DELETE FROM reminders WHERE event_id = ?", (event_id,))
            for offset in event.get("reminderOffsets", []) or []:
                conn.execute(
                    "INSERT INTO reminders (id, event_id, offset_minutes) VALUES (?, ?, ?)",
                    (uuid.uuid4().hex, event_id, int(offset)),
                )

            focus = event.get("focusProfile")
            if focus and focus.get("enabled"):
                conn.execute(
                    """
                    INSERT INTO focus_profiles (event_id, enabled, lock_mode, process_whitelist, domain_whitelist, warning_minutes)
                    VALUES (?, 1, ?, ?, ?, ?)
                    ON CONFLICT(event_id) DO UPDATE SET
                        enabled=1, lock_mode=excluded.lock_mode,
                        process_whitelist=excluded.process_whitelist,
                        domain_whitelist=excluded.domain_whitelist,
                        warning_minutes=excluded.warning_minutes
                    """,
                    (
                        event_id, focus.get("lockMode", "soft"),
                        json.dumps(focus.get("processWhitelist", [])),
                        json.dumps(focus.get("domainWhitelist", [])),
                        focus.get("warningMinutes"),
                    ),
                )
            else:
                conn.execute("DELETE FROM focus_profiles WHERE event_id = ?", (event_id,))

            conn.commit()
            return event_id
        except Exception:
            logger.exception("save_event failed for %s", event.get("title"))
            try:
                conn.rollback()
            except Exception:
                pass
            return None


def soft_delete_event(event_id):
    """Marks an event deleted without removing it — recoverable via
    undo_delete_event() for SOFT_DELETE_GRACE_SECONDS, matching the UI's
    10-second undo toast."""
    with _lock:
        try:
            conn = _get_conn()
            conn.execute(
                "UPDATE events SET deleted_at = ? WHERE id = ?", (datetime.now().isoformat(), event_id)
            )
            conn.commit()
            return True
        except Exception:
            logger.exception("soft_delete_event failed for %s", event_id)
            return False


def undo_delete_event(event_id):
    with _lock:
        try:
            conn = _get_conn()
            conn.execute("UPDATE events SET deleted_at = NULL WHERE id = ?", (event_id,))
            conn.commit()
            return True
        except Exception:
            logger.exception("undo_delete_event failed for %s", event_id)
            return False


def purge_expired_soft_deletes():
    """Permanently removes events whose soft-delete grace period has
    elapsed. Meant to be called periodically from the scheduler loop —
    failures here are logged and swallowed, same as every other write in
    this module, so a purge failure never takes down the scheduler."""
    with _lock:
        try:
            conn = _get_conn()
            cutoff = time.time() - SOFT_DELETE_GRACE_SECONDS
            rows = conn.execute("SELECT id, deleted_at FROM events WHERE deleted_at IS NOT NULL").fetchall()
            for row in rows:
                try:
                    deleted_ts = datetime.fromisoformat(row["deleted_at"]).timestamp()
                except ValueError:
                    continue
                if deleted_ts <= cutoff:
                    conn.execute("DELETE FROM events WHERE id = ?", (row["id"],))
            conn.commit()
        except Exception:
            logger.exception("purge_expired_soft_deletes failed")


def export_db(dest_path):
    with _lock:
        try:
            conn = _get_conn()
            dest = sqlite3.connect(dest_path)
            with dest:
                conn.backup(dest)
            dest.close()
            return True
        except Exception:
            logger.exception("export_db failed to %s", dest_path)
            return False


def import_db(src_path):
    with _lock:
        try:
            global _conn
            src = sqlite3.connect(src_path)
            if _conn is not None:
                _conn.close()
            dest = sqlite3.connect(DB_PATH)
            with dest:
                src.backup(dest)
            src.close()
            dest.close()
            _conn = None
            _get_conn()
            return True
        except Exception:
            logger.exception("import_db failed from %s", src_path)
            return False
