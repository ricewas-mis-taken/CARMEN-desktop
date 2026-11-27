"""Grouping finished sessions under a bigger one: the reviews and timers that happened during a Pomodoro.

A review timed beside a session of its task is grouped under it by time. One that began before the Pomodoro, or
that ran on its own, can be linked by hand ("groupedUnder" holds the Pomodoro's id). Grouping only changes how
the history shows things: no time moves, so nothing is counted twice or lost."""
from datetime import datetime, timedelta

import session_history


def _parse(iso):
    try:
        return datetime.fromisoformat(iso) if iso else None
    except ValueError:
        return None


def groupable(entry):
    """A review, first attempt or plain focus session can be grouped; a task session is the group."""
    return bool(entry) and entry.get("source") not in ("task", "calendar-event")


def _overlaps(entry, parent):
    start, end = _parse(entry.get("startTime")), _parse(entry.get("endTime"))
    p_start, p_end = _parse(parent.get("startTime")), _parse(parent.get("endTime"))
    if not (start and end and p_start and p_end):
        return False
    return start < p_end and end > p_start


def parent_of(entry, sessions):
    """The session this one is grouped under, or None."""
    if not groupable(entry):
        return None
    wanted = entry.get("groupedUnder")
    if wanted:
        return next((s for s in sessions if session_history.entry_id(s) == wanted), None)
    if not entry.get("beside"):
        return None
    for parent in sessions:
        if (parent.get("source") == "task" and parent.get("eventId") and parent.get("eventId") == entry.get("eventId")
                and _overlaps(entry, parent)):
            return parent
    return None


def members_of(parent, sessions):
    """Everything grouped under this session, oldest first."""
    if parent.get("source") != "task":
        return []
    mine = session_history.entry_id(parent)
    return [s for s in sessions
            if session_history.entry_id(s) != mine and groupable(s)
            and (p := parent_of(s, sessions)) is not None and session_history.entry_id(p) == mine]


def candidates(entry, sessions, within_hours=24):
    """Task sessions (Pomodoros included) close in time to this one, nearest first."""
    start = _parse(entry.get("startTime"))
    if start is None:
        return []
    found = []
    for s in sessions:
        if s.get("source") != "task" or session_history.entry_id(s) == session_history.entry_id(entry):
            continue
        s_start = _parse(s.get("startTime"))
        if s_start is not None and abs(s_start - start) <= timedelta(hours=within_hours):
            found.append((abs(s_start - start), s))
    return [s for _gap, s in sorted(found, key=lambda pair: pair[0])]


def link(entry, parent):
    """Groups `entry` under `parent`. False if either cannot be grouped."""
    if not groupable(entry) or parent.get("source") != "task":
        return False
    return session_history.update_entry(session_history.entry_id(entry), {"groupedUnder": session_history.entry_id(parent)})


def unlink(entry):
    return session_history.update_entry(session_history.entry_id(entry), {"groupedUnder": None})
