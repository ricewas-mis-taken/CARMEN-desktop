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
import uuid
from datetime import datetime

import device_id
from calendar_log import logger

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "private", "calendar.db")

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
        # private/ (gitignored, holds every real data file) won't exist yet
        # on a fresh clone -- sqlite3.connect() doesn't create its parent
        # directory itself.
        os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
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
            process_blocklist TEXT NOT NULL DEFAULT '[]',
            domain_whitelist TEXT NOT NULL DEFAULT '[]',
            warning_minutes INTEGER
        );

        CREATE INDEX IF NOT EXISTS idx_events_start ON events(start);
        CREATE INDEX IF NOT EXISTS idx_events_deleted ON events(deleted_at);
        """
    )
    conn.commit()

    # Add process_blocklist to focus_profiles for DBs that predate the
    # apps-are-a-blocklist restructure -- CREATE TABLE IF NOT EXISTS above
    # is a no-op against an already-existing table, so an existing
    # focus_profiles still only has the old process_whitelist column
    # without this. Domains stay an allow list (domain_whitelist), so that
    # column needs no migration -- it already exists under this same name.
    # Deliberately does not migrate process_whitelist's old values over: an
    # "allow only these apps" list means the opposite of a "block these
    # apps" list, so every event's saved app block list starts fresh empty
    # (nothing blocked) rather than silently inverting what it used to allow.
    focus_cols = {row[1] for row in conn.execute("PRAGMA table_info(focus_profiles)").fetchall()}
    if "process_blocklist" not in focus_cols:
        conn.execute("ALTER TABLE focus_profiles ADD COLUMN process_blocklist TEXT NOT NULL DEFAULT '[]'")
        conn.commit()

    # Sync scaffolding (multi-device sync, Phase 2 of that work). Unlike
    # review_store.py's tables, nothing here needs a separate sync_id --
    # every table below already uses a TEXT uuid4().hex primary key
    # (events.id, reminders.id) or a natural globally-unique key
    # (focus_profiles.event_id), so the existing primary key already is a
    # safe thing to sync on directly.
    #
    # events already carries updated_at (kept current by every save_event()
    # write) and deleted_at (already the soft-delete tombstone -- non-NULL
    # means deleted). Both are reused as-is for sync rather than duplicated;
    # events only gains device_id here. reminders/focus_profiles never had
    # any timestamp columns at all, so they gain the full set.
    #
    # save_event() now populates reminders.device_id/is_deleted directly
    # (see its diff-based reminder handling below); focus_profiles rows are
    # still hard-deleted on disable rather than tombstoned -- narrower in
    # scope than the reminder/task/board/event deletes fixed in Phase 3
    # Part A, left for a later pass.
    events_cols = {row[1] for row in conn.execute("PRAGMA table_info(events)").fetchall()}
    if "device_id" not in events_cols:
        conn.execute("ALTER TABLE events ADD COLUMN device_id TEXT")
        conn.commit()

    reminders_cols = {row[1] for row in conn.execute("PRAGMA table_info(reminders)").fetchall()}
    if "updated_at" not in reminders_cols:
        conn.execute("ALTER TABLE reminders ADD COLUMN updated_at TEXT")
        conn.execute("ALTER TABLE reminders ADD COLUMN device_id TEXT")
        conn.execute("ALTER TABLE reminders ADD COLUMN is_deleted INTEGER NOT NULL DEFAULT 0")
        conn.commit()
        # Best-effort backfill -- reminders never tracked their own write
        # time before this, so "now" (migration time) is the closest
        # available stand-in for pre-existing rows, not a real history.
        conn.execute(
            "UPDATE reminders SET updated_at = ? WHERE updated_at IS NULL", (datetime.now().isoformat(),)
        )
        conn.commit()

    focus_cols = {row[1] for row in conn.execute("PRAGMA table_info(focus_profiles)").fetchall()}
    if "updated_at" not in focus_cols:
        conn.execute("ALTER TABLE focus_profiles ADD COLUMN updated_at TEXT")
        conn.execute("ALTER TABLE focus_profiles ADD COLUMN device_id TEXT")
        conn.execute("ALTER TABLE focus_profiles ADD COLUMN is_deleted INTEGER NOT NULL DEFAULT 0")
        conn.commit()
        conn.execute(
            "UPDATE focus_profiles SET updated_at = ? WHERE updated_at IS NULL", (datetime.now().isoformat(),)
        )
        conn.commit()


def _row_to_event(conn, row):
    reminders = [
        r["offset_minutes"]
        for r in conn.execute(
            "SELECT offset_minutes FROM reminders WHERE event_id = ? AND is_deleted = 0 ORDER BY offset_minutes",
            (row["id"],),
        )
    ]
    focus = conn.execute("SELECT * FROM focus_profiles WHERE event_id = ?", (row["id"],)).fetchone()
    focus_profile = None
    if focus is not None:
        focus_profile = {
            "enabled": bool(focus["enabled"]),
            "lockMode": focus["lock_mode"],
            "processBlocklist": json.loads(focus["process_blocklist"]),
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

            # Diff against existing reminder rows instead of DELETE-then-
            # reinsert-everything -- the old approach rewrote every reminder
            # on every single edit (even ones that changed nothing about
            # reminders), which would generate spurious tombstones and
            # device_id churn once these rows matter for sync. Only rows
            # that actually change get touched.
            requested = {int(o) for o in (event.get("reminderOffsets") or [])}
            existing_rows = conn.execute(
                "SELECT id, offset_minutes, is_deleted FROM reminders WHERE event_id = ?", (event_id,)
            ).fetchall()
            active_offsets = {r["offset_minutes"] for r in existing_rows if not r["is_deleted"]}
            deleted_row_by_offset = {r["offset_minutes"]: r["id"] for r in existing_rows if r["is_deleted"]}
            this_device = device_id.get_device_id()

            for offset in requested - active_offsets:
                if offset in deleted_row_by_offset:
                    conn.execute(
                        "UPDATE reminders SET is_deleted = 0, updated_at = ?, device_id = ? WHERE id = ?",
                        (now, this_device, deleted_row_by_offset[offset]),
                    )
                else:
                    conn.execute(
                        "INSERT INTO reminders (id, event_id, offset_minutes, updated_at, device_id, is_deleted) "
                        "VALUES (?, ?, ?, ?, ?, 0)",
                        (uuid.uuid4().hex, event_id, offset, now, this_device),
                    )

            for offset in active_offsets - requested:
                conn.execute(
                    "UPDATE reminders SET is_deleted = 1, updated_at = ?, device_id = ? "
                    "WHERE event_id = ? AND offset_minutes = ? AND is_deleted = 0",
                    (now, this_device, event_id, offset),
                )

            focus = event.get("focusProfile")
            if focus and focus.get("enabled"):
                conn.execute(
                    """
                    INSERT INTO focus_profiles (event_id, enabled, lock_mode, process_blocklist, domain_whitelist, warning_minutes)
                    VALUES (?, 1, ?, ?, ?, ?)
                    ON CONFLICT(event_id) DO UPDATE SET
                        enabled=1, lock_mode=excluded.lock_mode,
                        process_blocklist=excluded.process_blocklist,
                        domain_whitelist=excluded.domain_whitelist,
                        warning_minutes=excluded.warning_minutes
                    """,
                    (
                        event_id, focus.get("lockMode", "soft"),
                        json.dumps(focus.get("processBlocklist", [])),
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
    """Used to permanently DELETE events past their undo grace period.
    Now a no-op by design: a hard delete here can never propagate to other
    devices, so a soft-deleted event (deleted_at set) is left in place
    indefinitely once SOFT_DELETE_GRACE_SECONDS passes -- undo_delete_event()
    simply becomes unreachable from the UI at that point. Permanent physical
    purge is a deliberate future cleanup task (once sync_now() can confirm
    every known device has seen the tombstone), not an oversight. Kept as a
    function (rather than removed) since calendar_scheduler.py's polling
    loop already calls it every tick and that call site is harmless to leave
    in place for when real purge logic lands here."""
    return


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
