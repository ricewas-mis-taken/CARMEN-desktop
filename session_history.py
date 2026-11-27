"""Persisted log of completed focus sessions.

Each entry covers one session start-to-finish: when it started and ended,
the lock mode, the process/domain blocklists in effect, and every violation
that happened (with how long it took to get back on track, if it ever did).
Lives in session_history.json, appended to whenever a session ends —
manually (tray "End Session" / POST /session/end) or by running out the
clock — so it survives past whatever session_state.json currently holds.
"""
import hashlib
from datetime import datetime
import json
import logging
import os
import shutil
import threading
import time
import paths

HISTORY_PATH = paths.private_path("session_history.json")

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


def update_entry(session_entry_id, fields):
    """Changes fields on one filed session (found by entry_id). Returns True if it was found. Meant for
    small edits after the fact, such as saving a session as an attempt at a problem."""
    with _lock:
        history = list(_load_all_locked())
        for entry in history:
            if entry_id(entry) == session_entry_id:
                entry.update(fields)
                entry["editedAt"] = datetime.now().isoformat()
                break
        else:
            return False
        _save_all_locked(history)
    return True


def merge_remote(entries):
    """Adds sessions another device filed. Returns how many were new."""
    return _append_entries(entries)


def sync_stamp(entry):
    """When an entry last changed for the sync: its end, or a later edit made to it afterwards."""
    return max(entry.get("endTime") or "", entry.get("editedAt") or "")


def entries_ended_after(watermark):
    """Entries that ended or were edited after `watermark` (all of them when None)."""
    return [e for e in load_all(include_beside=True) if watermark is None or sync_stamp(e) > watermark]


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
                # An edit made to a filed session on another computer (saving it as an attempt at a problem)
                # replaces ours when it is newer.
                mine = next((i for i, e in enumerate(history) if entry_id(e) == entry_id(entry)), None)
                if (mine is not None and (entry.get("editedAt") or "") > (history[mine].get("editedAt") or "")
                        and history[mine].get("endType") != "superseded"):
                    history[mine] = entry
                    added += 1
                    continue
                # A session filed early as "superseded" is only a stub if the same session later finishes for
                # real: the longer, later entry replaces it instead of being dropped as a duplicate.
                same = next((i for i, e in enumerate(history) if entry_id(e) == entry_id(entry)), None)
                if (same is not None and history[same].get("endType") == "superseded"
                        and (entry.get("endTime") or "") > (history[same].get("endTime") or "")):
                    history[same] = entry
                    added += 1
                continue
            known.add(entry_id(entry))
            history.append(entry)
            added += 1
        if added:
            history.sort(key=lambda e: e.get("endTime") or e.get("startTime") or "")
            _save_all_locked(history)
        return added


def load_all(include_beside=False):
    """Returns all recorded sessions, oldest first. A review that was timed beside a session of the same task
    ("beside": True) is on file so the history can show it, but its time is already inside that session, so
    everything that adds time up leaves it out; the history page and the sync ask for it with include_beside."""
    with _lock:
        everything = _load_all_locked()
    return everything if include_beside else [e for e in everything if not e.get("beside")]


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
