"""Recurrence handling built on dateutil.rrule — RFC5545 RRULE strings, same
format iCal/Google Calendar use, so this stays import/export-compatible if
that's ever added later. calendar_store.py stores the bare RRULE value part
(no "RRULE:" prefix, no DTSTART) on the event row; DTSTART always comes from
the event's own start time instead, since every occurrence must anchor to it.

Two halves:
- build_rrule(...) turns the UI's friendly recurrence choice (none / daily /
  weekly / weekly-on-specific-days / monthly / yearly / custom N weeks or
  days) into a storable RRULE string.
- expand_occurrences(...) turns a stored event back into concrete
  (start, end) datetime occurrences within a range, for the month grid, day
  view, and the scheduler's lookahead.
"""
from datetime import datetime

from dateutil.rrule import rrulestr

WEEKDAY_CODES = ["MO", "TU", "WE", "TH", "FR", "SA", "SU"]


def build_rrule(kind, interval=1, weekdays=None):
    """kind is one of: "none", "daily", "weekly", "weekly_days", "monthly",
    "yearly". weekdays, only used for "weekly_days", is a list of
    WEEKDAY_CODES entries (e.g. ["MO", "WE", "FR"]). interval implements
    "every N days/weeks" for the daily/weekly kinds. Returns None for
    "none", otherwise an RRULE value string with no "RRULE:" prefix."""
    interval = max(1, int(interval or 1))

    if kind == "none":
        return None
    if kind == "daily":
        return f"FREQ=DAILY;INTERVAL={interval}"
    if kind == "weekly":
        return f"FREQ=WEEKLY;INTERVAL={interval}"
    if kind == "weekly_days":
        days = ",".join(weekdays) if weekdays else "MO"
        return f"FREQ=WEEKLY;INTERVAL={interval};BYDAY={days}"
    if kind == "monthly":
        return f"FREQ=MONTHLY;INTERVAL={interval}"
    if kind == "yearly":
        return f"FREQ=YEARLY;INTERVAL={interval}"
    raise ValueError(f"unknown recurrence kind: {kind}")


def describe_rrule(rrule_str):
    """Short human-readable label for the event editor/list, e.g. 'Weekly on
    Mon, Wed, Fri' — best-effort, falls back to the raw string on anything
    unexpected rather than raising."""
    if not rrule_str:
        return "Does not repeat"
    try:
        parts = dict(p.split("=") for p in rrule_str.split(";"))
        freq = parts.get("FREQ", "").title()
        interval = int(parts.get("INTERVAL", 1))
        prefix = f"Every {interval} " if interval > 1 else ""
        if "BYDAY" in parts:
            days = parts["BYDAY"].split(",")
            return f"{prefix}{freq.lower() if prefix else freq} on {', '.join(days)}"
        unit = freq.lower() + ("s" if interval > 1 else "")
        return f"{prefix}{unit}" if prefix else freq
    except Exception:
        return rrule_str


def expand_occurrences(event, range_start, range_end):
    """Returns [(occurrence_start, occurrence_end), ...] datetimes for this
    event that overlap [range_start, range_end]. Non-recurring events yield
    at most one occurrence — their own start/end, if they overlap the range.
    """
    start_dt = datetime.fromisoformat(event["start"])
    end_dt = datetime.fromisoformat(event["end"])
    duration = end_dt - start_dt

    if not event.get("rrule"):
        if start_dt < range_end and end_dt > range_start:
            return [(start_dt, end_dt)]
        return []

    try:
        rule = rrulestr(f"RRULE:{event['rrule']}", dtstart=start_dt)
    except Exception:
        return []

    # rrule.between's start bound must account for events whose *duration*
    # crosses into range_start (e.g. an overnight event) — pull occurrences
    # starting up to one duration early, since between() only matches on
    # occurrence *start* time.
    lookback_start = range_start - duration
    occurrences = rule.between(lookback_start, range_end, inc=True)
    return [(occ, occ + duration) for occ in occurrences]


def next_occurrences(events, from_dt, count=2, lookahead_days=90):
    """Flattened, time-sorted occurrences across all events starting at or
    after from_dt, for the "next up" widget. lookahead_days bounds how far
    forward recurring events get expanded — plenty for "what's next", and
    keeps this cheap even with far-future yearly rules."""
    from datetime import timedelta

    range_end = from_dt + timedelta(days=lookahead_days)
    upcoming = []
    for event in events:
        for occ_start, occ_end in expand_occurrences(event, from_dt, range_end):
            if occ_start >= from_dt:
                upcoming.append((occ_start, occ_end, event))
    upcoming.sort(key=lambda item: item[0])
    return upcoming[:count]
