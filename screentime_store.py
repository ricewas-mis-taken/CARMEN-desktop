"""Always-on per-day screen time tracking -- how long each app/domain has
had the user's attention, independent of session_manager's focus sessions
entirely (no isActive/isPaused/isBreak gating here; this is a background
tally, not enforcement). Persisted to private/screentime.json, same
tmp-file-then-replace atomic save pattern as session_manager.py.

App time comes from window_tracker's polling loop (whichever process is in
the foreground each tick). Domain time comes from the browser extension's
own local tracking, reported in via POST /screentime/domain -- the desktop
process has no visibility into which domain a browser tab is on by itself.
"""
import json
import os
import threading
import time
from datetime import datetime, timedelta

STATE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "private", "screentime.json")

_lock = threading.Lock()
_data = {}

# Disk writes are debounced rather than done on every add_*_seconds() call --
# window_tracker's polling loop calls add_app_seconds() every ~1.5s while
# anything is running, and a plain in-place write on every tick would be a
# lot of needless disk I/O for data that only needs to survive a crash to
# within a few seconds' accuracy.
_FLUSH_INTERVAL_SECONDS = 10.0
_last_flush = 0.0


def _load():
    global _data
    if not os.path.exists(STATE_PATH):
        return
    try:
        with open(STATE_PATH, "r", encoding="utf-8") as f:
            _data = json.load(f)
    except (json.JSONDecodeError, OSError):
        _data = {}


_load()


def _save_locked():
    os.makedirs(os.path.dirname(STATE_PATH), exist_ok=True)
    tmp_path = STATE_PATH + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(_data, f, indent=2)
    os.replace(tmp_path, STATE_PATH)


def _maybe_flush_locked():
    global _last_flush
    now = time.monotonic()
    if now - _last_flush >= _FLUSH_INTERVAL_SECONDS:
        _last_flush = now
        _save_locked()


def flush():
    """Forces an immediate save regardless of the debounce interval -- call
    on clean shutdown so the last few seconds since the last periodic flush
    aren't lost."""
    with _lock:
        _save_locked()


def _day_key(when=None):
    return (when or datetime.now()).strftime("%Y-%m-%d")


def add_app_seconds(process_name, seconds, when=None):
    if not process_name or seconds <= 0:
        return
    day = _day_key(when)
    with _lock:
        bucket = _data.setdefault(day, {"apps": {}, "domains": {}})
        bucket["apps"][process_name] = bucket["apps"].get(process_name, 0) + seconds
        _maybe_flush_locked()


def add_domain_seconds(domain, seconds, when=None):
    if not domain or seconds <= 0:
        return
    day = _day_key(when)
    with _lock:
        bucket = _data.setdefault(day, {"apps": {}, "domains": {}})
        bucket["domains"][domain] = bucket["domains"].get(domain, 0) + seconds
        _maybe_flush_locked()


def get_day(date_str):
    with _lock:
        bucket = _data.get(date_str, {})
        return {"apps": dict(bucket.get("apps", {})), "domains": dict(bucket.get("domains", {}))}


def get_range(start_date_str, end_date_str):
    """Sums apps/domains across every day from start_date_str to
    end_date_str inclusive (both "YYYY-MM-DD")."""
    start = datetime.strptime(start_date_str, "%Y-%m-%d").date()
    end = datetime.strptime(end_date_str, "%Y-%m-%d").date()
    apps = {}
    domains = {}
    with _lock:
        d = start
        while d <= end:
            bucket = _data.get(d.isoformat())
            if bucket:
                for name, secs in bucket.get("apps", {}).items():
                    apps[name] = apps.get(name, 0) + secs
                for name, secs in bucket.get("domains", {}).items():
                    domains[name] = domains.get(name, 0) + secs
            d += timedelta(days=1)
    return {"apps": apps, "domains": domains}
