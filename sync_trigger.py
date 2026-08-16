"""Push-on-change: fires sync_client.sync_now() shortly after a meaningful
local write, instead of waiting for sync_scheduler's 5-minute timer.
tasks_store.py/board_store.py/calendar_store.py/review_store.py call
note_change() from their own save-completing write functions (see each
module for exactly which ones -- not every function, and never on a
per-keystroke edit).

Debounced with a single shared threading.Timer: rapid successive changes
(e.g. checking off five tasks in a row) collapse into one sync_now() call
DEBOUNCE_SECONDS after the last change, not one call per change. The
Timer's callback already runs on its own background thread, not the Qt
main thread, which is what sync_now()'s network calls require.

Import-cycle note: sync_client.py imports all four store modules, and
those stores import this module -- so this module must NOT import
sync_client at module level (that would cycle back). It's imported
lazily inside _fire() instead, the only place this module actually needs
it.
"""
import threading

import auth_manager
from calendar_log import logger

DEBOUNCE_SECONDS = 3

_lock = threading.Lock()
_timer = None
_enabled = True


def enable():
    """Re-armed on login (Phase 4 Part B) so push-on-change resumes."""
    global _enabled
    _enabled = True


def disable():
    """Called on logout (Phase 4 Part B) -- cancels any pending debounced
    sync so one doesn't fire moments after the user signs out, and stops
    further note_change() calls from scheduling new ones until enable()
    is called again."""
    global _enabled, _timer
    with _lock:
        _enabled = False
        if _timer is not None:
            _timer.cancel()
            _timer = None


def note_change():
    """Call after a meaningful local write completes. No-op if
    push-on-change is disabled or nobody's logged in -- no point
    debouncing a sync that can't happen."""
    if not _enabled or not auth_manager.is_logged_in():
        return
    with _lock:
        global _timer
        if _timer is not None:
            _timer.cancel()
        _timer = threading.Timer(DEBOUNCE_SECONDS, _fire)
        _timer.daemon = True
        _timer.start()


def _fire():
    import sync_client  # lazy -- see module docstring

    try:
        result = sync_client.sync_now()
        if not result.success and not result.not_logged_in:
            logger.warning("sync_trigger: push-on-change sync failed: %s", result.error)
    except Exception:
        logger.exception("sync_trigger: push-on-change sync crashed")
