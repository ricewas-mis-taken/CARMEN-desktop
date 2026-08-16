"""Phase 4 Part A -- gathers local changes and exchanges them with
sync_server (auth_manager.py handles login; sync_server is the separate
FastAPI backend this module talks to over HTTP).

sync_now() is the only thing callers (the future UI, sync_scheduler.py,
scripts/manual_sync_check.py) need. It:
  1. Requires auth_manager.is_logged_in() -- returns a failure SyncResult
     (not_logged_in=True) rather than raising if not.
  2. Reads the last successful sync's watermark from private/last_sync.txt.
  3. Gathers every local record changed since that watermark, across
     tasks.json, board.json, and calendar.db's events/reminders/
     focus_profiles/review_* tables.
  4. Pushes them to {SYNC_SERVER_URL}/sync/push.
  5. Pulls everything changed since the watermark from
     {SYNC_SERVER_URL}/sync/pull and applies it locally, last-write-wins.
  6. On success, advances the watermark and returns counts.

Never raises out to a caller -- any network/server problem is caught and
turned into a failure SyncResult, logged via calendar_log's shared logger
(the same one calendar_store/review_store already use for background-
thread failures that must never crash the app).

Timestamps: every local store already writes naive datetime.now().iso-
format() strings in the machine's own local time (see tasks_store.py/
board_store.py/calendar_store.py/review_store.py) -- this module does not
change that. It only converts at the wire boundary: _to_wire_ts()/
_from_wire_ts() translate between that local-naive format and the aware
UTC ISO format sync_server expects (both for record updated_at values and
for the ?since= watermark), so every existing write path and every
existing "changed since" comparison elsewhere in the app is untouched.

Review tables (review_topics/review_subjects/review_problems/
review_sessions) use INTEGER PRIMARY KEY locally, which collides across
devices, so they carry a separate sync_id column (review_store.py's
_add_sync_columns()). Child rows reference their parent via the parent's
sync_id (not the local integer FK) in the pushed `data`, and applying a
pulled child record resolves that sync_id back to a local integer id via
a lookup -- see _apply_review_subjects/_apply_review_problems/
_apply_review_sessions. Rows created before Phase 4's write-path wiring
(review_store.py) can still have a NULL sync_id; _ensure_row_sync_meta()
mints and saves one lazily the first time such a row is gathered.

Known gaps, matching how Phase 3 scoped focus_profiles: delete_topic()
still hard-deletes (no tombstone), so topic/subject/problem/session
deletions don't propagate through sync yet. review_problems'
descriptionPhotoPath is a local file path -- the path syncs, but the
photo file itself does not (no blob storage in this phase), so a pulled
problem with a photo will show a broken image on the receiving device
until file sync is built.
"""
import logging
import os
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

import httpx
from dotenv import load_dotenv

import auth_manager
import board_store
import calendar_store
import device_id
import review_store
import tasks_store
from calendar_log import logger as calendar_logger

logger = logging.getLogger("carmen_sync")

# httpx logs one INFO line per outgoing request -- with push/pull making
# one request per changed record, that floods any terminal where the
# root/httpx logger ends up at INFO (sync_server's uvicorn process,
# scripts/manual_sync_check.py, or the main app if it ever configures
# logging that broadly). Quiet httpx specifically here, at the point
# this module actually starts making those requests, so every caller of
# sync_client gets this for free instead of each needing its own fix.
logging.getLogger("httpx").setLevel(logging.WARNING)

ENV_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "private", ".env")
load_dotenv(ENV_PATH)

SYNC_SERVER_URL = os.environ.get("SYNC_SERVER_URL", "http://127.0.0.1:8420").rstrip("/")

# How often sync_scheduler.py's background timer calls sync_now(). A named
# constant instead of a literal in sync_scheduler.py so it's discoverable
# from the module that actually defines what "syncing" means.
SYNC_INTERVAL_SECONDS = 5 * 60

LAST_SYNC_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "private", "last_sync.txt")
_cached_last_sync = None

REVIEW_TABLES = ("review_topics", "review_subjects", "review_problems", "review_sessions")


@dataclass
class SyncResult:
    success: bool
    pushed: int
    pulled: int
    skipped: int
    error: Optional[str]
    not_logged_in: bool = False


# --- watermark persistence (private/last_sync.txt, mirrors device_id.py's
# single-value cache-file pattern) ---

def _load_last_sync():
    global _cached_last_sync
    if _cached_last_sync is not None:
        return _cached_last_sync
    if os.path.exists(LAST_SYNC_PATH):
        try:
            with open(LAST_SYNC_PATH, "r", encoding="utf-8") as f:
                value = f.read().strip()
            if value:
                _cached_last_sync = value
                return value
        except OSError:
            pass
    return None


def _save_last_sync(wire_ts):
    global _cached_last_sync
    os.makedirs(os.path.dirname(LAST_SYNC_PATH), exist_ok=True)
    tmp_path = LAST_SYNC_PATH + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        f.write(wire_ts)
    os.replace(tmp_path, LAST_SYNC_PATH)
    _cached_last_sync = wire_ts


# --- timestamp conversion (local-naive <-> wire aware-UTC) ---

def _to_wire_ts(local_iso):
    if not local_iso:
        return None
    naive = datetime.fromisoformat(local_iso)
    return naive.astimezone().astimezone(timezone.utc).isoformat()


def _from_wire_ts(wire_iso):
    if not wire_iso:
        return None
    aware = datetime.fromisoformat(wire_iso.replace("Z", "+00:00"))
    return aware.astimezone().replace(tzinfo=None).isoformat()


def _is_newer(candidate_iso, cutoff_iso):
    if not candidate_iso:
        return False
    if not cutoff_iso:
        return True
    return datetime.fromisoformat(candidate_iso) > datetime.fromisoformat(cutoff_iso)


# --- gather: tasks.json / board.json ---

def _gather_json_store(records_source, table_name, id_field, cutoff_local):
    records = []
    for item in records_source:
        updated_at = item.get("updatedAt")
        if not _is_newer(updated_at, cutoff_local):
            continue
        data = {k: v for k, v in item.items() if k not in (id_field, "updatedAt", "deviceId", "isDeleted")}
        records.append({
            "table_name": table_name,
            "sync_id": item[id_field],
            "data": data,
            "device_id": item.get("deviceId") or device_id.get_device_id(),
            "updated_at": _to_wire_ts(updated_at),
            "is_deleted": bool(item.get("isDeleted")),
        })
    return records


def _gather_tasks(cutoff_local):
    return _gather_json_store(tasks_store.load_tasks(include_deleted=True), "tasks", "id", cutoff_local)


def _gather_board(cutoff_local):
    return _gather_json_store(board_store.load_board(include_deleted=True), "board", "id", cutoff_local)


# --- gather: calendar.db (events / reminders / focus_profiles) ---

def _gather_events(conn, cutoff_local):
    if cutoff_local:
        rows = conn.execute("SELECT * FROM events WHERE updated_at > ?", (cutoff_local,)).fetchall()
    else:
        rows = conn.execute("SELECT * FROM events").fetchall()
    records = []
    for row in rows:
        data = {
            "title": row["title"], "start": row["start"], "end": row["end"],
            "allDay": bool(row["all_day"]), "color": row["color"], "notes": row["notes"],
            "rrule": row["rrule"], "createdAt": row["created_at"],
        }
        records.append({
            "table_name": "events",
            "sync_id": row["id"],
            "data": data,
            "device_id": row["device_id"] or device_id.get_device_id(),
            "updated_at": _to_wire_ts(row["updated_at"]),
            "is_deleted": row["deleted_at"] is not None,
        })
    return records


def _gather_reminders(conn, cutoff_local):
    if cutoff_local:
        rows = conn.execute(
            "SELECT * FROM reminders WHERE updated_at IS NULL OR updated_at > ?", (cutoff_local,)
        ).fetchall()
    else:
        rows = conn.execute("SELECT * FROM reminders").fetchall()
    records = []
    for row in rows:
        updated_at = row["updated_at"] or datetime.now().isoformat()
        data = {"eventId": row["event_id"], "offsetMinutes": row["offset_minutes"]}
        records.append({
            "table_name": "reminders",
            "sync_id": row["id"],
            "data": data,
            "device_id": row["device_id"] or device_id.get_device_id(),
            "updated_at": _to_wire_ts(updated_at),
            "is_deleted": bool(row["is_deleted"]),
        })
    return records


def _gather_focus_profiles(conn, cutoff_local):
    if cutoff_local:
        rows = conn.execute(
            "SELECT * FROM focus_profiles WHERE updated_at IS NULL OR updated_at > ?", (cutoff_local,)
        ).fetchall()
    else:
        rows = conn.execute("SELECT * FROM focus_profiles").fetchall()
    records = []
    for row in rows:
        updated_at = row["updated_at"] or datetime.now().isoformat()
        data = {
            "eventId": row["event_id"], "enabled": bool(row["enabled"]), "lockMode": row["lock_mode"],
            "processBlocklist": row["process_blocklist"], "domainWhitelist": row["domain_whitelist"],
            "warningMinutes": row["warning_minutes"],
        }
        records.append({
            "table_name": "focus_profiles",
            "sync_id": row["event_id"],
            "data": data,
            "device_id": row["device_id"] or device_id.get_device_id(),
            "updated_at": _to_wire_ts(updated_at),
            "is_deleted": bool(row["is_deleted"]),
        })
    return records


# --- gather: calendar.db (review_topics / review_subjects / review_problems / review_sessions) ---

def _lookup_sync_id(conn, table, local_id):
    if local_id is None:
        return None
    row = conn.execute(f"SELECT sync_id FROM {table} WHERE id = ?", (local_id,)).fetchone()
    return row["sync_id"] if row else None


def _ensure_row_sync_meta(conn, table, row):
    """Mints sync_id/updated_at/device_id for a row that predates Phase 4's
    review_store write-path wiring (still possible for anything created
    between the Phase 2 migration and that fix). Returns the current
    (possibly just-written) values."""
    sync_id = row["sync_id"]
    updated_at = row["updated_at"]
    dev = row["device_id"]
    changed = False
    if not sync_id:
        sync_id = uuid.uuid4().hex
        changed = True
    if not updated_at:
        updated_at = datetime.now().isoformat()
        changed = True
    if not dev:
        dev = device_id.get_device_id()
        changed = True
    if changed:
        conn.execute(
            f"UPDATE {table} SET sync_id = ?, updated_at = ?, device_id = ? WHERE id = ?",
            (sync_id, updated_at, dev, row["id"]),
        )
        conn.commit()
    return sync_id, updated_at, dev


def _gather_review_topics(conn, cutoff_local):
    rows = conn.execute("SELECT * FROM review_topics WHERE is_deleted = 0").fetchall()
    records = []
    for row in rows:
        needs_meta = not row["sync_id"] or not row["updated_at"] or not row["device_id"]
        if not needs_meta and not _is_newer(row["updated_at"], cutoff_local):
            continue
        sync_id, updated_at, dev = _ensure_row_sync_meta(conn, "review_topics", row)
        data = {"name": row["name"], "orderIndex": row["order_index"], "linkedTaskId": row["linked_task_id"]}
        records.append({
            "table_name": "review_topics", "sync_id": sync_id, "data": data,
            "device_id": dev, "updated_at": _to_wire_ts(updated_at), "is_deleted": bool(row["is_deleted"]),
        })
    return records


def _gather_review_subjects(conn, cutoff_local):
    rows = conn.execute("SELECT * FROM review_subjects WHERE is_deleted = 0").fetchall()
    records = []
    for row in rows:
        needs_meta = not row["sync_id"] or not row["updated_at"] or not row["device_id"]
        if not needs_meta and not _is_newer(row["updated_at"], cutoff_local):
            continue
        topic_sync_id = _lookup_sync_id(conn, "review_topics", row["topic_id"])
        if not topic_sync_id:
            logger.warning("sync_client: review_subject %s has no syncable parent topic, skipping push", row["id"])
            continue
        sync_id, updated_at, dev = _ensure_row_sync_meta(conn, "review_subjects", row)
        data = {
            "name": row["name"], "color": row["color"], "linkedTaskId": row["linked_task_id"],
            "topicSyncId": topic_sync_id,
        }
        records.append({
            "table_name": "review_subjects", "sync_id": sync_id, "data": data,
            "device_id": dev, "updated_at": _to_wire_ts(updated_at), "is_deleted": bool(row["is_deleted"]),
        })
    return records


def _gather_review_problems(conn, cutoff_local):
    rows = conn.execute("SELECT * FROM review_problems WHERE is_deleted = 0").fetchall()
    records = []
    for row in rows:
        needs_meta = not row["sync_id"] or not row["updated_at"] or not row["device_id"]
        if not needs_meta and not _is_newer(row["updated_at"], cutoff_local):
            continue
        topic_sync_id = _lookup_sync_id(conn, "review_topics", row["topic_id"])
        subject_sync_id = _lookup_sync_id(conn, "review_subjects", row["subject_id"])
        if not topic_sync_id or not subject_sync_id:
            logger.warning("sync_client: review_problem %s has no syncable parent topic/subject, skipping push", row["id"])
            continue
        sync_id, updated_at, dev = _ensure_row_sync_meta(conn, "review_problems", row)
        data = {
            "name": row["name"], "stars": row["stars"], "descriptionType": row["description_type"],
            "descriptionText": row["description_text"], "descriptionPhotoPath": row["description_photo_path"],
            "descriptionLink": row["description_link"], "dateAdded": row["date_added"],
            "reviewCount": row["review_count"], "lastReviewedAt": row["last_reviewed_at"],
            "fastestTimeSeconds": row["fastest_time_seconds"], "fastestTimeIsSolved": row["fastest_time_is_solved"],
            "scheduleStage": row["schedule_stage"], "nextReviewDate": row["next_review_date"],
            "firstAttemptSeconds": row["first_attempt_seconds"], "firstAttemptShakiness": row["first_attempt_shakiness"],
            "firstAttemptSelfSolved": row["first_attempt_self_solved"],
            "topicSyncId": topic_sync_id, "subjectSyncId": subject_sync_id,
        }
        records.append({
            "table_name": "review_problems", "sync_id": sync_id, "data": data,
            "device_id": dev, "updated_at": _to_wire_ts(updated_at), "is_deleted": bool(row["is_deleted"]),
        })
    return records


def _gather_review_sessions(conn, cutoff_local):
    rows = conn.execute("SELECT * FROM review_sessions WHERE is_deleted = 0").fetchall()
    records = []
    for row in rows:
        needs_meta = not row["sync_id"] or not row["updated_at"] or not row["device_id"]
        if not needs_meta and not _is_newer(row["updated_at"], cutoff_local):
            continue
        problem_sync_id = _lookup_sync_id(conn, "review_problems", row["problem_id"])
        if not problem_sync_id:
            logger.warning("sync_client: review_session %s has no syncable parent problem, skipping push", row["id"])
            continue
        sync_id, updated_at, dev = _ensure_row_sync_meta(conn, "review_sessions", row)
        data = {
            "startedAt": row["started_at"], "finishedAt": row["finished_at"],
            "durationSeconds": row["duration_seconds"],
            "selfSolved": row["self_solved"], "shakiness": row["shakiness"],
            "problemSyncId": problem_sync_id,
        }
        records.append({
            "table_name": "review_sessions", "sync_id": sync_id, "data": data,
            "device_id": dev, "updated_at": _to_wire_ts(updated_at), "is_deleted": bool(row["is_deleted"]),
        })
    return records


def _gather_calendar_records(cutoff_local):
    """Everything in calendar.db, gathered under a single lock acquisition
    (calendar_store's own -- reused rather than a private one, same reason
    review_store.py shares it: the GUI thread and this sync call must never
    interleave writes on the same sqlite connection). Uses raw conn.execute
    throughout rather than calendar_store's/review_store's public
    functions, since those each acquire this same non-reentrant lock
    themselves."""
    with calendar_store._lock:
        review_store._get_conn()  # ensures the review_* tables exist even if the Review tab was never opened
        conn = calendar_store._get_conn()
        records = []
        records += _gather_events(conn, cutoff_local)
        records += _gather_reminders(conn, cutoff_local)
        records += _gather_focus_profiles(conn, cutoff_local)
        # Parent-before-child, since subjects/problems/sessions look up
        # their parent's sync_id from the current DB state.
        records += _gather_review_topics(conn, cutoff_local)
        records += _gather_review_subjects(conn, cutoff_local)
        records += _gather_review_problems(conn, cutoff_local)
        records += _gather_review_sessions(conn, cutoff_local)
        return records


def _gather_all(cutoff_local):
    return _gather_tasks(cutoff_local) + _gather_board(cutoff_local) + _gather_calendar_records(cutoff_local)


# --- apply: tasks.json / board.json ---

def _apply_json_store(records, load_fn, save_fn, id_field):
    if not records:
        return 0, 0
    local = load_fn(include_deleted=True)
    index_by_id = {item[id_field]: i for i, item in enumerate(local)}
    applied = skipped = 0
    changed = False
    for record in records:
        incoming_updated_at = _from_wire_ts(record["updated_at"])
        idx = index_by_id.get(record["sync_id"])
        existing = local[idx] if idx is not None else None
        if existing is not None and not _is_newer(incoming_updated_at, existing.get("updatedAt")):
            skipped += 1
            continue
        new_item = dict(record["data"])
        new_item[id_field] = record["sync_id"]
        new_item["updatedAt"] = incoming_updated_at
        new_item["deviceId"] = record["device_id"]
        new_item["isDeleted"] = record["is_deleted"]
        if idx is not None:
            local[idx] = new_item
        else:
            index_by_id[record["sync_id"]] = len(local)
            local.append(new_item)
        changed = True
        applied += 1
    if changed:
        save_fn(local)
    return applied, skipped


def _apply_tasks(records):
    return _apply_json_store(records, tasks_store.load_tasks, tasks_store.save_tasks, "id")


def _apply_board(records):
    return _apply_json_store(records, board_store.load_board, board_store.save_board, "id")


# --- apply: calendar.db ---

def _apply_events(conn, records):
    applied = skipped = 0
    for record in records:
        incoming_updated_at = _from_wire_ts(record["updated_at"])
        row = conn.execute("SELECT updated_at FROM events WHERE id = ?", (record["sync_id"],)).fetchone()
        if row and not _is_newer(incoming_updated_at, row["updated_at"]):
            skipped += 1
            continue
        data = record["data"]
        deleted_at = incoming_updated_at if record["is_deleted"] else None
        conn.execute(
            """
            INSERT INTO events (id, title, start, end, all_day, color, notes, rrule, created_at, updated_at, deleted_at, device_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                title=excluded.title, start=excluded.start, end=excluded.end, all_day=excluded.all_day,
                color=excluded.color, notes=excluded.notes, rrule=excluded.rrule,
                updated_at=excluded.updated_at, deleted_at=excluded.deleted_at, device_id=excluded.device_id
            """,
            (
                record["sync_id"], data["title"], data["start"], data["end"], int(bool(data.get("allDay"))),
                data.get("color", "#2d8cff"), data.get("notes", ""), data.get("rrule"),
                data.get("createdAt") or incoming_updated_at, incoming_updated_at, deleted_at, record["device_id"],
            ),
        )
        applied += 1
    if applied:
        conn.commit()
    return applied, skipped


def _apply_reminders(conn, records):
    applied = skipped = 0
    for record in records:
        incoming_updated_at = _from_wire_ts(record["updated_at"])
        row = conn.execute("SELECT updated_at FROM reminders WHERE id = ?", (record["sync_id"],)).fetchone()
        if row and row["updated_at"] and not _is_newer(incoming_updated_at, row["updated_at"]):
            skipped += 1
            continue
        data = record["data"]
        conn.execute(
            """
            INSERT INTO reminders (id, event_id, offset_minutes, updated_at, device_id, is_deleted)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                event_id=excluded.event_id, offset_minutes=excluded.offset_minutes,
                updated_at=excluded.updated_at, device_id=excluded.device_id, is_deleted=excluded.is_deleted
            """,
            (
                record["sync_id"], data["eventId"], data["offsetMinutes"],
                incoming_updated_at, record["device_id"], int(record["is_deleted"]),
            ),
        )
        applied += 1
    if applied:
        conn.commit()
    return applied, skipped


def _apply_focus_profiles(conn, records):
    applied = skipped = 0
    for record in records:
        incoming_updated_at = _from_wire_ts(record["updated_at"])
        row = conn.execute(
            "SELECT updated_at FROM focus_profiles WHERE event_id = ?", (record["sync_id"],)
        ).fetchone()
        if row and row["updated_at"] and not _is_newer(incoming_updated_at, row["updated_at"]):
            skipped += 1
            continue
        data = record["data"]
        conn.execute(
            """
            INSERT INTO focus_profiles
                (event_id, enabled, lock_mode, process_blocklist, domain_whitelist, warning_minutes,
                 updated_at, device_id, is_deleted)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(event_id) DO UPDATE SET
                enabled=excluded.enabled, lock_mode=excluded.lock_mode,
                process_blocklist=excluded.process_blocklist, domain_whitelist=excluded.domain_whitelist,
                warning_minutes=excluded.warning_minutes, updated_at=excluded.updated_at,
                device_id=excluded.device_id, is_deleted=excluded.is_deleted
            """,
            (
                record["sync_id"], int(bool(data.get("enabled"))), data.get("lockMode", "soft"),
                data.get("processBlocklist", "[]"), data.get("domainWhitelist", "[]"), data.get("warningMinutes"),
                incoming_updated_at, record["device_id"], int(record["is_deleted"]),
            ),
        )
        applied += 1
    if applied:
        conn.commit()
    return applied, skipped


def _apply_review_topics(conn, records):
    applied = skipped = 0
    for record in records:
        incoming_updated_at = _from_wire_ts(record["updated_at"])
        row = conn.execute("SELECT id, updated_at FROM review_topics WHERE sync_id = ?", (record["sync_id"],)).fetchone()
        if row and row["updated_at"] and not _is_newer(incoming_updated_at, row["updated_at"]):
            skipped += 1
            continue
        data = record["data"]
        if row:
            conn.execute(
                "UPDATE review_topics SET name=?, order_index=?, linked_task_id=?, updated_at=?, device_id=?, is_deleted=? "
                "WHERE id=?",
                (
                    data["name"], data["orderIndex"], data.get("linkedTaskId"),
                    incoming_updated_at, record["device_id"], int(record["is_deleted"]), row["id"],
                ),
            )
        else:
            conn.execute(
                "INSERT INTO review_topics (name, order_index, linked_task_id, sync_id, updated_at, device_id, is_deleted) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    data["name"], data["orderIndex"], data.get("linkedTaskId"),
                    record["sync_id"], incoming_updated_at, record["device_id"], int(record["is_deleted"]),
                ),
            )
        applied += 1
    if applied:
        conn.commit()
    return applied, skipped


def _apply_review_subjects(conn, records):
    applied = skipped = 0
    for record in records:
        data = record["data"]
        topic_row = conn.execute(
            "SELECT id FROM review_topics WHERE sync_id = ?", (data.get("topicSyncId"),)
        ).fetchone()
        if not topic_row:
            logger.warning(
                "sync_client: skipping pulled review_subject %s -- parent topic not found locally", record["sync_id"]
            )
            skipped += 1
            continue
        incoming_updated_at = _from_wire_ts(record["updated_at"])
        row = conn.execute("SELECT id, updated_at FROM review_subjects WHERE sync_id = ?", (record["sync_id"],)).fetchone()
        if row and row["updated_at"] and not _is_newer(incoming_updated_at, row["updated_at"]):
            skipped += 1
            continue
        if row:
            conn.execute(
                "UPDATE review_subjects SET topic_id=?, name=?, color=?, linked_task_id=?, "
                "updated_at=?, device_id=?, is_deleted=? WHERE id=?",
                (
                    topic_row["id"], data["name"], data["color"], data.get("linkedTaskId"),
                    incoming_updated_at, record["device_id"], int(record["is_deleted"]), row["id"],
                ),
            )
        else:
            conn.execute(
                "INSERT INTO review_subjects (topic_id, name, color, linked_task_id, sync_id, updated_at, device_id, is_deleted) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    topic_row["id"], data["name"], data["color"], data.get("linkedTaskId"),
                    record["sync_id"], incoming_updated_at, record["device_id"], int(record["is_deleted"]),
                ),
            )
        applied += 1
    if applied:
        conn.commit()
    return applied, skipped


_PROBLEM_FIELDS = (
    "name", "stars", "description_type", "description_text", "description_photo_path", "description_link",
    "date_added", "review_count", "last_reviewed_at", "fastest_time_seconds", "fastest_time_is_solved",
    "schedule_stage", "next_review_date", "first_attempt_seconds", "first_attempt_shakiness", "first_attempt_self_solved",
)


def _apply_review_problems(conn, records):
    applied = skipped = 0
    for record in records:
        data = record["data"]
        topic_row = conn.execute("SELECT id FROM review_topics WHERE sync_id = ?", (data.get("topicSyncId"),)).fetchone()
        subject_row = conn.execute("SELECT id FROM review_subjects WHERE sync_id = ?", (data.get("subjectSyncId"),)).fetchone()
        if not topic_row or not subject_row:
            logger.warning(
                "sync_client: skipping pulled review_problem %s -- parent topic/subject not found locally", record["sync_id"]
            )
            skipped += 1
            continue
        incoming_updated_at = _from_wire_ts(record["updated_at"])
        row = conn.execute("SELECT id, updated_at FROM review_problems WHERE sync_id = ?", (record["sync_id"],)).fetchone()
        if row and row["updated_at"] and not _is_newer(incoming_updated_at, row["updated_at"]):
            skipped += 1
            continue
        values = (
            topic_row["id"], subject_row["id"], data["name"], data["stars"], data["descriptionType"],
            data.get("descriptionText"), data.get("descriptionPhotoPath"), data.get("descriptionLink"),
            data.get("dateAdded"), data.get("reviewCount", 0), data.get("lastReviewedAt"),
            data.get("fastestTimeSeconds"), data.get("fastestTimeIsSolved"),
            data.get("scheduleStage", 0), data.get("nextReviewDate"),
            data.get("firstAttemptSeconds"), data.get("firstAttemptShakiness"), data.get("firstAttemptSelfSolved"),
        )
        if row:
            conn.execute(
                """
                UPDATE review_problems SET
                    topic_id=?, subject_id=?, name=?, stars=?, description_type=?,
                    description_text=?, description_photo_path=?, description_link=?,
                    date_added=?, review_count=?, last_reviewed_at=?,
                    fastest_time_seconds=?, fastest_time_is_solved=?,
                    schedule_stage=?, next_review_date=?,
                    first_attempt_seconds=?, first_attempt_shakiness=?, first_attempt_self_solved=?,
                    updated_at=?, device_id=?, is_deleted=?
                WHERE id=?
                """,
                values + (incoming_updated_at, record["device_id"], int(record["is_deleted"]), row["id"]),
            )
        else:
            conn.execute(
                """
                INSERT INTO review_problems (
                    topic_id, subject_id, name, stars, description_type,
                    description_text, description_photo_path, description_link,
                    date_added, review_count, last_reviewed_at,
                    fastest_time_seconds, fastest_time_is_solved,
                    schedule_stage, next_review_date,
                    first_attempt_seconds, first_attempt_shakiness, first_attempt_self_solved,
                    sync_id, updated_at, device_id, is_deleted
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                values + (record["sync_id"], incoming_updated_at, record["device_id"], int(record["is_deleted"])),
            )
        applied += 1
    if applied:
        conn.commit()
    return applied, skipped


def _apply_review_sessions(conn, records):
    applied = skipped = 0
    for record in records:
        data = record["data"]
        problem_row = conn.execute(
            "SELECT id FROM review_problems WHERE sync_id = ?", (data.get("problemSyncId"),)
        ).fetchone()
        if not problem_row:
            logger.warning(
                "sync_client: skipping pulled review_session %s -- parent problem not found locally", record["sync_id"]
            )
            skipped += 1
            continue
        incoming_updated_at = _from_wire_ts(record["updated_at"])
        row = conn.execute("SELECT id, updated_at FROM review_sessions WHERE sync_id = ?", (record["sync_id"],)).fetchone()
        if row and row["updated_at"] and not _is_newer(incoming_updated_at, row["updated_at"]):
            skipped += 1
            continue
        values = (
            problem_row["id"], data["startedAt"], data["finishedAt"], data["durationSeconds"],
            data.get("selfSolved"), data.get("shakiness"),
        )
        if row:
            conn.execute(
                "UPDATE review_sessions SET problem_id=?, started_at=?, finished_at=?, duration_seconds=?, "
                "self_solved=?, shakiness=?, updated_at=?, device_id=?, is_deleted=? WHERE id=?",
                values + (incoming_updated_at, record["device_id"], int(record["is_deleted"]), row["id"]),
            )
        else:
            conn.execute(
                "INSERT INTO review_sessions "
                "(problem_id, started_at, finished_at, duration_seconds, self_solved, shakiness, "
                " sync_id, updated_at, device_id, is_deleted) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                values + (record["sync_id"], incoming_updated_at, record["device_id"], int(record["is_deleted"])),
            )
        applied += 1
    if applied:
        conn.commit()
    return applied, skipped


def _apply_calendar_records(by_table):
    with calendar_store._lock:
        review_store._get_conn()
        conn = calendar_store._get_conn()
        applied = skipped = 0
        # Parent-before-child, mirroring the gather order.
        for table_name, apply_fn in (
            ("events", _apply_events),
            ("reminders", _apply_reminders),
            ("focus_profiles", _apply_focus_profiles),
            ("review_topics", _apply_review_topics),
            ("review_subjects", _apply_review_subjects),
            ("review_problems", _apply_review_problems),
            ("review_sessions", _apply_review_sessions),
        ):
            a, s = apply_fn(conn, by_table.get(table_name, []))
            applied += a
            skipped += s
        return applied, skipped


def _apply_all(pulled_records):
    by_table = {}
    for record in pulled_records:
        by_table.setdefault(record["table_name"], []).append(record)

    applied = skipped = 0
    a, s = _apply_tasks(by_table.get("tasks", []))
    applied += a
    skipped += s
    a, s = _apply_board(by_table.get("board", []))
    applied += a
    skipped += s
    a, s = _apply_calendar_records(by_table)
    applied += a
    skipped += s
    return applied, skipped


# --- the public entry point ---

def sync_now():
    """Never raises. Returns a SyncResult describing what happened."""
    if not auth_manager.is_logged_in():
        return SyncResult(success=False, pushed=0, pulled=0, skipped=0, error="Not logged in.", not_logged_in=True)

    token = auth_manager.get_access_token()
    if not token:
        return SyncResult(success=False, pushed=0, pulled=0, skipped=0, error="Not logged in.", not_logged_in=True)

    sync_start_wire = datetime.now(timezone.utc).isoformat()
    last_sync_wire = _load_last_sync()
    cutoff_local = _from_wire_ts(last_sync_wire)

    try:
        push_records = _gather_all(cutoff_local)
    except Exception:
        calendar_logger.exception("sync_client.sync_now: failed gathering local changes")
        return SyncResult(success=False, pushed=0, pulled=0, skipped=0, error="Sync failed while reading local data.")

    pushed = skipped_push = 0
    try:
        headers = {"Authorization": f"Bearer {token}"}
        with httpx.Client(timeout=20.0) as client:
            if push_records:
                resp = client.post(f"{SYNC_SERVER_URL}/sync/push", headers=headers, json=push_records)
                resp.raise_for_status()
                push_result = resp.json()
                pushed = len(push_result.get("accepted", []))
                skipped_push = len(push_result.get("skipped", []))

            params = {"since": last_sync_wire} if last_sync_wire else {}
            resp = client.get(f"{SYNC_SERVER_URL}/sync/pull", headers=headers, params=params)
            resp.raise_for_status()
            pulled_records = resp.json()
    except httpx.HTTPError as exc:
        logger.warning("sync_client.sync_now: couldn't reach sync server at %s: %s", SYNC_SERVER_URL, exc)
        return SyncResult(success=False, pushed=0, pulled=0, skipped=0, error="Couldn't reach the sync server.")

    try:
        pulled, skipped_pull = _apply_all(pulled_records)
    except Exception:
        calendar_logger.exception("sync_client.sync_now: failed applying pulled changes")
        return SyncResult(
            success=False, pushed=pushed, pulled=0, skipped=skipped_push,
            error="Sync failed while applying incoming changes.",
        )

    _save_last_sync(sync_start_wire)
    return SyncResult(
        success=True, pushed=pushed, pulled=pulled, skipped=skipped_push + skipped_pull, error=None,
    )
