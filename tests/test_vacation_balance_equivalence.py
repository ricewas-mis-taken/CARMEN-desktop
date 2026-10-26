"""vacation_balance_minutes() now sums the history in one pass; it must give exactly
what the day-by-day version gave, and stay fast with a long history."""
import random
import time
from datetime import date, datetime, timedelta

import tasks_store


def _reference_balance(task, sessions, today):
    """The previous implementation: one logged_seconds_for_date() call per day."""
    cashed = task.get("cashedInDates") or {}
    earned, seen = 0.0, set()
    for entry in tasks_store._task_history_entries(task["id"], sessions):
        day = datetime.fromisoformat(entry["startTime"]).date()
        if day >= today or day in seen or not tasks_store.is_scheduled_on(task, day):
            continue
        seen.add(day)
        logged = tasks_store.logged_seconds_for_date(task, day, sessions) / 60
        earned += max(0, logged - tasks_store.required_minutes_for_date(task, day))
    return max(0.0, earned - sum(cashed.values()))


def _sessions(task_id, count, seed, days_back=300):
    rng = random.Random(seed)
    today = date.today()
    out = []
    for _ in range(count):
        start = datetime.combine(today - timedelta(days=rng.randint(1, days_back)), datetime.min.time())
        start += timedelta(minutes=rng.randint(0, 24 * 60 - 1))
        end = start + timedelta(minutes=rng.randint(5, 600))   # some cross midnight
        log = []
        if rng.random() < 0.5:
            pause = start + timedelta(minutes=rng.randint(1, 4))
            log = [{"kind": "pause", "timestamp": pause.isoformat()},
                   {"kind": "resume", "timestamp": (pause + timedelta(minutes=rng.randint(1, 30))).isoformat()}]
        out.append({"source": rng.choice(["task", "review"]), "eventId": task_id,
                    "startTime": start.isoformat(), "endTime": end.isoformat(), "violationLog": log})
    return out


def _task(**extra):
    task = {"id": "t1", "targetMinutes": 45, "recurrence": "weekly_days",
            "weekdays": ["MO", "TU", "WE", "TH", "FR"], "cashedInDates": {}}
    task.update(extra)
    return task


def test_matches_the_day_by_day_version():
    for seed in range(6):
        task = _task()
        sessions = _sessions("t1", 120, seed)
        today = date.today()
        assert abs(tasks_store.vacation_balance_minutes(task, sessions, today)
                   - _reference_balance(task, sessions, today)) < 1e-6


def test_respects_cashed_in_minutes_and_daily_recurrence():
    task = _task(recurrence="daily", cashedInDates={(date.today() - timedelta(days=3)).isoformat(): 20})
    sessions = _sessions("t1", 80, 99)
    today = date.today()
    assert abs(tasks_store.vacation_balance_minutes(task, sessions, today)
               - _reference_balance(task, sessions, today)) < 1e-6


def test_long_history_stays_fast():
    task = _task(recurrence="daily")
    sessions = _sessions("t1", 3000, 7, days_back=700)
    started = time.perf_counter()
    tasks_store.vacation_balance_minutes(task, sessions)
    assert time.perf_counter() - started < 0.5
