"""Persisted log of completed focus sessions.

Each entry covers one session start-to-finish: when it started and ended,
the lock mode, the process/domain whitelists in effect, and every violation
that happened (with how long it took to get back on track, if it ever did).
Lives in session_history.json, appended to whenever a session ends —
manually (tray "End Session" / POST /session/end) or by running out the
clock — so it survives past whatever session_state.json currently holds.
"""
import json
import os
import threading

HISTORY_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "session_history.json")

_lock = threading.Lock()


def append_entry(entry):
    with _lock:
        history = _load_all_locked()
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
    tmp_path = HISTORY_PATH + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(history, f, indent=2)
    os.replace(tmp_path, HISTORY_PATH)
