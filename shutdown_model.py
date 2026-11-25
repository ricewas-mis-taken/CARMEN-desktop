"""Learns when you usually stop for the night, so Carmen can ask about tomorrow a little before.

What it sees: a once-a-minute "someone was at the computer" mark, kept only for the evening and night window
(18:00 to 06:00). The last mark of a night is that night's stop time. The guess is a recency-weighted median of the
recent nights (separately for Friday/Saturday nights, which run late), after throwing away nights that are far from
the rest (an all-nighter, a dinner out). A night is measured in minutes after 18:00, so 23:50 and 00:20 sit 30
minutes apart instead of a day apart.

Local only: how late you work is not shared between computers."""
import json
import os
import threading
from datetime import datetime, timedelta

import paths

PATH = paths.private_path("shutdown_log.json")
ANCHOR_HOUR = 18             # a night starts here; its stop time counts as minutes after this
WINDOW_END_HOUR = 6          # marks after this hour in the morning belong to the day, not the night
MAX_NIGHTS = 120
MIN_NIGHTS = 3               # fewer than this and the default is used
MIN_BUCKET_NIGHTS = 5        # a weekend-only guess needs this many weekend nights
HALF_LIFE_NIGHTS = 14.0
MAD_CUTOFF = 3.0
DEFAULT_STOP = (22, 30)      # until there are enough nights to go on
LEAD_MINUTES = (30, 15)      # the two nudges before the guessed stop

_lock = threading.Lock()


def _read():
    try:
        with open(PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {}
    nights = data.get("nights") if isinstance(data, dict) else None
    if not isinstance(nights, dict):
        return {}
    return {k: v for k, v in nights.items() if isinstance(k, str) and isinstance(v, (int, float))}


def _write(nights):
    os.makedirs(os.path.dirname(PATH), exist_ok=True)
    keep = dict(sorted(nights.items())[-MAX_NIGHTS:])
    tmp = PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"nights": keep}, f, separators=(",", ":"))
    os.replace(tmp, PATH)


def night_of(moment):
    """(anchor date, minutes after 18:00) when `moment` is in the night window, else None."""
    if moment.hour >= ANCHOR_HOUR:
        anchor = moment.date()
    elif moment.hour < WINDOW_END_HOUR:
        anchor = moment.date() - timedelta(days=1)
    else:
        return None
    start = datetime.combine(anchor, datetime.min.time()).replace(hour=ANCHOR_HOUR)
    return anchor, int((moment - start).total_seconds() // 60)


def note_active(moment=None):
    """Marks that someone is at the computer now. Cheap: it only writes when the night's last mark moves on."""
    moment = moment or datetime.now()
    night = night_of(moment)
    if night is None:
        return False
    anchor, minutes = night
    with _lock:
        nights = _read()
        key = anchor.isoformat()
        if nights.get(key, -1) >= minutes:
            return False
        nights[key] = minutes
        _write(nights)
    return True


def _median(values):
    ordered = sorted(values)
    mid = len(ordered) // 2
    return ordered[mid] if len(ordered) % 2 else (ordered[mid - 1] + ordered[mid]) / 2


def _weighted_median(pairs):
    """pairs: (value, weight). The value where half the weight lies below."""
    ordered = sorted(pairs)
    half = sum(w for _v, w in ordered) / 2
    running = 0.0
    for value, weight in ordered:
        running += weight
        if running >= half:
            return value
    return ordered[-1][0]


def _is_late_night(anchor):
    return anchor.weekday() in (4, 5)         # Friday and Saturday nights


def typical_stop(for_anchor, nights=None):
    """Minutes after 18:00 that the night starting on `for_anchor` usually ends, or None with too few nights."""
    nights = _read() if nights is None else nights
    today = for_anchor
    dated = []
    for key, minutes in nights.items():
        try:
            day = datetime.strptime(key, "%Y-%m-%d").date()
        except ValueError:
            continue
        if day < today:
            dated.append((day, float(minutes)))
    if len(dated) < MIN_NIGHTS:
        return None
    same = [(d, m) for d, m in dated if _is_late_night(d) == _is_late_night(today)]
    pool = same if len(same) >= MIN_BUCKET_NIGHTS else dated
    center = _median([m for _d, m in pool])
    spread = _median([abs(m - center) for _d, m in pool]) or 15.0
    kept = [(d, m) for d, m in pool if abs(m - center) <= MAD_CUTOFF * 1.4826 * spread]
    pairs = [(m, 0.5 ** (max(0, (today - d).days) / HALF_LIFE_NIGHTS)) for d, m in kept]
    return _weighted_median(pairs)


def expected_stop(now=None):
    """The datetime tonight's stop is expected at, and whether it was learned (False: the default)."""
    now = now or datetime.now()
    anchor = now.date() if now.hour >= WINDOW_END_HOUR else now.date() - timedelta(days=1)
    minutes = typical_stop(anchor)
    start = datetime.combine(anchor, datetime.min.time()).replace(hour=ANCHOR_HOUR)
    if minutes is None:
        stop = datetime.combine(anchor, datetime.min.time()).replace(hour=DEFAULT_STOP[0], minute=DEFAULT_STOP[1])
        return stop, False
    return start + timedelta(minutes=minutes), True


def nudge_times(now=None):
    """The times tonight's two nudges are due: [(minutes before, datetime), ...], earliest first."""
    stop, _learned = expected_stop(now)
    return [(lead, stop - timedelta(minutes=lead)) for lead in LEAD_MINUTES]


def usual_stop_minutes(day):
    """When the night that starts on `day` usually ends, as minutes after that day's midnight (so 00:30 is 1470)."""
    minutes = typical_stop(day)
    if minutes is None:
        return DEFAULT_STOP[0] * 60 + DEFAULT_STOP[1]
    return ANCHOR_HOUR * 60 + int(minutes)
