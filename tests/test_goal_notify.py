from datetime import date, datetime, timedelta

import pytest

import goal_notify
import session_manager as sm
import tasks_store


@pytest.fixture
def world(tmp_path, monkeypatch, isolate_state):
    monkeypatch.setattr(tasks_store, "TASKS_PATH", str(tmp_path / "tasks.json"))
    monkeypatch.setattr(goal_notify, "PATH", str(tmp_path / "goal_notified.json"))


def _run_for(task, minutes_ago):
    sm.start_session(120, "soft", [], [], source="task", event_id=task["id"], event_title=task["name"])
    sm._state["startTime"] = (datetime.now() - timedelta(minutes=minutes_ago)).isoformat()
    return sm.get_status()


def test_reaching_the_daily_target_is_said_once(world):
    task = tasks_store.create_task({"name": "Study", "targetMinutes": 30})
    assert goal_notify.check(_run_for(task, 10)) is None            # 10 of 30
    status = _run_for(task, 31)
    reached = goal_notify.check(status)
    assert reached and reached["name"] == "Study" and reached["required"] == 30
    assert goal_notify.check(status) is None                         # not again, however long it goes on
    assert goal_notify.check(_run_for(task, 90)) is None


def test_it_waits_for_the_goal_to_really_be_met_and_ignores_pauses_breaks_and_other_sources(world):
    task = tasks_store.create_task({"name": "Study", "targetMinutes": 30})
    status = _run_for(task, 40)
    assert goal_notify.check({**status, "isPaused": True}) is None
    assert goal_notify.check({**status, "isBreak": True}) is None
    assert goal_notify.check({**status, "source": "manual"}) is None
    assert goal_notify.check({"isActive": False}) is None
    assert goal_notify.check(status)                                  # none of the above used it up


def test_week_goals_and_goalless_tasks_do_not_notify(world):
    weekly = tasks_store.create_task({"name": "Weekly", "targetMinutes": 10, "recurrence": "weekly_total"})
    assert tasks_store.is_period_task(weekly)
    assert goal_notify.check(_run_for(weekly, 60)) is None
    none = tasks_store.create_task({"name": "Free", "targetMinutes": 0})
    assert goal_notify.check(_run_for(none, 60)) is None
