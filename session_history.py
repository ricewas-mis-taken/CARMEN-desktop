"""Persisted log of completed focus sessions.

Each entry covers one session start-to-finish: when it started and ended,
the lock mode, the process/domain blocklists in effect, and every violation
that happened (with how long it took to get back on track, if it ever did).
Lives in session_history.json, appended to whenever a session ends —
manually (tray "End Session" / POST /session/end) or by running out the
clock — so it survives past whatever session_state.json currently holds.
"""
import json
import logging
import os
import shutil
import threading
import time

HISTORY_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "private", "session_history.json")

_lock = threading.Lock()


logger = logging.getLogger(__name__)


def append_entry(entry):
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
        history.append(entry)
        _save_all_locked(history)


def load_all():
    """Returns all recorded sessions, oldest first."""
    with _lock:
        return _load_all_locked()


def _load_all_locked():
    if not os.path.exists(HISTORY_PATH):
        return []
    try:
        with open(HISTORY_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        # A corrupt/truncated file must not crash the whole app — treat it
        # as an empty history rather than propagating the error up through
        # session_manager.end_session() (which runs on every session end).
        return []
    return data if isinstance(data, list) else []


def _save_all_locked(history):
    # private/ (gitignored, holds every real data file) won't exist yet on
    # a fresh clone.
    os.makedirs(os.path.dirname(HISTORY_PATH), exist_ok=True)
    tmp_path = HISTORY_PATH + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(history, f, indent=2)
    os.replace(tmp_path, HISTORY_PATH)
