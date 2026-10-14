"""Records what every thread was doing when the window thread stops responding.

Windows closes a program whose window thread is stuck for a few seconds ("stopped
interacting with Windows"), leaving no trace of why. A timer on the window thread
beats once a second; if the beat goes stale, the stacks of all threads are written
to private/hang_trace.txt (once per hang), so the cause can be read afterwards.
"""
import faulthandler
import os
import threading
import time

from PySide6.QtCore import QTimer

from calendar_log import logger

TRACE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "private", "hang_trace.txt")
BEAT_MS = 1000
STALE_SECONDS = 6
CHECK_SECONDS = 2

_last_beat = time.monotonic()
_timer = None


def _beat():
    global _last_beat
    _last_beat = time.monotonic()


def write_trace(path=None):
    with open(path or TRACE_PATH, "a", encoding="utf-8") as f:
        f.write(f"\n=== window thread stuck for over {STALE_SECONDS}s at {time.strftime('%Y-%m-%d %H:%M:%S')} ===\n")
        f.flush()
        faulthandler.dump_traceback(file=f, all_threads=True)


def _watch():
    reported = False
    while True:
        time.sleep(CHECK_SECONDS)
        stale = time.monotonic() - _last_beat > STALE_SECONDS
        if stale and not reported:
            reported = True
            try:
                write_trace()
                logger.error("ui_watchdog: window thread stuck, stacks in %s", TRACE_PATH)
            except Exception:
                logger.exception("ui_watchdog: could not write the trace")
        elif not stale:
            reported = False


def start():
    """Call on the Qt main thread after QApplication() exists."""
    global _timer
    _beat()
    _timer = QTimer()
    _timer.setInterval(BEAT_MS)
    _timer.timeout.connect(_beat)
    _timer.start()
    threading.Thread(target=_watch, daemon=True, name="ui-watchdog").start()
