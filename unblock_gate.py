"""Unblocking mid-session, with a daily budget.

Every unblock already has to give a reason. A detailed one also says what you will do with it and for how
long (plan, minutes). The first DAILY_FREE unblocks of the day are free. After that each one makes you wait
WAIT_SECONDS: the first request starts the wait, and asking again once it is over goes through. The wait
is enforced here, in the desktop app, so a client (the browser extension) can't skip it.

Points: a detailed unblock inside the budget earns UNBLOCK_BONUS; one past the budget costs OVER_BUDGET_COST.
A client that sends no plan is never penalised for it (an old extension and the lock overlay can't ask)."""
import threading
import time
from datetime import date, datetime

import score_store
import session_history
import session_manager

DAILY_FREE = 3
WAIT_SECONDS = 120
UNBLOCK_BONUS = 1
OVER_BUDGET_COST = 3

_lock = threading.Lock()
_waiting = {}      # key -> monotonic time the wait started


def used_today(today=None):
    """How many unblocks have been made today: in the running session and in every session that ended today."""
    today = (today or date.today()).isoformat()
    count = 0
    for entry in session_history.load_all():
        count += _count(entry.get("processBlocklistExceptions"), today)
        count += _count(entry.get("domainWhitelistAdditions"), today)
    with session_manager._lock:
        count += _count(session_manager._state.get("processBlocklistExceptions"), today)
        count += _count(session_manager._state.get("domainWhitelistAdditions"), today)
    return count


def _count(items, today):
    return sum(1 for i in items or [] if str(i.get("timestamp", ""))[:10] == today)


def check(key, now=None):
    """(allowed, seconds still to wait). Over the daily budget, the first call starts the wait."""
    if used_today() < DAILY_FREE:
        return True, 0
    now = time.monotonic() if now is None else now
    with _lock:
        started = _waiting.get(key)
        if started is None:
            _waiting[key] = now
            return False, WAIT_SECONDS
        left = WAIT_SECONDS - (now - started)
        if left > 0:
            return False, int(left) + 1
        del _waiting[key]
        return True, 0


def is_detailed(plan, minutes):
    return isinstance(plan, str) and bool(plan.strip()) and isinstance(minutes, int) and not isinstance(minutes, bool) and minutes > 0


def record(entry, over_budget):
    """Scores an unblock that went through (`entry` is the exception / addition the session logged)."""
    stamp = entry.get("timestamp") or datetime.now().isoformat()
    day = datetime.fromisoformat(stamp).date()
    if over_budget:
        score_store.add_event(f"unblock:{stamp}", day, "unblocks", -OVER_BUDGET_COST,
                              "unblocked past the daily budget")
    elif is_detailed(entry.get("plan"), entry.get("minutes")):
        score_store.add_event(f"unblock:{stamp}", day, "unblocks", UNBLOCK_BONUS, "a detailed unblock")
