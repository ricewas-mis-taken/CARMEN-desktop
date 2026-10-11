"""This computer's clock compared with the cloud's.

Two computers whose clocks disagree by a couple of minutes must still agree
on "when does this session end" and "which change is newer". Every response
from the cloud carries its own time in the Date header; the gap between that
and this computer's clock is remembered, and the running session's timestamps
are written in the cloud's time and read back in each computer's own. If a
response has no usable Date header the gap simply stays what it was (0 until
the first good one).

next_stamp() is also strictly increasing on this computer, so two changes made
in the same instant never get the same "newer than" stamp.
"""
import threading
import time
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

_lock = threading.Lock()
_offset_seconds = 0.0          # cloud clock minus this computer's clock
_last_stamp = None             # datetime, the newest stamp handed out here
# The Date header only has whole seconds and is rounded down; the middle of the
# second is the best guess for when the cloud actually answered.
_HEADER_RESOLUTION_GUESS = 0.5
# A gap this big means a broken header, not a wrong clock: ignore it.
_MAX_PLAUSIBLE_OFFSET = 7 * 24 * 3600
_ONE_MICROSECOND = timedelta(microseconds=1)


def offset_seconds():
    return _offset_seconds


def set_offset(seconds):
    global _offset_seconds
    _offset_seconds = float(seconds)


def note_response(response):
    """Learns the gap from an httpx response. Never raises."""
    try:
        header = response.headers.get("date")
        if not header:
            return
        server_time = parsedate_to_datetime(header).astimezone(timezone.utc).timestamp()
        gap = server_time + _HEADER_RESOLUTION_GUESS - time.time()
        if abs(gap) <= _MAX_PLAUSIBLE_OFFSET:
            set_offset(gap)
    except Exception:
        pass


def now_utc():
    """The cloud's idea of 'now', as an aware UTC datetime."""
    return datetime.fromtimestamp(time.time() + _offset_seconds, timezone.utc)


def next_stamp(at_least=None):
    """A new 'last changed' stamp (aware UTC ISO text): the cloud's now, but
    always later than every stamp handed out before and than `at_least`."""
    global _last_stamp
    at_least = parse(at_least) if isinstance(at_least, str) else at_least
    with _lock:
        stamp = now_utc()
        for floor in (_last_stamp, at_least):
            if floor is not None and stamp <= floor:
                stamp = floor + _ONE_MICROSECOND
        _last_stamp = stamp
        return stamp.isoformat()


def parse(stamp):
    """Aware UTC datetime from stamp text, or None if it is not one."""
    if not isinstance(stamp, str):
        return None
    try:
        parsed = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def is_newer(candidate, than):
    """True if stamp text `candidate` is later than `than` (None = never)."""
    candidate_dt = parse(candidate)
    if candidate_dt is None:
        return False
    than_dt = parse(than)
    return than_dt is None or candidate_dt > than_dt
