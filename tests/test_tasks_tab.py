"""Widget-level tests for the Tasks tab's running card (qt_ui/tasks_tab.py) --
in particular that a review timer running against a task's linked topic
(source="review", same eventId as the task) is recognized as *this* task's
own running session, not some other session locking the card out."""
import pytest

import session_history
import session_manager
import tasks_store
import qt_ui.tasks_tab as tasks_tab


@pytest.fixture
def isolate_tasks(tmp_path, monkeypatch):
    monkeypatch.setattr(tasks_store, "TASKS_PATH", str(tmp_path / "tasks.json"))
    yield


def _make_task(**overrides):
    data = {"name": "Study", "targetMinutes": 30}
    data.update(overrides)
    return tasks_store.create_task(data)


def test_review_session_for_linked_task_shows_as_running(qtbot, isolate_tasks, isolate_state):
    task = _make_task()
    card = tasks_tab._TaskCard(task, on_changed=lambda: None)
    qtbot.addWidget(card)
    card.show()

    session_manager.start_session(
        30, "soft", [], [],
        source="review", event_id=task["id"], event_title="Study - Algebra review",
        review_problem_name="Quadratics 1", review_subject_name="Algebra",
    )
    status = session_manager.get_status()
    card.update_dynamic(status, session_history.load_all())

    assert card._running_panel.isVisible()
    assert not card.property("locked")
    text = card._countdown_label.text()
    assert "Quadratics 1" in text
    assert "Algebra" in text
    assert "elapsed" in text.lower()


def test_review_session_pause_button_works_for_linked_task(qtbot, isolate_tasks, isolate_state):
    task = _make_task()
    card = tasks_tab._TaskCard(task, on_changed=lambda: None)
    qtbot.addWidget(card)

    session_manager.start_session(
        30, "soft", [], [],
        source="review", event_id=task["id"], event_title="Study - Algebra review",
        review_problem_name="Quadratics 1", review_subject_name="Algebra",
    )
    card.update_dynamic(session_manager.get_status(), session_history.load_all())
    assert card._pause_button.text() == "Pause"

    card._pause_resume()
    assert session_manager.get_status()["isPaused"]
    card.update_dynamic(session_manager.get_status(), session_history.load_all())
    assert card._pause_button.text() == "Resume"

    card._pause_resume()
    assert not session_manager.get_status()["isPaused"]
