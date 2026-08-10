"""Unit tests for tasks_store.py's pure scheduling/progress math -- the
error-prone part of the Tasks tab (pause-aware worked-time, vacation
balance, cash-in), kept independent of Qt so it's testable headlessly."""
from datetime import date, datetime, timedelta

import pytest

import tasks_store


@pytest.fixture
def isolate_tasks(tmp_path, monkeypatch):
    monkeypatch.setattr(tasks_store, "TASKS_PATH", str(tmp_path / "tasks.json"))
    yield


def _iso(d, h=9, m=0):
    return datetime(d.year, d.month, d.day, h, m).isoformat()


def test_worked_seconds_no_pauses():
    start = _iso(date(2026, 1, 1), 9, 0)
    end = _iso(date(2026, 1, 1), 9, 30)
    assert tasks_store.worked_seconds(start, end, []) == 1800


def test_worked_seconds_excludes_single_pause():
    day = date(2026, 1, 1)
    start = datetime(day.year, day.month, day.day, 9, 0)
    log = [
        {"kind": "pause", "timestamp": (start + timedelta(minutes=20)).isoformat()},
        {"kind": "resume", "timestamp": (start + timedelta(minutes=80)).isoformat()},
    ]
    end = start + timedelta(minutes=90)
    # worked 20m, paused 60m, worked 10m more -> 30m total, not the 90m span
    assert tasks_store.worked_seconds(start.isoformat(), end.isoformat(), log) == 30 * 60


def test_worked_seconds_multiple_pauses():
    start = datetime(2026, 1, 1, 9, 0)
    log = [
        {"kind": "pause", "timestamp": (start + timedelta(minutes=10)).isoformat()},
        {"kind": "resume", "timestamp": (start + timedelta(minutes=15)).isoformat()},
        {"kind": "pause", "timestamp": (start + timedelta(minutes=25)).isoformat()},
        {"kind": "resume", "timestamp": (start + timedelta(minutes=40)).isoformat()},
    ]
    end = start + timedelta(minutes=50)
    # worked: 0-10 (10m) + 15-25 (10m) + 40-50 (10m) = 30m
    assert tasks_store.worked_seconds(start.isoformat(), end.isoformat(), log) == 30 * 60


def test_worked_seconds_ended_while_paused():
    start = datetime(2026, 1, 1, 9, 0)
    log = [
        {"kind": "pause", "timestamp": (start + timedelta(minutes=10)).isoformat()},
    ]
    end = start + timedelta(minutes=30)
    # only the first 10m before the (unresolved) pause counts
    assert tasks_store.worked_seconds(start.isoformat(), end.isoformat(), log) == 10 * 60


def test_is_scheduled_on_daily():
    task = {"recurrence": "daily", "weekdays": []}
    assert tasks_store.is_scheduled_on(task, date(2026, 7, 24))  # Friday
    assert tasks_store.is_scheduled_on(task, date(2026, 7, 25))  # Saturday


def test_is_scheduled_on_weekly_days():
    task = {"recurrence": "weekly_days", "weekdays": ["FR"]}
    assert tasks_store.is_scheduled_on(task, date(2026, 7, 24))  # Friday
    assert not tasks_store.is_scheduled_on(task, date(2026, 7, 25))  # Saturday


def test_required_minutes_for_date_respects_cash_in():
    task = {"recurrence": "daily", "weekdays": [], "targetMinutes": 60, "cashedInDates": {"2026-07-24": 20}}
    assert tasks_store.required_minutes_for_date(task, date(2026, 7, 24)) == 40


def test_required_minutes_for_date_never_negative():
    task = {"recurrence": "daily", "weekdays": [], "targetMinutes": 60, "cashedInDates": {"2026-07-24": 999}}
    assert tasks_store.required_minutes_for_date(task, date(2026, 7, 24)) == 0


def test_required_minutes_zero_on_unscheduled_day():
    task = {"recurrence": "weekly_days", "weekdays": ["MO"], "targetMinutes": 60, "cashedInDates": {}}
    assert tasks_store.required_minutes_for_date(task, date(2026, 7, 24)) == 0  # Friday


def test_logged_seconds_for_date_sums_matching_sessions(isolate_tasks):
    task = {"id": "t1"}
    day = date(2026, 7, 20)
    sessions = [
        {
            "source": "task", "eventId": "t1",
            "startTime": _iso(day, 9, 0), "endTime": _iso(day, 9, 30),
            "violationLog": [],
        },
        {
            "source": "task", "eventId": "t1",
            "startTime": _iso(day, 10, 0), "endTime": _iso(day, 10, 15),
            "violationLog": [],
        },
        # different task -- must not count
        {
            "source": "task", "eventId": "other",
            "startTime": _iso(day, 11, 0), "endTime": _iso(day, 12, 0),
            "violationLog": [],
        },
        # manual session -- must not count
        {
            "source": "manual", "eventId": None,
            "startTime": _iso(day, 13, 0), "endTime": _iso(day, 14, 0),
            "violationLog": [],
        },
    ]
    assert tasks_store.logged_seconds_for_date(task, day, sessions) == 45 * 60


def test_logged_seconds_for_date_includes_live_session(isolate_tasks):
    task = {"id": "t1"}
    day = date.today()
    start = datetime.combine(day, datetime.min.time()).replace(hour=9)
    live_status = {
        "isActive": True, "source": "task", "eventId": "t1",
        "startTime": start.isoformat(), "violationLog": [],
    }
    logged = tasks_store.logged_seconds_for_date(task, day, [], live_status=live_status)
    # elapsed since 9:00 today, roughly -- just assert it's positive and
    # matches worked_seconds() directly for consistency between the two.
    expected = tasks_store.worked_seconds(start.isoformat(), None, [])
    assert logged == expected
    assert logged > 0


def test_vacation_balance_earns_surplus_from_past_days():
    task = {
        "id": "t1", "recurrence": "daily", "weekdays": [],
        "targetMinutes": 30, "cashedInDates": {},
    }
    yesterday = date.today() - timedelta(days=1)
    two_days_ago = date.today() - timedelta(days=2)
    sessions = [
        {
            "source": "task", "eventId": "t1",
            "startTime": _iso(yesterday, 9, 0), "endTime": _iso(yesterday, 9, 50),
            "violationLog": [],
        },  # 50m logged, 30m required -> 20m surplus
        {
            "source": "task", "eventId": "t1",
            "startTime": _iso(two_days_ago, 9, 0), "endTime": _iso(two_days_ago, 9, 20),
            "violationLog": [],
        },  # 20m logged, under target -> 0 surplus
    ]
    assert tasks_store.vacation_balance_minutes(task, sessions) == 20


def test_vacation_balance_excludes_today():
    task = {"id": "t1", "recurrence": "daily", "weekdays": [], "targetMinutes": 30, "cashedInDates": {}}
    today = date.today()
    sessions = [
        {
            "source": "task", "eventId": "t1",
            "startTime": _iso(today, 9, 0), "endTime": _iso(today, 10, 30),
            "violationLog": [],
        },  # 90m logged today -- must not count as banked yet
    ]
    assert tasks_store.vacation_balance_minutes(task, sessions) == 0


def test_vacation_balance_unaffected_by_later_goal_time_increase(isolate_tasks):
    week_ago = date.today() - timedelta(days=7)
    yesterday = date.today() - timedelta(days=1)
    task = {
        "id": "t1", "recurrence": "daily", "weekdays": [], "cashedInDates": {},
        "targetMinutes": 30,
        "targetMinutesHistory": [{"date": week_ago.isoformat(), "minutes": 30}],
    }
    sessions = [{
        "source": "task", "eventId": "t1",
        "startTime": _iso(yesterday, 9, 0), "endTime": _iso(yesterday, 9, 50),
        "violationLog": [],
    }]  # 50m logged yesterday against a 30m target -> 20m banked

    assert tasks_store.vacation_balance_minutes(task, sessions) == 20

    # Raising today's goal to 200m appends a new history entry dated today
    # (mirroring what update_task does) instead of overwriting the old one.
    task["targetMinutes"] = 200
    task["targetMinutesHistory"].append({"date": date.today().isoformat(), "minutes": 200})

    # Yesterday's target must still resolve to 30, and its already-banked
    # 20m of vacation must be untouched by today's goal-time change.
    assert tasks_store.vacation_balance_minutes(task, sessions) == 20
    assert tasks_store.required_minutes_for_date(task, yesterday) == 30
    assert tasks_store.required_minutes_for_date(task, date.today()) == 200


def test_update_task_appends_target_minutes_history_entry(isolate_tasks):
    task = tasks_store.create_task({
        "name": "Deep Work", "recurrence": "daily", "weekdays": [], "targetMinutes": 30,
    })
    created_date = task["createdAt"][:10]
    assert task["targetMinutesHistory"] == [{"date": created_date, "minutes": 30}]

    task = tasks_store.update_task(task["id"], {"targetMinutes": 200})
    today_key = date.today().isoformat()
    if created_date == today_key:
        # Same-day change: the creation-day entry is updated in place, not
        # duplicated -- only one target is ever "in effect" for a given day.
        assert task["targetMinutesHistory"] == [{"date": today_key, "minutes": 200}]
    else:
        assert task["targetMinutesHistory"] == [
            {"date": created_date, "minutes": 30},
            {"date": today_key, "minutes": 200},
        ]


def test_cash_in_reduces_balance_and_todays_requirement(isolate_tasks):
    yesterday = date.today() - timedelta(days=1)
    task = tasks_store.create_task({
        "name": "Deep Work", "recurrence": "daily", "weekdays": [], "targetMinutes": 30,
    })
    sessions = [
        {
            "source": "task", "eventId": task["id"],
            "startTime": _iso(yesterday, 9, 0), "endTime": _iso(yesterday, 9, 50),
            "violationLog": [],
        },
    ]
    assert tasks_store.vacation_balance_minutes(task, sessions) == 20

    updated = tasks_store.cash_in(task["id"], date.today(), 15, sessions)
    assert updated["cashedInDates"][date.today().isoformat()] == 15
    assert tasks_store.required_minutes_for_date(updated, date.today()) == 15
    assert tasks_store.vacation_balance_minutes(updated, sessions) == 5


def test_cash_in_rejects_overspend(isolate_tasks):
    task = tasks_store.create_task({
        "name": "Deep Work", "recurrence": "daily", "weekdays": [], "targetMinutes": 30,
    })
    with pytest.raises(ValueError):
        tasks_store.cash_in(task["id"], date.today(), 15, sessions=[])


def test_create_update_delete_roundtrip(isolate_tasks):
    task = tasks_store.create_task({"name": "Writing", "targetMinutes": 45})
    assert task["name"] == "Writing"
    assert task["id"]

    fetched = tasks_store.get_task(task["id"])
    assert fetched["targetMinutes"] == 45

    tasks_store.update_task(task["id"], {"targetMinutes": 60})
    assert tasks_store.get_task(task["id"])["targetMinutes"] == 60

    assert tasks_store.delete_task(task["id"]) is True
    assert tasks_store.get_task(task["id"]) is None
    assert tasks_store.delete_task(task["id"]) is False
