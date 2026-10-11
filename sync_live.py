"""The fast lane of sync: things that must reach the other computer within
seconds, not minutes -- finished focus sessions (so a task's logged minutes
match on every computer) and, see sync_live_session, the running session.

Same cloud table and same rules as sync_client's slower lane (newest write
wins, enforced by the database trigger), but its own loop, its own pull
cursor, and a small query: only this lane's table names, only rows newer than
the cursor. Nothing here needs new SQL.

The loop polls every few seconds all the time while logged in -- the other
computer cannot know a session started elsewhere unless it asks. A failure
(offline, database not set up yet) backs off quietly instead of retrying
every few seconds forever.
"""
import os
import re
import threading

import httpx

import auth_manager
import device_id
import session_history
import sync_client
import sync_cloud
from calendar_log import logger

HISTORY_TABLE = "session_history"
LANE_TABLES = (HISTORY_TABLE,)
POLL_SECONDS = 5
MAX_BACKOFF_SECONDS = 60

_PRIVATE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "private")
CURSOR_PATH = os.path.join(_PRIVATE, "live_cursor.txt")
HISTORY_PUSHED_PATH = os.path.join(_PRIVATE, "live_history_pushed.txt")

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


# --- one pass ---

def _apply(records):
    by_table = {}
    for record in records:
        by_table.setdefault(record["table_name"], []).append(record)
    _apply_history(by_table.get(HISTORY_TABLE, []))


def tick():
    """One push-and-pull pass. Returns None on success or a short reason."""
    if not auth_manager.is_logged_in():
        return "not logged in"
    token = auth_manager.get_access_token()
    user = auth_manager.get_current_user()
    if not token or not user or not sync_client._check_sync_owner(user["id"]):
        return "not signed in as this device's account"

    push_records, history_watermark = _gather_history()
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
    _apply(records)
    if cursor:
        _write(CURSOR_PATH, cursor)
    return None


# --- the loop ---

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
