"""The review banner must come back whenever its task's review session does --
including when this view was opened while that session was waiting behind
another task, or the session was switched back to from elsewhere."""
import pytest

import qt_ui.review_tab as review_tab
import review_store
import session_manager as sm
import tasks_store


@pytest.fixture
def isolate_tasks(tmp_path, monkeypatch):
    monkeypatch.setattr(tasks_store, "TASKS_PATH", str(tmp_path / "tasks.json"))


def _linked_topic():
    task = tasks_store.create_task({"name": "usaco", "targetMinutes": 30})
    topic = review_store.create_topic("USACO")
    review_store.update_topic_link(topic["id"], task["id"])
    return topic, task


def _review_session(task):
    return sm.start_session(
        tasks_store.BURNOUT_MINUTES, "hard", [], [], source="review", event_id=task["id"],
        event_title="Review", review_problem_name="P1", review_subject_name="S", review_problem_id=7,
    )


def _other_task_over(task):
    sm.pause_session()
    sm.start_session(30, "hard", [], [], source="task", event_id="other", event_title="Other")


def test_view_opened_while_the_review_waits_still_shows_the_banner(qtbot, isolate_tasks, isolate_state, isolate_review_db):
    topic, task = _linked_topic()
    _review_session(task)
    _other_task_over(task)
    view = review_tab._TopicView(topic["id"])
    qtbot.addWidget(view)
    assert view._review_banner.is_running()
    assert view._review_banner._pause_btn.text() == "Switch to this"


def test_banner_returns_when_the_waiting_review_is_switched_back_to(qtbot, isolate_tasks, isolate_state, isolate_review_db):
    topic, task = _linked_topic()
    view = review_tab._TopicView(topic["id"])
    qtbot.addWidget(view)
    assert not view._review_banner.is_running()
    _review_session(task)
    _other_task_over(task)
    sm.swap_with_parked(sm.get_status()["parkedSessions"][0]["parkId"])
    view._show_banner_for_linked_session()
    assert view._review_banner.is_running()
    assert view._review_banner._pause_btn.text() == "Pause"


def test_banner_returns_after_the_task_over_it_ends_and_the_review_is_resumed(
        qtbot, isolate_tasks, isolate_state, isolate_review_db):
    topic, task = _linked_topic()
    _review_session(task)
    _other_task_over(task)
    view = review_tab._TopicView(topic["id"])
    qtbot.addWidget(view)
    sm.end_session()          # the other task finishes, the review comes back paused
    sm.resume_session()
    view._show_banner_for_linked_session()
    assert view._review_banner.is_running()
    assert not sm.get_status()["isPaused"]


def test_nothing_shows_for_a_topic_without_a_review_session(qtbot, isolate_tasks, isolate_state, isolate_review_db):
    topic, _task = _linked_topic()
    view = review_tab._TopicView(topic["id"])
    qtbot.addWidget(view)
    view._show_banner_for_linked_session()
    assert not view._review_banner.is_running()
