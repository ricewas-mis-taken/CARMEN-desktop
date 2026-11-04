"""Carmen Score: one number from 0 to 1000 that is never reset.

It starts at 500 and every day it is settled it goes up or down. The score is the sum of a ledger of events,
so it is permanent: nothing restarts it at the end of a week or a month.

Every event has a key made from what it is about (goal:<date>, period:<task>:<start>, review:<id>, ...), and
an event whose key is already in the ledger is ignored. Settling twice, or catching up after the app was
closed for days, can therefore never count a day twice. Settling only looks at days after the ledger began.

What earns and costs points (all small: a good day is about +12, a bad one about -18):
  - the day's per-day goals: from +12 when all met, down to -18 when none of it was done
  - a week / month / custom goal: only at the END of its period, +20 if met, down to -30 if untouched
  - a review done on or before its due date: +6; later than that costs 3 more per day (to -15)
  - a review left overdue: -3 for each day, for at most 10 days per due date
  - things set aside (out of rotation) never cost anything
Pauses and unblocks add their own events through add_event()."""
import json
import os
import threading
from datetime import date, datetime, timedelta

import paths

PATH = paths.private_path("score.json")

BASE = 500
MIN_SCORE = 0
MAX_SCORE = 1000
MAX_CATCH_UP_DAYS = 60
OVERDUE_DAY_COST = 3
OVERDUE_MAX_DAYS = 10

_lock = threading.RLock()


def _empty():
    return {"version": 1, "events": [], "settledThrough": None}


def load():
    try:
        with open(PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict) and isinstance(data.get("events"), list):
            data.setdefault("settledThrough", None)
            return data
    except (OSError, ValueError):
        pass
    return _empty()


def _save(data):
    tmp = PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f)
    os.replace(tmp, PATH)


def clamp(value):
    return max(MIN_SCORE, min(MAX_SCORE, value))


def score(data=None):
    """The score now. The running total is clamped after every event, so a long bad stretch bottoms out at
    0 and the first good day after it climbs from 0, not from a hidden negative."""
    data = data or load()
    total = BASE
    for event in sorted(data["events"], key=lambda e: (e["date"], e["key"])):
        total = clamp(total + event["points"])
    return total


def score_through(day, data=None):
    """What the score was at the end of `day`."""
    data = data or load()
    cut = day.isoformat()
    total = BASE
    for event in sorted(data["events"], key=lambda e: (e["date"], e["key"])):
        if event["date"] <= cut:
            total = clamp(total + event["points"])
    return total


def grade(value):
    return "A" if value >= 800 else "B" if value >= 650 else "C" if value >= 500 else "D" if value >= 350 else "F"


def add_event(key, day, kind, points, note="", _data=None):
    """Adds one event unless its key is already there. Returns True when it was added."""
    points = int(points)
    own = _data is None
    with _lock:
        data = _data if _data is not None else load()
        if any(e["key"] == key for e in data["events"]):
            return False
        data["events"].append({"key": key, "date": day.isoformat() if hasattr(day, "isoformat") else str(day),
                               "kind": kind, "points": points, "note": note})
        if own:
            _save(data)
        return True


def events_between(first, last, data=None):
    data = data or load()
    a, b = first.isoformat(), last.isoformat()
    return [e for e in data["events"] if a <= e["date"] <= b]


def record_review(session_key, due_iso, done_day, name=""):
    """Scores one finished review against the day it was due."""
    try:
        due = date.fromisoformat(str(due_iso)[:10])
    except ValueError:
        return False
    late = max(0, (done_day - due).days)
    points = 6 - min(late * 3, 21) if late else 6
    note = f"{name}: on time" if not late else f"{name}: {late} day{'s' if late != 1 else ''} late"
    return add_event(f"review:{session_key}", done_day, "review", points, note.strip(": "))


def settle(today=None, tasks=None, sessions=None, overdue=None):
    """Settles every day after the last settled one, up to yesterday. `overdue(day)` returns the
    [(problem id, due date)] that were overdue on that day and not set aside. Returns the points added."""
    import tasks_store
    today = today or date.today()
    yesterday = today - timedelta(days=1)
    with _lock:
        data = load()
        began = data["settledThrough"]
        if began is None:
            data["settledThrough"] = yesterday.isoformat()      # nothing is judged before the score began
            _save(data)
            return 0
        start = date.fromisoformat(began) + timedelta(days=1)
        start = max(start, yesterday - timedelta(days=MAX_CATCH_UP_DAYS - 1))
        if start > yesterday:
            return 0
    tasks = tasks if tasks is not None else tasks_store.load_tasks()
    if sessions is None:
        import session_history
        sessions = session_history.load_all()
    added = 0
    with _lock:
        data = load()
        day = start
        while day <= yesterday:
            added += _settle_day(data, day, tasks, sessions, overdue, tasks_store)
            day += timedelta(days=1)
        data["settledThrough"] = yesterday.isoformat()
        _save(data)
    return added


def _settle_day(data, day, tasks, sessions, overdue, tasks_store):
    before = len(data["events"])
    required = logged = 0.0
    for task in tasks:
        if tasks_store.is_period_task(task):
            _, end = tasks_store.period_bounds(task, day)
            if end == day:
                _settle_period(data, task, day, sessions, tasks_store)
            continue
        snap = tasks_store.goal_snapshot(task, day, sessions)
        if snap["required"] > 0:
            required += snap["required"]
            logged += min(snap["logged"], snap["required"])
    if required > 0:
        ratio = logged / required
        add_event(f"goal:{day.isoformat()}", day, "goals", round((ratio - 0.6) * 30),
                  f"{round(ratio * 100)}% of the day's goals", _data=data)
    if overdue is not None:
        for problem_id, due in overdue(day):
            days = (day - due).days
            if 1 <= days <= OVERDUE_MAX_DAYS:
                add_event(f"overdue:{problem_id}:{due.isoformat()}:{day.isoformat()}", day, "overdue",
                          -OVERDUE_DAY_COST, f"review {days} day{'s' if days != 1 else ''} overdue", _data=data)
    return len(data["events"]) - before


def _settle_period(data, task, day, sessions, tasks_store):
    start, _ = tasks_store.period_bounds(task, day)
    required = tasks_store.required_minutes_for_period(task, day)
    if required <= 0:
        return
    logged = tasks_store.logged_seconds_for_period(task, day, sessions) / 60
    ratio = min(1.0, logged / required)
    points = 20 if ratio >= 1.0 else -round(30 * (1 - ratio))
    add_event(f"period:{task.get('id')}:{start.isoformat()}", day, "period", points,
              f"{task.get('name', 'Goal')}: {round(ratio * 100)}% of its {tasks_store.period_phrase(task)} goal",
              _data=data)


def settle_now():
    """Settles up to yesterday with the live data. Safe to call often: it does nothing when up to date."""
    import review_store
    return settle(overdue=review_store.overdue_on)


def start(stop_event, every_seconds=3600):
    """Settles at start-up and again every hour, so a day that ends while Carmen is open is settled
    without a restart."""
    def run():
        while not stop_event.is_set():
            try:
                settle_now()
            except Exception:
                import logging
                logging.getLogger(__name__).exception("score settle failed")
            stop_event.wait(every_seconds)
    thread = threading.Thread(target=run, daemon=True, name="score-settle")
    thread.start()
    return thread
