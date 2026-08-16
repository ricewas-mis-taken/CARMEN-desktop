"""Background thread that calls sync_client.sync_now() once at startup (if
already logged in) and then every sync_client.SYNC_INTERVAL_SECONDS, same
poll-loop pattern as calendar_scheduler.py (stop_event.wait(), not
time.sleep(), so shutdown stays responsive; every tick wrapped in
try/except so a sync failure can never take the background thread down).

sync_now() already never raises and already checks is_logged_in() itself,
so a logged-out run is just a cheap early-return, not something this
scheduler needs to guard against separately.
"""
import threading

import sync_client
from calendar_log import logger

_stop_event = None


def start(stop_event):
    """Starts the sync loop in its own daemon thread. Call once from
    main.py, same pattern as calendar_scheduler.start(stop_event)."""
    global _stop_event
    _stop_event = stop_event
    thread = threading.Thread(target=_run, args=(stop_event,), daemon=True)
    thread.start()
    return thread


def _run(stop_event):
    while not stop_event.is_set():
        try:
            _tick()
        except Exception:
            logger.exception("sync scheduler tick failed")
        stop_event.wait(sync_client.SYNC_INTERVAL_SECONDS)


def _tick():
    result = sync_client.sync_now()
    if not result.success and not result.not_logged_in:
        logger.warning("sync_scheduler: sync_now() failed: %s", result.error)
