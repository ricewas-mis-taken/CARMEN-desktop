"""Persisted log of completed focus sessions.

Each entry covers one session start-to-finish: when it started and ended,
the lock mode, the process/domain blocklists in effect, and every violation
that happened (with how long it took to get back on track, if it ever did).
Lives in session_history.json, appended to whenever a session ends —
manually (tray "End Session" / POST /session/end) or by running out the
clock — so it survives past whatever session_state.json currently holds.
"""
import hashlib
import json
import logging
import os
import shutil
import threading
import time

HISTORY_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "private", "session_history.json")

_lock = threading.Lock()


logger = logging.getLogger(__name__)


def entry_id(entry):
    """Identity of a history entry across devices: the sessionId stamped when
    the session started, or -- for an entry filed before sessions had ids -- a
    fingerprint of its own start/end, so every device derives the same id."""
    session_id = entry.get("sessionId")
    if session_id:
        return session_id
    raw = "|".join(str(entry.get(k)) for k in (
        "startTime", "endTime", "source", "eventId", "eventTitle", "reviewProblemName"))
    return "legacy-" + hashlib.sha1(raw.encode("utf-8")).hexdigest()[:24]


def append_entry(entry):
    """Files one finished session. A session already on file (same id) is not
    added twice -- two synced devices can both see the same session end."""
    _append_entries([entry])


def merge_remote(entries):
    """Adds sessions another device filed. Returns how many were new."""
    return _append_entries(entries)


def entries_ended_after(watermark):
    """Entries whose endTime is later than `watermark` (all of them when None)."""
    return [e for e in load_all() if watermark is None or (e.get("endTime") or "") > watermark]


def _append_entries(new_entries):
    with _lock:
        history = None
        # A read failure must never be mistaken for "no history yet": the
        # save below rewrites the whole file, so that would destroy every
        # earlier session. Retry a transient OSError (e.g. another process
        # briefly locking the file); preserve a corrupt file before starting
        # a new one.
        for attempt in range(3):
            if not os.path.exists(HISTORY_PATH):
                history = []
                break
            try:
                with open(HISTORY_PATH, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if not isinstance(data, list):
                    raise json.JSONDecodeError("history is not a list", "", 0)
                history = data
                break
            except json.JSONDecodeError:
                try:
                    shutil.copy2(HISTORY_PATH, f"{HISTORY_PATH}.corrupt-{int(time.time())}")
                except OSError:
                    logger.exception("could not back up corrupt session history")
                    return
                history = []
                break
            except OSError:
                time.sleep(0.05)
        if history is None:
            logger.error("session history unreadable; not overwriting it with a new entry")
            return
        known = {entry_id(e) for e in history}
        added = 0
        for entry in new_entries:
            if entry_id(entry) in known:
                continue
            known.add(entry_id(entry))
            history.append(entry)
            added += 1
        if added:
            history.sort(key=lambda e: e.get("endTime") or e.get("startTime") or "")
            _save_all_locked(history)
        return added


def load_all():
    """Returns all recorded sessions, oldest first."""
    with _lock:
        return _load_all_locked()


# The parsed file, with the (path, mtime, size) it was read at. The Tasks tab
# reloads the history every second on the window thread, and the file only grows
# (it is written with indent=2), so re-parsing it each time got slower with use.
_cache = {"key": None, "data": []}


def _load_all_locked():
    try:
        stat = os.stat(HISTORY_PATH)
    except OSError:
        return []
    key = (HISTORY_PATH, stat.st_mtime_ns, stat.st_size)
    if _cache["key"] == key:
        return list(_cache["data"])   # a copy of the list; the entries are shared, read-only
    try:
        with open(HISTORY_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        # A corrupt/truncated file must not crash the whole app — treat it
        # as an empty history rather than propagating the error up through
        # session_manager.end_session() (which runs on every session end).
        return []
    data = data if isinstance(data, list) else []
    _cache["key"], _cache["data"] = key, data
    return list(data)


def _save_all_locked(history):
    # private/ (gitignored, holds every real data file) won't exist yet on
    # a fresh clone.
    os.makedirs(os.path.dirname(HISTORY_PATH), exist_ok=True)
    tmp_path = HISTORY_PATH + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(history, f, indent=2)
    os.replace(tmp_path, HISTORY_PATH)
    _cache["key"] = None
