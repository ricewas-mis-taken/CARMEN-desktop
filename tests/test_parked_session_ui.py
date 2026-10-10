"""The Focus tab, a task card and a review banner when a paused session waits
behind a different running one."""
import pytest

import qt_ui.focus_tab as focus_tab
import qt_ui.review_tab as review_tab
import qt_ui.tasks_tab as tasks_tab
import session_history
import session_manager as sm
import tasks_store


@pytest.fixture
def isolate_tasks(tmp_path, monkeypatch):
    monkeypatch.setattr(tasks_store, "TASKS_PATH", str(tmp_path / "tasks.json"))
    yield


def _task(name):
    return tasks_store.create_task({"name": name, "targetMinutes": 30})


def _start_for(task, **kw):
    return sm.start_session(30, "hard", task.get("processBlocklist", []), [], source="task",
                            event_id=task["id"], event_title=task["name"], **kw)


# --- Focus tab ---

def test_start_stays_disabled_while_a_session_is_running(qtbot, isolate_state):
    sm.start_session(25, "soft", [], [])
    tab = focus_tab.FocusTab()
    qtbot.addWidget(tab)
    assert not tab._start_button.isEnabled()


def test_start_is_available_while_the_current_session_is_paused(qtbot, isolate_state):
    sm.start_session(25, "soft", [], [])
    sm.pause_session()
    tab = focus_tab.FocusTab()
    qtbot.addWidget(tab)
    assert tab._start_button.isEnabled()


def test_focus_tab_shows_the_waiting_session_and_a_switch_button(qtbot, isolate_state):
    sm.start_session(25, "soft", [], [], source="task", event_id="a", event_title="Alpha")
    sm.pause_session()
    sm.start_session(25, "soft", [], [], source="task", event_id="b", event_title="Beta")
    tab = focus_tab.FocusTab()
    qtbot.addWidget(tab)
    tab.show()
    tab._refresh_status()
    assert "waiting (paused): alpha" in tab._status_label.text().lower()
    assert tab._swap_button.isVisible()
    assert not tab._start_button.isEnabled()  # the one waiting spot is taken, and Beta is running
    tab._swap_button.click()
    assert sm.get_status()["eventTitle"] == "Alpha"


# --- Tasks tab ---

def test_other_task_cards_unlock_while_the_running_session_is_paused(qtbot, isolate_tasks, isolate_state):
    a, b = _task("Alpha"), _task("Beta")
    _start_for(a)
    card_b = tasks_tab._TaskCard(b, on_changed=lambda: None)
    qtbot.addWidget(card_b)
    assert card_b._is_locked_by_other_session()
    sm.pause_session()
    assert not card_b._is_locked_by_other_session()


def test_the_waiting_tasks_card_shows_it_is_waiting_and_can_switch_back(qtbot, isolate_tasks, isolate_state):
    a, b = _task("Alpha"), _task("Beta")
    _start_for(a)
    sm.pause_session()
    _start_for(b)
    card_a = tasks_tab._TaskCard(a, on_changed=lambda: None)
    card_b = tasks_tab._TaskCard(b, on_changed=lambda: None)
    for card in (card_a, card_b):
        qtbot.addWidget(card)
        card.show()
    status = sm.get_status()
    card_a.update_dynamic(status, session_history.load_all())
    card_b.update_dynamic(status, session_history.load_all())

    assert card_a._running_panel.isVisible() and card_b._running_panel.isVisible()
    assert "waiting" in card_a._countdown_label.text().lower()
    assert card_a._pause_button.text() == "Switch to this"
    assert card_b._pause_button.text() == "Pause"
    assert not card_a.property("locked")

    card_a._pause_resume()
    assert sm.get_status()["eventId"] == a["id"] and not sm.get_status()["isPaused"]


def test_ending_the_waiting_task_leaves_the_running_one_alone(qtbot, isolate_tasks, isolate_state):
    a, b = _task("Alpha"), _task("Beta")
    _start_for(a)
    sm.pause_session()
    _start_for(b)
    card_a = tasks_tab._TaskCard(a, on_changed=lambda: None)
    qtbot.addWidget(card_a)
    card_a._end_task()
    status = sm.get_status()
    assert status["eventId"] == b["id"] and status["isActive"] and status["parkedSession"] is None


# --- review timer banner ---

def _review_session(task):
    return sm.start_session(
        tasks_store.BURNOUT_MINUTES, "hard", [], [], source="review", event_id=task["id"],
        event_title="Review", review_problem_name="P1", review_subject_name="S", review_problem_id=7,
    )


def _banner(qtbot):
    banner = review_tab._ReviewBanner(on_finished=lambda: None)
    qtbot.addWidget(banner)
    return banner


def test_a_review_waiting_behind_a_task_keeps_its_own_frozen_time(qtbot, isolate_tasks, isolate_state, isolate_review_db):
    a, other = _task("Alpha"), _task("Other")
    _review_session(a)
    banner = _banner(qtbot)
    banner.start({"name": "P1", "id": 7}, token=None, end_session_on_finish=True)
    sm.pause_session()
    _start_for(other)

    status, parked = banner._linked_view()
    assert parked and status["isPaused"] and status["reviewProblemId"] == 7
    assert banner._currently_paused()
    assert banner._pause_button_text(True) == "Switch to this"
    banner._tick()
    assert banner._end_session_on_finish  # still alive, not mistaken for an ended session
    assert sm.get_status()["eventId"] == other["id"] and not sm.get_status()["isPaused"]  # untouched by the banner


def test_the_waiting_reviews_button_switches_instead_of_pausing_the_other_task(qtbot, isolate_tasks, isolate_state,
                                                                              isolate_review_db):
    a, other = _task("Alpha"), _task("Other")
    _review_session(a)
    banner = _banner(qtbot)
    banner.start({"name": "P1", "id": 7}, token=None, end_session_on_finish=True)
    sm.pause_session()
    _start_for(other)
    banner._pause_resume()
    status = sm.get_status()
    assert status["reviewProblemId"] == 7 and not status["isPaused"]
    assert status["parkedSession"]["eventId"] == other["id"]


def test_ending_a_waiting_review_ends_its_own_session_not_the_running_task(qtbot, isolate_tasks, isolate_state,
                                                                          isolate_review_db):
    a, other = _task("Alpha"), _task("Other")
    _review_session(a)
    sm.pause_session()
    _start_for(other)
    review_tab._end_session_for_problem({"id": 7})
    status = sm.get_status()
    assert status["eventId"] == other["id"] and status["isActive"] and status["parkedSession"] is None


def test_a_review_whose_session_was_replaced_for_good_closes_itself(qtbot, isolate_tasks, isolate_state,
                                                                    isolate_review_db):
    a, other = _task("Alpha"), _task("Other")
    _review_session(a)
    banner = _banner(qtbot)
    banner.start({"name": "P1", "id": 7}, token=None, end_session_on_finish=True)
    _start_for(other)  # review was running, not paused: it is replaced, not parked
    status, parked = banner._linked_view()
    assert status == {"isActive": False} and not parked
