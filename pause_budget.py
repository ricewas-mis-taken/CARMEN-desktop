"""Honest pauses: what each pause in a session was for, and how much of it counted against the budget.

A pause is recorded in the session's log as a marker with a reason. The reasons:
  break        a real rest                                      free
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

FREE = {"break", "interrupted", "stuck", "rest", "aside"}
REASONS = ("break", "interrupted", "stuck", "checking")      # what the picker offers
BUDGET_SHARE = 0.10
BUDGET_MINIMUM_SECONDS = 120
POINT_EVERY_SECONDS = 180
MAX_COST = 15


def reason_of(marker):
    return marker.get("pauseReason") or "unspecified"


def pauses(log, start_iso, end_iso):
    """[{reason, seconds}] from a session's violation log. A pause still open at the end runs to the end."""
    out, open_at, open_reason = [], None, None
    for marker in log or []:
        kind = marker.get("kind")
        if kind == "pause" and open_at is None:
            open_at, open_reason = _parse(marker.get("timestamp")), reason_of(marker)
        elif kind == "resume" and open_at is not None:
            at = _parse(marker.get("timestamp"))
            if at and open_at:
                out.append({"reason": open_reason, "seconds": max(0, int((at - open_at).total_seconds()))})
            open_at = None
    end = _parse(end_iso)
    if open_at is not None and end:
        out.append({"reason": open_reason, "seconds": max(0, int((end - open_at).total_seconds()))})
    return out


def summarize(entry):
    """What the history shows for one session: free pause seconds, escape pause seconds, the budget, and
    the points the escape time cost."""
    items = pauses(entry.get("violationLog"), entry.get("startTime"), entry.get("endTime"))
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
                        "endTime": now.isoformat()})
    budget = max(BUDGET_MINIMUM_SECONDS, int(length * BUDGET_SHARE))
    used = result["escape_seconds"]
    return {"budget_seconds": budget, "used_seconds": used,
            "left_seconds": max(0, budget - used), "over_seconds": max(0, used - budget)}


def _parse(iso):
    try:
        return datetime.fromisoformat(iso) if iso else None
    except ValueError:
        return None
