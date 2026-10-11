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
import re
import shutil
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


# True while STATE_PATH exists but could not be read (e.g. briefly locked by
# another process). While set, _save_locked() must not write: the in-memory
# tally is only today's new seconds, and saving it would replace the user's
# whole history on disk with that.
_load_failed = False


def _read_disk():
    """Parsed contents of STATE_PATH ({} if it doesn't exist). Raises OSError
    if the file can't be read; a corrupt/non-object file is set aside as
    STATE_PATH.corrupt-<timestamp> (never silently overwritten) and treated
    as empty."""
    if not os.path.exists(STATE_PATH):
        return {}
    last_exc = None
    for _ in range(3):
        try:
            with open(STATE_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            break
        except json.JSONDecodeError:
            data = None
            break
        except OSError as exc:
            last_exc = exc
            time.sleep(0.05)
    else:
        raise last_exc
    if isinstance(data, dict):
        return data
    try:
        shutil.copy2(STATE_PATH, f"{STATE_PATH}.corrupt-{datetime.now():%Y%m%d-%H%M%S-%f}")
    except OSError:
        pass
    return {}


def _load():
    global _data, _load_failed
    try:
        _data = _read_disk()
        _load_failed = False
    except OSError:
        _data = {}
        _load_failed = True


_load()


def _merge_into(disk, pending):
    for day, bucket in pending.items():
        target = disk.setdefault(day, {"apps": {}, "domains": {}})
        for kind in ("apps", "domains"):
            dest = target.setdefault(kind, {})
            for name, secs in bucket.get(kind, {}).items():
                dest[name] = dest.get(name, 0) + secs
    return disk


def _save_locked():
    global _data, _load_failed
    if _load_failed:
        try:
            disk = _read_disk()
        except OSError:
            return  # still unreadable: keep counting in memory, never overwrite
        _data = _merge_into(disk, _data)
        _load_failed = False
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
    if not domain or seconds <= 0 or seconds != seconds or seconds == float("inf"):
        return
    day = _day_key(when)
    with _lock:
        bucket = _data.setdefault(day, {"apps": {}, "domains": {}})
        bucket["domains"][domain] = bucket["domains"].get(domain, 0) + seconds
        _maybe_flush_locked()


def get_day(date_str, device=None):
    """One day's seconds per app/domain. device: None/"this" is this computer,
    "all" adds every other computer, or the id of one other computer."""
    apps = {}
    domains = {}
    with _lock:
        for bucket in _buckets_for(date_str, device):
            for name, secs in bucket.get("apps", {}).items():
                apps[name] = apps.get(name, 0) + secs
            for name, secs in bucket.get("domains", {}).items():
                domains[name] = domains.get(name, 0) + secs
    return {"apps": apps, "domains": domains}


def get_range(start_date_str, end_date_str, device=None):
    """Sums apps/domains across every day from start_date_str to
    end_date_str inclusive (both "YYYY-MM-DD"); `device` as in get_day()."""
    start = datetime.strptime(start_date_str, "%Y-%m-%d").date()
    end = datetime.strptime(end_date_str, "%Y-%m-%d").date()
    apps = {}
    domains = {}
    with _lock:
        d = start
        while d <= end:
            for bucket in _buckets_for(d.isoformat(), device):
                for name, secs in bucket.get("apps", {}).items():
                    apps[name] = apps.get(name, 0) + secs
                for name, secs in bucket.get("domains", {}).items():
                    domains[name] = domains.get(name, 0) + secs
            d += timedelta(days=1)
    return {"apps": apps, "domains": domains}


# --- other computers' screen time (see sync_live.py) ---
#
# Each computer only ever writes its own days; what other computers sent is
# kept apart, per device, so the tab can show them one at a time or together.

REMOTE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "private", "screentime_remote.json")
_remote = None            # {deviceId: {"name": str, "days": {day: {"apps": {}, "domains": {}}}}}
_SAFE_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_SAFE_DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_MAX_NAMES_PER_DAY = 2000
_MAX_SECONDS = 10 * 24 * 3600


def _load_remote_locked():
    global _remote
    if _remote is None:
        try:
            with open(REMOTE_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            _remote = data if isinstance(data, dict) else {}
        except (OSError, json.JSONDecodeError):
            _remote = {}
    return _remote


def _clean_seconds(table):
    """{name: seconds} with only sane entries, or None if it is not a dict."""
    if not isinstance(table, dict):
        return None
    clean = {}
    for name, secs in table.items():
        if (isinstance(name, str) and 0 < len(name) <= 200 and isinstance(secs, (int, float))
                and not isinstance(secs, bool) and secs == secs and 0 < secs <= _MAX_SECONDS):
            clean[name] = secs
        if len(clean) >= _MAX_NAMES_PER_DAY:
            break
    return clean


def merge_remote_day(device_id, name, day, apps, domains):
    """Stores one day of another computer's screen time, replacing what we had
    for that computer and day. Returns False if the data is not usable."""
    clean_apps, clean_domains = _clean_seconds(apps), _clean_seconds(domains)
    if (not isinstance(device_id, str) or not _SAFE_ID.match(device_id) or not isinstance(day, str)
            or not _SAFE_DAY.match(day) or clean_apps is None or clean_domains is None):
        return False
    label = name.strip()[:64] if isinstance(name, str) and name.strip() else device_id[:8]
    with _lock:
        remote = _load_remote_locked()
        entry = remote.setdefault(device_id, {"name": label, "days": {}})
        entry["name"] = label
        entry["days"][day] = {"apps": clean_apps, "domains": clean_domains}
        os.makedirs(os.path.dirname(REMOTE_PATH), exist_ok=True)
        tmp_path = REMOTE_PATH + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(remote, f)
        os.replace(tmp_path, REMOTE_PATH)
    return True


def remote_devices():
    """[{"id", "name"}] for every other computer that has sent screen time,
    named after the computer (a short id is added when two share a name)."""
    with _lock:
        devices = [{"id": did, "name": entry.get("name") or did[:8]} for did, entry in _load_remote_locked().items()]
    names = [d["name"] for d in devices]
    for d in devices:
        if names.count(d["name"]) > 1:
            d["name"] = f"{d['name']} ({d['id'][:4]})"
    return sorted(devices, key=lambda d: d["name"].lower())


def export_days():
    """A copy of this computer's own days: {day: {"apps", "domains"}}."""
    with _lock:
        return {day: {"apps": dict(b.get("apps", {})), "domains": dict(b.get("domains", {}))}
                for day, b in _data.items() if isinstance(b, dict)}


def _buckets_for(day, device):
    """The buckets of `day` that `device` ("this", None, "all" or a device id) covers."""
    buckets = []
    if device in (None, "this", "all"):
        buckets.append(_data.get(day))
    if device == "all":
        buckets += [entry.get("days", {}).get(day) for entry in _load_remote_locked().values()]
    elif device not in (None, "this"):
        buckets.append(_load_remote_locked().get(device, {}).get("days", {}).get(day))
    return [b for b in buckets if b]
