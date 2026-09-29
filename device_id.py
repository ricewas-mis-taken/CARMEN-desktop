"""Generates and persists a stable per-install device identifier.

Used to stamp the deviceId/device_id column on every row a soft-delete or
sync write touches, so a future sync module (and a human debugging a
conflict) can tell which machine made which change. Not a hardware ID --
just a random UUID minted once per install and cached to disk.
"""
import os
import threading
import uuid

DEVICE_ID_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "private", "device_id.txt")

_cached_id = None
# Guards the whole check-exists / generate / write / cache sequence below --
# get_device_id() is called from many independent threads (Flask worker
# threads via review_store.py/calendar_store.py's write paths, the Qt main
# thread, background schedulers), and on a brand-new install (no
# DEVICE_ID_PATH on disk yet) two threads racing this function before either
# has written the file could each generate a *different* uuid, both try to
# write it, and whichever write lands last silently wins -- leaving
# _cached_id (in at least one of those threads) permanently out of sync with
# what's actually on disk. A real threading.Lock, not the lazy
# check-a-flag-then-set-it pattern review_store.py's own
# _ensure_active_sessions_loaded() uses elsewhere in this codebase -- that
# pattern isn't itself race-free either (see review_store.py's fix for the
# same class of bug), so it's not something to copy here verbatim.
_lock = threading.Lock()


def get_device_id():
    global _cached_id
    if _cached_id:
        return _cached_id

    with _lock:
        # Re-check now that _lock is held -- another thread may have already
        # done the full first-run generate-and-write below while this thread
        # was waiting on the lock, in which case there's nothing left to do
        # but return what it produced.
        if _cached_id:
            return _cached_id

        if os.path.exists(DEVICE_ID_PATH):
            try:
                with open(DEVICE_ID_PATH, "r", encoding="utf-8") as f:
                    existing = f.read().strip()
                if existing:
                    _cached_id = existing
                    return _cached_id
            except OSError:
                pass

        new_id = uuid.uuid4().hex
        os.makedirs(os.path.dirname(DEVICE_ID_PATH), exist_ok=True)
        tmp_path = DEVICE_ID_PATH + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            f.write(new_id)
        os.replace(tmp_path, DEVICE_ID_PATH)
        _cached_id = new_id
        return _cached_id
