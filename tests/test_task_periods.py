"""Goal periods: a total per week / 2 weeks / month that is only due at the end of
it, next to the per-day goals ("daily", "weekly_days") that must stay exactly as
they were."""
from datetime import date, datetime, timedelta

import pytest

import sync_client
import tasks_store


@pytest.fixture
def isolate_tasks(tmp_path, monkeypatch):
    monkeypatch.setattr(tasks_store, "TASKS_PATH", str(tmp_path / "tasks.json"))
    yield


def _iso(d, h=9, m=0):
    return datetime(d.year, d.month, d.day, h, m).isoformat()


def _work(task_id, day, minutes, hour=9):
    end_h, end_m = divmod(hour * 60 + minutes, 60)
    return {
        "source": "task", "eventId": task_id,
        "startTime": _iso(day, hour, 0), "endTime": _iso(day, end_h, end_m),
        "violationLog": [],
    }


def _weekly(**extra):
    task = {"id": "t1", "recurrence": "weekly_total", "targetMinutes": 600,
            "createdAt": "2026-01-01T09:00:00", "targetMinutesHistory": [{"date": "2026-01-01", "minutes": 600}]}
    task.update(extra)
    return task


# 2026-10-19 is a Monday, 2026-10-25 the Sunday after it.
MON, WED, SUN = date(2026, 10, 19), date(2026, 10, 21), date(2026, 10, 25)
NEXT_MON = date(2026, 10, 26)


def test_week_runs_monday_to_sunday():
    assert tasks_store.period_bounds(_weekly(), WED) == (MON, SUN)
    assert tasks_store.period_bounds(_weekly(), MON) == (MON, SUN)
    assert tasks_store.period_bounds(_weekly(), SUN) == (MON, SUN)
    assert tasks_store.period_bounds(_weekly(), NEXT_MON)[0] == NEXT_MON


def test_two_week_stretches_count_from_the_anchor_monday():
    task = _weekly(recurrence="biweekly_total", periodAnchor="2026-10-19")
    assert tasks_store.period_bounds(task, date(2026, 10, 30)) == (MON, date(2026, 11, 1))
    assert tasks_store.period_bounds(task, date(2026, 11, 2)) == (date(2026, 11, 2), date(2026, 11, 15))
    # before the anchor still lands on a whole two-week stretch
    assert tasks_store.period_bounds(task, date(2026, 10, 12)) == (date(2026, 10, 5), date(2026, 10, 18))


def test_month_is_the_calendar_month():
    task = _weekly(recurrence="monthly_total")
    assert tasks_store.period_bounds(task, date(2026, 2, 10)) == (date(2026, 2, 1), date(2026, 2, 28))
    assert tasks_store.period_bounds(task, date(2028, 2, 10)) == (date(2028, 2, 1), date(2028, 2, 29))
    assert tasks_store.period_bounds(task, date(2026, 12, 31)) == (date(2026, 12, 1), date(2026, 12, 31))


def test_per_day_goals_are_their_own_one_day_stretch():
    daily = {"recurrence": "daily"}
    assert tasks_store.period_bounds(daily, WED) == (WED, WED)
    assert tasks_store.period_bounds({"recurrence": "weekly_days"}, WED) == (WED, WED)
    assert not tasks_store.is_period_task(daily)
    assert tasks_store.is_period_task(_weekly())


def test_days_left_counts_today():
    assert tasks_store.days_left_in_period(_weekly(), MON) == 7
    assert tasks_store.days_left_in_period(_weekly(), SUN) == 1


def test_logged_time_adds_up_across_the_whole_week_only():
    task = _weekly()
    sessions = [
        _work("t1", MON, 60), _work("t1", WED, 90), _work("t1", SUN, 30),
        _work("t1", date(2026, 10, 18), 120),  # the Sunday before: last week
        _work("t1", NEXT_MON, 45),             # the Monday after: next week
        _work("other", WED, 200),
    ]
    assert tasks_store.logged_seconds_for_period(task, WED, sessions) == 180 * 60


def test_every_day_counts_toward_a_period_goal():
    task = _weekly()
    assert all(tasks_store.is_scheduled_on(task, date(2026, 10, 19 + i)) for i in range(7))


def test_required_is_the_period_total_minus_vacation_cashed_in_that_period():
    task = _weekly(cashedInDates={"2026-10-20": 45, "2026-10-12": 30})  # the second is last week
    assert tasks_store.required_minutes_for_period(task, WED, today=WED) == 555


def test_a_target_change_mid_week_applies_to_the_whole_week():
    task = _weekly(targetMinutesHistory=[{"date": "2026-01-01", "minutes": 600}, {"date": "2026-10-22", "minutes": 300}])
    assert tasks_store.required_minutes_for_period(task, MON, today=date(2026, 10, 23)) == 300
    # last week's own closing target is untouched
    assert tasks_store.required_minutes_for_period(task, date(2026, 10, 14), today=date(2026, 10, 23)) == 600


def test_snapshot_of_a_period_goal():
    task = _weekly(cashedInDates={"2026-10-20": 30})
    sessions = [_work("t1", MON, 120), _work("t1", WED, 60)]
    snap = tasks_store.goal_snapshot(task, WED, sessions)
    assert snap["isPeriod"] and snap["phrase"] == "this week"
    assert (snap["start"], snap["end"], snap["daysLeft"]) == (MON, SUN, 5)
    assert snap["required"] == 570 and snap["cashed"] == 30 and snap["logged"] == 180


def test_snapshot_of_a_per_day_goal_is_unchanged():
    task = {"id": "t1", "recurrence": "daily", "targetMinutes": 40, "cashedInDates": {WED.isoformat(): 10},
            "targetMinutesHistory": [{"date": "2026-01-01", "minutes": 40}]}
    snap = tasks_store.goal_snapshot(task, WED, [_work("t1", WED, 25), _work("t1", MON, 99)])
    assert not snap["isPeriod"] and snap["phrase"] == "today"
    assert (snap["start"], snap["end"], snap["daysLeft"]) == (WED, WED, 1)
    assert snap["required"] == 30 and snap["cashed"] == 10 and snap["logged"] == 25


def test_only_finished_periods_bank_vacation(isolate_tasks):
    task = _weekly()
    last_week = date(2026, 10, 12)
    sessions = [_work("t1", last_week, 400), _work("t1", date(2026, 10, 14), 300),   # 700 of 600 last week: +100
                _work("t1", MON, 500), _work("t1", WED, 300)]                         # 800 of 600 this week: not banked yet
    assert tasks_store.vacation_balance_minutes(task, sessions, today=WED) == pytest.approx(100)


def test_a_short_week_banks_nothing(isolate_tasks):
    task = _weekly()
    sessions = [_work("t1", date(2026, 10, 12), 200)]
    assert tasks_store.vacation_balance_minutes(task, sessions, today=WED) == 0


def test_cashing_in_reduces_the_running_periods_requirement(isolate_tasks):
    created = tasks_store.create_task({"name": "Read", "recurrence": "weekly_total", "targetMinutes": 600})
    task_id = created["id"]
    # a banked surplus from a finished week: two weeks ago, 100 over target
    two_weeks_ago = date.today() - timedelta(days=14)
    sessions = [_work(task_id, two_weeks_ago, 700)]
    balance = tasks_store.vacation_balance_minutes(created, sessions)
    assert balance == pytest.approx(100)
    updated = tasks_store.cash_in(task_id, date.today(), 60, sessions)
    assert tasks_store.required_minutes_for_period(updated, date.today()) == 540
    assert tasks_store.vacation_balance_minutes(updated, sessions) == pytest.approx(40)


def test_a_two_week_goal_gets_this_weeks_monday_as_its_anchor(isolate_tasks):
    task = tasks_store.create_task({"name": "Essay", "recurrence": "biweekly_total", "targetMinutes": 900})
    assert task["periodAnchor"] == tasks_store.monday_of(date.today()).isoformat()
    other = tasks_store.create_task({"name": "Gym", "recurrence": "weekly_total", "targetMinutes": 300})
    assert other["periodAnchor"] is None
    switched = tasks_store.update_task(other["id"], {"recurrence": "biweekly_total"})
    assert switched["periodAnchor"] == tasks_store.monday_of(date.today()).isoformat()


def test_synced_tasks_with_a_bad_anchor_fall_back_to_none():
    good = sync_client._normalize_task({"id": "a", "name": "x", "recurrence": "biweekly_total", "periodAnchor": "2026-10-19"})
    bad = sync_client._normalize_task({"id": "b", "name": "x", "recurrence": "biweekly_total", "periodAnchor": 12345})
    assert good["periodAnchor"] == "2026-10-19"
    assert bad["periodAnchor"] is None
