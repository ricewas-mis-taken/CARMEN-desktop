"""vacation_balance_minutes() now sums the history in one pass; it must give exactly
what the day-by-day version gave, and stay fast with a long history."""
import random
import time
from datetime import date, datetime, timedelta

import tasks_store
import vacation


def _reference_balance(task, sessions, today):
    """The rules applied the slow way: one logged_seconds_for_date() call per day, in date order (sessions here
    have no violations and no hard lock, so only the ladder, the streak and the weekly shape apply)."""
    cashed = task.get("cashedInDates") or {}
    entries = tasks_store._task_history_entries(task["id"], sessions)
    first = min(datetime.fromisoformat(e["startTime"]).date() for e in entries)
    earned, streak, week, banked = 0.0, 0, None, 0.0
    day = first
    while day < today:
        if tasks_store.is_scheduled_on(task, day):
            key = day.isocalendar()[:2]
            if key != week:
                week, banked = key, 0.0
            logged = tasks_store.logged_seconds_for_date(task, day, sessions) / 60
            required = tasks_store.required_minutes_for_date(task, day)
            target = tasks_store._target_minutes_for_date(task, day)
            got = 0.0
            if logged > 0:
                got = vacation.ladder(max(0, logged - required), target) * vacation.streak_factor(streak)
                got = vacation.week_shaped(banked, got, target)
            earned += got
            banked += got
            streak = streak + 1 if (target > 0 and logged >= required) else 0
        day += timedelta(days=1)
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


def _day(task_id, days_ago, minutes, **extra):
    start = datetime.combine(date.today() - timedelta(days=days_ago), datetime.min.time()) + timedelta(hours=9)
    return {"source": "task", "eventId": task_id, "startTime": start.isoformat(),
            "endTime": (start + timedelta(minutes=minutes)).isoformat(), "violationLog": [], **extra}


def test_the_ladder_steps_down_and_stops_at_twice_the_target():
    assert vacation.ladder(15, 60) == 15                          # first quarter: one for one
    assert abs(vacation.ladder(30, 60) - 22.5) < 1e-9             # next quarter at half
    assert abs(vacation.ladder(60, 60) - 32.5) < 1e-9             # next half at a third
    assert abs(vacation.ladder(120, 60) - 44.5) < 1e-9            # next whole target at a fifth
    assert vacation.ladder(500, 60) == vacation.ladder(120, 60)   # nothing beyond twice the target
    assert vacation.ladder(0, 60) == 0 and vacation.ladder(10, 0) == 0


def test_violations_halve_a_day_and_a_hard_lock_adds_a_quarter():
    task = _task(recurrence="daily", targetMinutes=60)
    clean = tasks_store.vacation_balance_minutes(task, [_day("t1", 1, 90)])
    noisy = tasks_store.vacation_balance_minutes(task, [_day("t1", 1, 90, violationCount=3)])
    hard = tasks_store.vacation_balance_minutes(task, [_day("t1", 1, 90, lockMode="hard")])
    assert abs(noisy - clean * 0.5) < 1e-9
    assert abs(hard - clean * 1.25) < 1e-9


def test_days_in_a_row_that_met_the_target_boost_the_next_one():
    task = _task(recurrence="daily", targetMinutes=60)
    alone = tasks_store.vacation_balance_minutes(task, [_day("t1", 1, 75)])
    run = [_day("t1", 4, 60), _day("t1", 3, 60), _day("t1", 2, 60), _day("t1", 1, 75)]
    assert abs(tasks_store.vacation_balance_minutes(task, run) - alone * 1.3) < 1e-9
    broken = [_day("t1", 4, 60), _day("t1", 3, 10), _day("t1", 2, 60), _day("t1", 1, 75)]
    assert abs(tasks_store.vacation_balance_minutes(task, broken) - alone * 1.1) < 1e-9


def test_a_week_that_has_banked_a_days_target_earns_half_after_that():
    assert vacation.week_shaped(0, 20, 60) == 20
    assert vacation.week_shaped(50, 20, 60) == 10 + 5
    assert vacation.week_shaped(60, 20, 60) == 10
