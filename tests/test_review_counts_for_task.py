"""A review of a topic linked to a task is time on that task: while it runs, on the
task card's live count, and after it ends, from the session history."""
from datetime import date, datetime, timedelta

import session_manager
import tasks_store


def test_running_and_finished_reviews_count_toward_the_linked_task(isolate_state, tmp_path, monkeypatch):
    monkeypatch.setattr(tasks_store, "TASKS_PATH", str(tmp_path / "tasks.json"))
    task = tasks_store.create_task({"name": "usaco", "color": "#112233", "targetMinutes": 60})
    session_manager.start_session(
        duration_minutes=tasks_store.BURNOUT_MINUTES, lock_mode="soft", process_blocklist=[], domain_whitelist=[],
        source="review", event_id=task["id"], event_title="usaco - Sorting review",
        review_problem_name="p", review_subject_name="s", review_problem_id=1,
    )
    status = session_manager.get_status()
    # pretend it has been running 20 minutes
    status = dict(status, startTime=(datetime.now() - timedelta(minutes=20)).isoformat())
    live = tasks_store.logged_seconds_for_date(task, date.today(), [], live_status=status) / 60
    assert 19.5 < live < 20.5

    finished = [{
        "source": "review", "eventId": task["id"], "violationLog": [],
        "startTime": (datetime.now() - timedelta(minutes=50)).isoformat(),
        "endTime": (datetime.now() - timedelta(minutes=30)).isoformat(),
    }]
    assert 19.5 < tasks_store.logged_seconds_for_date(task, date.today(), finished) / 60 < 20.5
    snap = tasks_store.goal_snapshot(task, date.today(), finished, live_status=status)
    assert 39 < snap["logged"] < 41      # 20 finished + 20 running


def test_a_review_of_another_task_does_not_count(isolate_state, tmp_path, monkeypatch):
    monkeypatch.setattr(tasks_store, "TASKS_PATH", str(tmp_path / "tasks.json"))
    task = tasks_store.create_task({"name": "a", "color": "#112233", "targetMinutes": 60})
    other = {"source": "review", "eventId": "someone-else", "violationLog": [],
             "startTime": (datetime.now() - timedelta(minutes=30)).isoformat(), "endTime": datetime.now().isoformat()}
    assert tasks_store.logged_seconds_for_date(task, date.today(), [other]) == 0


def test_a_first_attempt_counts_too(isolate_state, tmp_path, monkeypatch):
    monkeypatch.setattr(tasks_store, "TASKS_PATH", str(tmp_path / "tasks.json"))
    task = tasks_store.create_task({"name": "usaco", "color": "#112233", "targetMinutes": 60})
    # started exactly the way the Review tab starts one (no problem exists yet)
    session_manager.start_session(
        duration_minutes=tasks_store.BURNOUT_MINUTES, lock_mode="soft", process_blocklist=[], domain_whitelist=[],
        source="review", event_id=task["id"], event_title="usaco - first attempt",
        review_problem_name=session_manager.FIRST_ATTEMPT_NAME, review_subject_name=None,
    )
    status = session_manager.get_status()
    assert session_manager.is_first_attempt(status)
    status = dict(status, startTime=(datetime.now() - timedelta(minutes=15)).isoformat())
    assert 14.5 < tasks_store.logged_seconds_for_date(task, date.today(), [], live_status=status) / 60 < 15.5

    done = [{
        "source": "review", "eventId": task["id"], "violationLog": [], "reviewProblemName": "First attempt",
        "startTime": (datetime.now() - timedelta(minutes=40)).isoformat(),
        "endTime": (datetime.now() - timedelta(minutes=10)).isoformat(),
    }]
    assert 29.5 < tasks_store.logged_seconds_for_date(task, date.today(), done) / 60 < 30.5
