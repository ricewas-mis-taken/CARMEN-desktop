"""Different minutes for different weekdays, and a custom-length goal period."""
from datetime import date, datetime, timedelta

import pytest

import sync_client
import tasks_store


@pytest.fixture
def isolate_tasks(tmp_path, monkeypatch):
    monkeypatch.setattr(tasks_store, "TASKS_PATH", str(tmp_path / "tasks.json"))
    yield


MON, TUE, WED = date(2026, 3, 2), date(2026, 3, 3), date(2026, 3, 4)


def _task(**extra):
    base = {"name": "Piano", "color": "#112233", "recurrence": "weekly_days",
            "weekdays": ["MO", "WE"], "targetMinutes": 30}
    base.update(extra)
    return tasks_store.create_task(base)


def test_each_ticked_day_can_have_its_own_time(isolate_tasks):
    task = _task(dayMinutes={"MO": 20, "WE": 90})
    assert tasks_store.required_minutes_for_date(task, MON) == 20
    assert tasks_store.required_minutes_for_date(task, WED) == 90
    assert tasks_store.required_minutes_for_date(task, TUE) == 0       # not ticked: free


def test_a_ticked_day_without_its_own_time_uses_the_default(isolate_tasks):
    task = _task(dayMinutes={"WE": 90})
    assert tasks_store.required_minutes_for_date(task, MON) == 30


def test_no_per_day_times_means_the_same_every_day(isolate_tasks):
    task = _task()
    assert tasks_store.required_minutes_for_date(task, MON) == 30
    assert tasks_store.required_minutes_for_date(task, WED) == 30


def test_changing_a_days_time_does_not_rewrite_the_past(isolate_tasks):
    task = _task(dayMinutes={"MO": 20})
    # pretend the task was created long ago, then change Monday to 60 today
    tasks = tasks_store.load_tasks()
    tasks[0]["targetMinutesHistory"][0]["date"] = "2026-01-01"
    tasks_store.save_tasks(tasks)
    tasks_store.update_task(task["id"], {"dayMinutes": {"MO": 60}})
    updated = tasks_store.get_task(task["id"])
    assert tasks_store.required_minutes_for_date(updated, MON) == 20                       # 2026-03-02 was before today
    assert tasks_store.required_minutes_for_date(updated, date.today() + timedelta(days=14 - date.today().weekday())) == 60


def test_removing_per_day_times_is_recorded(isolate_tasks):
    task = _task(dayMinutes={"MO": 20})
    tasks_store.update_task(task["id"], {"dayMinutes": {}})
    history = tasks_store.get_task(task["id"])["targetMinutesHistory"]
    assert history[-1].get("dayMinutes") == {}


def test_plain_tasks_keep_a_plain_history(isolate_tasks):
    task = tasks_store.create_task({"name": "Plain", "color": "#445566", "targetMinutes": 30})
    assert "dayMinutes" not in task["targetMinutesHistory"][0]


def test_custom_period_counts_from_its_start_day(isolate_tasks):
    task = tasks_store.create_task({
        "name": "Thesis", "color": "#778899", "recurrence": "custom_total", "targetMinutes": 600,
        "periodDays": 10, "periodAnchor": "2026-03-01",
    })
    assert tasks_store.period_bounds(task, date(2026, 3, 1)) == (date(2026, 3, 1), date(2026, 3, 10))
    assert tasks_store.period_bounds(task, date(2026, 3, 10)) == (date(2026, 3, 1), date(2026, 3, 10))
    assert tasks_store.period_bounds(task, date(2026, 3, 11)) == (date(2026, 3, 11), date(2026, 3, 20))
    assert tasks_store.period_bounds(task, date(2026, 2, 25)) == (date(2026, 2, 19), date(2026, 2, 28))
    assert tasks_store.period_phrase(task) == "these 10 days"
    assert tasks_store.is_period_task(task)


def test_custom_period_defaults_its_start_to_today(isolate_tasks):
    task = tasks_store.create_task({
        "name": "Sprint", "color": "#778899", "recurrence": "custom_total", "targetMinutes": 300, "periodDays": 5})
    assert task["periodAnchor"] == date.today().isoformat()
    assert tasks_store.days_left_in_period(task, date.today()) == 5


def test_custom_period_with_a_silly_length_is_a_week(isolate_tasks):
    for bad in (None, 0, 1, 9999, "ten", True):
        task = {"recurrence": "custom_total", "periodDays": bad, "periodAnchor": "2026-03-02"}
        assert tasks_store.custom_period_days(task) == 7


def test_custom_period_logs_across_all_its_days(isolate_tasks):
    task = tasks_store.create_task({
        "name": "Thesis", "color": "#778899", "recurrence": "custom_total", "targetMinutes": 600,
        "periodDays": 10, "periodAnchor": "2026-03-01"})
    def work(day, minutes):
        return {"source": "task", "eventId": task["id"], "startTime": datetime(day.year, day.month, day.day, 9).isoformat(),
                "endTime": datetime(day.year, day.month, day.day, 9, minutes).isoformat(), "violationLog": []}
    sessions = [work(date(2026, 3, 2), 40), work(date(2026, 3, 9), 50)]
    snap = tasks_store.goal_snapshot(task, date(2026, 3, 9), sessions)
    assert snap["logged"] == 90 and snap["required"] == 600 and snap["daysLeft"] == 2


def test_sync_cleans_bad_day_times_and_period_length():
    cleaned = sync_client._normalize_task({
        "id": "x", "name": "n", "dayMinutes": {"MO": 30, "XX": 5, "TU": -3, "WE": "a"}, "periodDays": 1,
        "targetMinutesHistory": [{"date": "2026-01-01", "minutes": 30, "dayMinutes": {"FR": 10, "no": 1}}]})
    assert cleaned["dayMinutes"] == {"MO": 30}
    assert cleaned["periodDays"] is None
    assert cleaned["targetMinutesHistory"][0]["dayMinutes"] == {"FR": 10}
