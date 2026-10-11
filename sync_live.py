"""The fast lane of sync: things that must reach the other computer within
seconds, not minutes -- finished focus sessions (so a task's logged minutes
match on every computer) and the running session itself (start, pause,
resume, end, rule changes, waiting sessions), so two computers behave as one.

Same cloud table and same rules as sync_client's slower lane (newest write
wins, enforced by the database trigger), but its own loop, its own pull
cursor, and a small query: only this lane's table names, only rows newer than
the cursor. Nothing here needs new SQL.

The loop polls every few seconds all the time while logged in -- the other
computer cannot know a session started elsewhere unless it asks. A failure
(offline, database not set up yet) backs off quietly instead of retrying
every few seconds forever.
"""
import hashlib
import json
import os
import platform
import re
import threading
import time
from datetime import datetime, timedelta

import httpx

import auth_manager
import calendar_store
import device_id
import review_store
import screentime_store
import server_clock
import session_history
import session_manager
import sync_client
import sync_cloud
from calendar_log import logger

HISTORY_TABLE = "session_history"
LIVE_TABLE = "live_session"
LIVE_SYNC_ID = "current"          # one row per account: the state of "what is running"
REVIEW_TABLE = "live_review"      # the review being timed with no task session under it
LANE_TABLES = sync_cloud.FAST_LANE_TABLES
POLL_SECONDS = 5
MAX_BACKOFF_SECONDS = 60

_PRIVATE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "private")
CURSOR_PATH = os.path.join(_PRIVATE, "live_cursor.txt")
HISTORY_PUSHED_PATH = os.path.join(_PRIVATE, "live_history_pushed.txt")
LIVE_PUSHED_PATH = os.path.join(_PRIVATE, "live_state_pushed.txt")
REVIEW_PUSHED_PATH = os.path.join(_PRIVATE, "live_review_pushed.txt")
SCREENTIME_PUSHED_PATH = os.path.join(_PRIVATE, "live_screentime_pushed.json")
SCREENTIME_TABLE = "screentime"   # one row per computer per day; each computer writes only its own
SCREENTIME_PUSH_SECONDS = 60      # it changes every second; once a minute is plenty

_NAIVE_ISO = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?$")
_AWARE_ISO = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?(Z|[+-]\d{2}:\d{2})$")

_wake = threading.Event()
_enabled = True


# --- small file helpers ---

def _read(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read().strip() or None
    except OSError:
        return None


def _write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, path)


# --- timestamps: local naive <-> UTC on the wire, anywhere inside a record ---

def _convert(value, matches, convert):
    if isinstance(value, dict):
        return {k: _convert(v, matches, convert) for k, v in value.items()}
    if isinstance(value, list):
        return [_convert(v, matches, convert) for v in value]
    if isinstance(value, str) and matches.match(value):
        return convert(value)
    return value


def to_wire(value):
    return _convert(value, _NAIVE_ISO, sync_client._to_wire_ts)


def from_wire(value):
    return _convert(value, _AWARE_ISO, sync_client._from_wire_ts)


def history_entry_to_wire(entry):
    return to_wire(entry)


def history_entry_from_wire(data):
    return from_wire(data)


# --- gather / apply: finished sessions ---

def _gather_history():
    watermark = _read(HISTORY_PUSHED_PATH)
    entries = session_history.entries_ended_after(watermark)
    records = []
    for entry in entries:
        stamp = entry.get("endTime") or entry.get("startTime")
        if not stamp:
            continue
        records.append({
            "table_name": HISTORY_TABLE,
            "sync_id": session_history.entry_id(entry),
            "data": history_entry_to_wire(entry),
            "device_id": device_id.get_device_id(),
            "updated_at": sync_client._to_wire_ts(stamp),
            "is_deleted": False,
        })
    newest = max((e.get("endTime") or "" for e in entries), default="") or None
    return records, newest


def _apply_history(records):
    entries = []
    for record in records:
        data = record.get("data")
        if record.get("is_deleted") or not isinstance(data, dict):
            continue
        if not isinstance(data.get("startTime"), str) or not isinstance(data.get("endTime"), str):
            continue
        try:
            entry = history_entry_from_wire(data)
            entry["sessionId"] = entry.get("sessionId") or record["sync_id"]
            entries.append(entry)
        except Exception:
            logger.exception("sync_live: skipped a malformed history record")
    return session_history.merge_remote(entries) or 0


# --- gather / apply: the running session ---

def _shift(iso, seconds):
    return (datetime.fromisoformat(iso) + timedelta(seconds=seconds)).isoformat()


def _review_problem_sync_id(local_id):
    if local_id is None:
        return None
    with calendar_store._lock:
        review_store._get_conn()
        return sync_client._lookup_sync_id(calendar_store._get_conn(), "review_problems", local_id)


def _review_problem_local_id(sync_id):
    if not sync_id:
        return None
    with calendar_store._lock:
        review_store._get_conn()
        row = calendar_store._get_conn().execute(
            "SELECT id FROM review_problems WHERE sync_id = ? AND is_deleted = 0", (sync_id,)
        ).fetchone()
    return row["id"] if row else None


def _sessions_in(state):
    return [state] + [p for p in state.get("parkedSessions") or [] if isinstance(p, dict)]


def live_state_to_wire(snapshot):
    """The running state as the other computer reads it: times in the cloud's
    clock (so a computer with a wrong clock still agrees on when it ends), and
    the one id that is just a row number on this computer replaced by the
    row's shared id."""
    offset = server_clock.offset_seconds()
    wire = _convert(
        {k: v for k, v in snapshot.items() if k != "syncUpdatedAt"}, _NAIVE_ISO,
        lambda value: _shift(sync_client._to_wire_ts(value), offset),
    )
    for session in _sessions_in(wire):
        session["reviewProblemSyncId"] = _review_problem_sync_id(session.get("reviewProblemId"))
        session["reviewProblemId"] = None
    return wire


def live_state_from_wire(data):
    if not isinstance(data, dict):
        return None
    offset = server_clock.offset_seconds()
    state = _convert(
        data, _AWARE_ISO, lambda value: sync_client._from_wire_ts(_shift(value, -offset))
    )
    for session in _sessions_in(state):
        session["reviewProblemId"] = _review_problem_local_id(session.pop("reviewProblemSyncId", None))
    return state


def _gather_live():
    """The running state, if it changed here since it was last sent."""
    stamp = session_manager.local_stamp()
    if not stamp or not server_clock.is_newer(stamp, _read(LIVE_PUSHED_PATH)):
        return [], None
    snapshot = session_manager.export_live_state()
    stamp = snapshot.get("syncUpdatedAt") or stamp
    return [{
        "table_name": LIVE_TABLE,
        "sync_id": LIVE_SYNC_ID,
        "data": live_state_to_wire(snapshot),
        "device_id": device_id.get_device_id(),
        "updated_at": stamp,
        "is_deleted": False,
    }], stamp


def _apply_live(records):
    for record in records:
        if record.get("sync_id") != LIVE_SYNC_ID:
            continue
        try:
            state = live_state_from_wire(record.get("data"))
            if state is None:
                continue
            outcome = session_manager.apply_remote_state(state, record.get("updated_at"))
            if outcome == "applied":
                # Already the cloud's version: nothing to send back.
                _write(LIVE_PUSHED_PATH, record["updated_at"])
        except Exception:
            logger.exception("sync_live: could not apply the shared session state")


# --- gather / apply: a review being timed on its own ---

_pending_review = None     # a shared review whose problem has not reached this computer yet


def _gather_review():
    stamp = review_store.review_stamp()
    if not stamp or not server_clock.is_newer(stamp, _read(REVIEW_PUSHED_PATH)):
        return [], None
    exported = review_store.export_active_review()
    offset = server_clock.offset_seconds()
    wire = {"endedToken": exported["endedToken"], "review": None}
    review = exported["review"]
    if review is not None:
        problem_sync_id = _review_problem_sync_id(review["problemId"])
        if not problem_sync_id:
            return [], None          # the problem has not been synced yet: try again next pass
        shifted = _convert(
            {k: v for k, v in review.items() if k != "problemId"}, _NAIVE_ISO,
            lambda value: _shift(sync_client._to_wire_ts(value), offset),
        )
        wire["review"] = {**shifted, "problemSyncId": problem_sync_id}
    stamp = exported["stamp"] or stamp
    return [{
        "table_name": REVIEW_TABLE, "sync_id": LIVE_SYNC_ID, "data": wire,
        "device_id": device_id.get_device_id(), "updated_at": stamp, "is_deleted": False,
    }], stamp


def _apply_review(records):
    global _pending_review
    for record in records:
        if record.get("sync_id") != LIVE_SYNC_ID or not isinstance(record.get("data"), dict):
            continue
        try:
            data = record["data"]
            review = data.get("review")
            if review is not None:
                if not isinstance(review, dict):
                    continue
                offset = server_clock.offset_seconds()
                local = _convert(
                    review, _AWARE_ISO, lambda value: sync_client._from_wire_ts(_shift(value, -offset))
                )
                local["problemId"] = _review_problem_local_id(local.pop("problemSyncId", None))
                if local["problemId"] is None:
                    _pending_review = record      # wait for the problem to arrive
                    continue
                review = local
            _pending_review = None
            outcome = review_store.apply_remote_review(review, data.get("endedToken"), record.get("updated_at"))
            if outcome == "applied":
                _write(REVIEW_PUSHED_PATH, record["updated_at"])
        except Exception:
            logger.exception("sync_live: could not apply the shared review")


# --- gather / apply: screen time, one row per computer per day ---

_last_screentime_push = 0.0


def _gather_screentime():
    """This computer's days whose totals changed since they were last sent.
    Returns (records, {day: fingerprint}) -- empty until a minute has passed."""
    global _last_screentime_push
    if _last_screentime_push and time.monotonic() - _last_screentime_push < SCREENTIME_PUSH_SECONDS:
        return [], {}
    try:
        with open(SCREENTIME_PUSHED_PATH, "r", encoding="utf-8") as f:
            sent = json.load(f)
        sent = sent if isinstance(sent, dict) else {}
    except (OSError, json.JSONDecodeError):
        sent = {}
    me = device_id.get_device_id()
    records, fingerprints = [], {}
    for day, bucket in screentime_store.export_days().items():
        if not (bucket["apps"] or bucket["domains"]):
            continue
        fingerprint = hashlib.sha1(json.dumps(bucket, sort_keys=True).encode("utf-8")).hexdigest()
        if sent.get(day) == fingerprint:
            continue
        fingerprints[day] = fingerprint
        records.append({
            "table_name": SCREENTIME_TABLE, "sync_id": f"{me}:{day}",
            "data": {"deviceId": me, "deviceName": platform.node() or "Computer", "day": day, **bucket},
            "device_id": me, "updated_at": server_clock.next_stamp(), "is_deleted": False,
        })
    return records, fingerprints


def _remember_screentime_sent(fingerprints):
    global _last_screentime_push
    _last_screentime_push = time.monotonic()
    if not fingerprints:
        return
    try:
        with open(SCREENTIME_PUSHED_PATH, "r", encoding="utf-8") as f:
            sent = json.load(f)
        sent = sent if isinstance(sent, dict) else {}
    except (OSError, json.JSONDecodeError):
        sent = {}
    sent.update(fingerprints)
    _write(SCREENTIME_PUSHED_PATH, json.dumps(sent))


def _apply_screentime(records):
    me = device_id.get_device_id()
    for record in records:
        data = record.get("data")
        if record.get("is_deleted") or not isinstance(data, dict) or data.get("deviceId") == me:
            continue
        screentime_store.merge_remote_day(
            data.get("deviceId"), data.get("deviceName"), data.get("day"), data.get("apps"), data.get("domains")
        )


# --- one pass ---

def _apply(records):
    by_table = {}
    for record in records:
        by_table.setdefault(record["table_name"], []).append(record)
    _apply_history(by_table.get(HISTORY_TABLE, []))
    _apply_live(by_table.get(LIVE_TABLE, []))
    _apply_review(([_pending_review] if _pending_review else []) + by_table.get(REVIEW_TABLE, []))
    _apply_screentime(by_table.get(SCREENTIME_TABLE, []))


def tick():
    """One push-and-pull pass. Returns None on success or a short reason."""
    if not auth_manager.is_logged_in():
        return "not logged in"
    token = auth_manager.get_access_token()
    user = auth_manager.get_current_user()
    if not token or not user or not sync_client._check_sync_owner(user["id"]):
        return "not signed in as this device's account"

    push_records, history_watermark = _gather_history()
    live_records, live_stamp = _gather_live()
    push_records += live_records
    review_records, review_stamp = _gather_review()
    push_records += review_records
    screentime_records, screentime_fingerprints = _gather_screentime()
    push_records += screentime_records
    refreshed = False
    while True:
        try:
            with httpx.Client(timeout=15.0) as client:
                sync_cloud.ensure_ready(client, token)
                sync_cloud.push(client, token, user["id"], push_records)
                records, cursor = sync_cloud.pull(client, token, _read(CURSOR_PATH), tables=LANE_TABLES)
            break
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 401 and not refreshed:
                refreshed = True
                if auth_manager.refresh_access_token():
                    token = auth_manager.get_access_token()
                    if token:
                        continue
            return f"cloud answered {exc.response.status_code}"
        except (httpx.HTTPError, sync_cloud.NotConfigured) as exc:
            return f"unreachable: {type(exc).__name__}"

    if history_watermark:
        _write(HISTORY_PUSHED_PATH, history_watermark)
    if live_stamp:
        _write(LIVE_PUSHED_PATH, live_stamp)
    if review_stamp:
        _write(REVIEW_PUSHED_PATH, review_stamp)
    _remember_screentime_sent(screentime_fingerprints)
    _apply(records)
    if cursor:
        _write(CURSOR_PATH, cursor)
    return None


# --- the loop ---

session_manager.add_change_listener(lambda: _wake.set())
review_store.add_change_listener(lambda: _wake.set())


def enable():
    global _enabled
    _enabled = True
    _wake.set()


def disable():
    global _enabled
    _enabled = False


def poke():
    """Run a pass now instead of waiting out the interval."""
    _wake.set()


def start(stop_event):
    thread = threading.Thread(target=_run, args=(stop_event,), daemon=True)
    thread.start()
    return thread


def _run(stop_event):
    failures = 0
    last_problem = None
    while not stop_event.is_set():
        problem = None
        if _enabled:
            try:
                problem = tick()
            except Exception:
                logger.exception("sync_live: pass crashed")
                problem = "crashed"
        failures = failures + 1 if problem and problem != "not logged in" else 0
        if problem != last_problem and problem and problem != "not logged in":
            logger.warning("sync_live: %s (will keep trying quietly)", problem)
        last_problem = problem
        wait = min(MAX_BACKOFF_SECONDS, POLL_SECONDS * (2 ** min(failures, 4)))
        _wake.wait(wait)
        _wake.clear()
