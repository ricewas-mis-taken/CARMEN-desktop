"""Honest pauses: what each pause in a session was for, and how much of it counted against the budget.

A pause is recorded in the session's log as a marker with a reason. The reasons:
  break        a real rest                                      free
  later        "I'll say what it was when I resume"             free while it is open; asked on resume, and
                                                                counts like checking if it is never answered
  long_break   a rest you set a length for (pauseMinutes)       free for that long, then counts as checking
  interrupted  something outside your control                   free
  stuck        you are blocked on the work                      free (it leads into the unblock flow)
  rest         the automatic pause of a Pomodoro break          free
  aside        Carmen parked this session for another one       free
  checking     looking at something else                        counts against the budget
  (none)       an old extension or a tray pause with no reason  counts against the budget, so leaving
                                                                the reason out is never the loophole
The budget is 10% of the session's length, at least 2 minutes. Past it, every 3 minutes costs a point
(at most 15 a session)."""
import math
from datetime import datetime

FREE = {"break", "long_break", "interrupted", "stuck", "rest", "aside"}
REASONS = ("break", "long_break", "interrupted", "stuck", "checking", "later")
LATER_AUTO_SECONDS = 120            # a "later" pause shorter than this is just called a break, nobody is asked      # what the picker offers
BUDGET_SHARE = 0.10
BUDGET_MINIMUM_SECONDS = 120
POINT_EVERY_SECONDS = 180
MAX_COST = 15
LONG_BREAK_MAX_MINUTES = 480
LONG_BREAK_GRACE_SECONDS = 60       # a minute of slack past the time set before it counts


def reason_of(marker):
    return marker.get("pauseReason") or "unspecified"


def _pieces(reason, seconds, minutes):
    """One pause as budget items. A long break is free for the time it was set for (plus a minute's grace);
    whatever runs past that is "overrun", which counts against the budget like checking something else."""
    if reason == "long_break" and minutes:
        free = int(minutes) * 60 + LONG_BREAK_GRACE_SECONDS
        if seconds > free:
            return [{"reason": reason, "seconds": free}, {"reason": "overrun", "seconds": seconds - free}]
    return [{"reason": reason, "seconds": seconds}]


def pauses(log, start_iso, end_iso, pending_free=False):
    """[{reason, seconds}] from a session's violation log. A pause still open at the end runs to the end."""
    out, open_at, open_reason, open_minutes = [], None, None, None
    for marker in log or []:
        kind = marker.get("kind")
        if kind == "pause" and open_at is None:
            open_at, open_reason = _parse(marker.get("timestamp")), reason_of(marker)
            open_minutes = marker.get("pauseMinutes")
        elif kind == "resume" and open_at is not None:
            at = _parse(marker.get("timestamp"))
            if at and open_at:
                out += _pieces(open_reason, max(0, int((at - open_at).total_seconds())), open_minutes)
            open_at = None
    end = _parse(end_iso)
    if open_at is not None and end and not (pending_free and open_reason == "later"):
        out += _pieces(open_reason, max(0, int((end - open_at).total_seconds())), open_minutes)
    return out


def summarize(entry, pending_free=False):
    """What the history shows for one session: free pause seconds, escape pause seconds, the budget, and
    the points the escape time cost."""
    items = pauses(entry.get("violationLog"), entry.get("startTime"), entry.get("endTime"), pending_free)
    start, end = _parse(entry.get("startTime")), _parse(entry.get("endTime"))
    length = max(0, int((end - start).total_seconds())) if start and end else 0
    free = sum(p["seconds"] for p in items if p["reason"] in FREE)
    escape = sum(p["seconds"] for p in items if p["reason"] not in FREE)
    budget = max(BUDGET_MINIMUM_SECONDS, int(length * BUDGET_SHARE))
    over = max(0, escape - budget)
    cost = min(MAX_COST, math.ceil(over / POINT_EVERY_SECONDS)) if over else 0
    return {"free_seconds": free, "escape_seconds": escape, "budget_seconds": budget,
            "over_seconds": over, "cost": cost, "pauses": items}


def live(status, now=None):
    """The budget of the session running now: {budget_seconds, used_seconds, left_seconds, over_seconds}, or
    None with no session. The length is the time already gone plus what is left, so the budget is the one
    the finished session will be judged by."""
    start = _parse((status or {}).get("startTime"))
    if not status or not status.get("isActive") or not start:
        return None
    now = now or datetime.now()
    length = max(0, int((now - start).total_seconds())) + int(status.get("secondsRemaining") or 0)
    result = summarize({"violationLog": status.get("violationLog"), "startTime": start.isoformat(),
                        "endTime": now.isoformat()}, pending_free=True)
    budget = max(BUDGET_MINIMUM_SECONDS, int(length * BUDGET_SHARE))
    used = result["escape_seconds"]
    return {"budget_seconds": budget, "used_seconds": used,
            "left_seconds": max(0, budget - used), "over_seconds": max(0, used - budget)}


def _parse(iso):
    try:
        return datetime.fromisoformat(iso) if iso else None
    except ValueError:
        return None
