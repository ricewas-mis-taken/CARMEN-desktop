"""Reviews and a Pomodoro of the same task: the reviews ride beside it, pause and resume with it, and you can
switch between them while the Pomodoro keeps running."""
import pytest

import review_store
import session_manager
import tasks_store
import qt_ui.review_tab as review_tab
from test_review_switch import _tab, _linked_task_id, world  # noqa: F401  (shared fixtures)


def _two_problems(world):
    topic, first = world.make_topic("Linked", linked=True)
    subject = review_store.list_subjects(topic["id"])[0]
    second = review_store.create_problem(
        topic["id"], subject["id"], "Second", stars=5, description_type="text", description_text="x")
    return topic, first, second


def test_switching_reviews_leaves_the_pomodoro_running(world):
    topic, first, second = _two_problems(world)
    tab = _tab(world)
    task_id = _linked_task_id(topic)
    session_manager.start_pomodoro_session(25, 5, 4, "hard", [], [], source="task", event_id=task_id, event_title="Same")
    view = tab._topic_views[topic["id"]]

    view._begin_review(first)
    assert review_store.get_active_review()["problemId"] == first["id"]
    view._start_problem(second)                       # switch while the first is running

    status = session_manager.get_status()
    assert status["source"] == "task" and not status["isPaused"]          # the Pomodoro never stopped
    assert review_store.get_active_review()["problemId"] == second["id"]
    assert [r["name"] for r in tab._parked_rows()] == ["P-Linked"]        # the first waits in the strip


def test_pausing_the_pomodoro_pauses_the_review_and_resuming_resumes_it(world):
    topic, first, _second = _two_problems(world)
    tab = _tab(world)
    task_id = _linked_task_id(topic)
    session_manager.start_pomodoro_session(25, 5, 4, "hard", [], [], source="task", event_id=task_id, event_title="Same")
    tab._topic_views[topic["id"]]._begin_review(first)

    session_manager.pause_session("aside")
    assert review_store.get_active_review()["isPaused"] is True
    session_manager.resume_session()
    assert review_store.get_active_review()["isPaused"] is False


def test_a_review_started_before_the_pomodoro_rides_beside_it_afterwards(world):
    topic, first, _second = _two_problems(world)
    tab = _tab(world)
    tab._topic_views[topic["id"]]._begin_review(first)          # owns its own session
    assert session_manager.get_status()["source"] == "review"

    assert review_tab.make_live_review_independent()            # what starting a Pomodoro does first
    task_id = _linked_task_id(topic)
    session_manager.start_pomodoro_session(25, 5, 4, "hard", [], [], source="task", event_id=task_id, event_title="Same")

    assert review_store.get_active_review()["problemId"] == first["id"]
    session_manager.pause_session("aside")
    assert review_store.get_active_review()["isPaused"] is True
    session_manager.resume_session()
    assert review_store.get_active_review()["isPaused"] is False


def test_a_first_attempt_started_before_the_pomodoro_rides_beside_it(world):
    from test_review_switch import _start_current
    topic, first, _second = _two_problems(world)
    tab = _tab(world)
    dialog = _start_current(world, tab, True, topic, first)      # owns its own session
    banner = tab._topic_views[topic["id"]]._review_banner
    assert session_manager.get_status()["source"] == "review" and banner._end_session_on_finish

    assert review_tab.make_live_review_independent()             # what starting a Pomodoro does first
    assert not banner._end_session_on_finish and banner.is_running()
    task_id = _linked_task_id(topic)
    session_manager.start_pomodoro_session(25, 5, 4, "hard", [], [], source="task", event_id=task_id, event_title="Same")
    banner._tick()
    assert not banner._is_paused

    session_manager.pause_session("aside")
    banner._tick()
    assert banner._is_paused and banner._auto_paused               # paused with the Pomodoro
    session_manager.resume_session()
    banner._tick()
    assert not banner._is_paused
    banner._end_early()
    dialog.close()
    from PySide6.QtWidgets import QApplication
    QApplication.processEvents()


def test_a_review_left_running_when_the_pomodoro_ends_gets_its_own_blocking_session(world):
    topic, first, _second = _two_problems(world)
    tab = _tab(world)
    task_id = _linked_task_id(topic)
    session_manager.start_pomodoro_session(25, 5, 4, "hard", [], [], source="task", event_id=task_id, event_title="Same")
    view = tab._topic_views[topic["id"]]
    view._begin_review(first)
    assert session_manager.get_status()["source"] == "task"

    session_manager.end_session()                                # the Pomodoro stops, the review keeps going
    banner = view._review_banner
    banner._tick()

    status = session_manager.get_status()
    assert status["isActive"] and status["source"] == "review"
    assert status["reviewProblemId"] == first["id"]
    assert review_store.get_active_review() is None              # one tracker, not two
    assert banner._end_session_on_finish


def test_a_review_started_while_the_pomodoro_is_paused_leaves_it_paused(world):
    topic, first, _second = _two_problems(world)
    tab = _tab(world)
    task_id = _linked_task_id(topic)
    session_manager.start_pomodoro_session(25, 5, 4, "hard", [], [], source="task", event_id=task_id, event_title="Same")
    session_manager.pause_session("aside")

    tab._topic_views[topic["id"]]._begin_review(first)

    status = session_manager.get_status()
    assert status["source"] == "review" and not status["isPaused"]            # the review blocks on its own
    parked = status["parkedSessions"]
    assert len(parked) == 1 and parked[0]["source"] == "task" and parked[0]["pomodoro"] is not None


def test_resuming_the_paused_pomodoro_keeps_the_review_running_beside_it(world):
    topic, first, _second = _two_problems(world)
    tab = _tab(world)
    task_id = _linked_task_id(topic)
    session_manager.start_pomodoro_session(25, 5, 4, "hard", [], [], source="task", event_id=task_id, event_title="Same")
    session_manager.pause_session("aside")
    tab._topic_views[topic["id"]]._begin_review(first)
    park_id = session_manager.get_status()["parkedSessions"][0]["parkId"]

    review_tab.switch_to_parked(park_id)

    status = session_manager.get_status()
    assert status["source"] == "task" and not status["isPaused"]              # the pomodoro runs again
    assert status["parkedSessions"] == []                                     # nothing left waiting
    active = review_store.get_active_review()
    assert active["problemId"] == first["id"] and not active["isPaused"]      # the review never stopped


def _first_attempt_beside_pomodoro(world):
    from test_review_switch import _start_current
    topic, first, _second = _two_problems(world)
    tab = _tab(world)
    dialog = _start_current(world, tab, True, topic, first)
    banner = tab._topic_views[topic["id"]]._review_banner
    assert review_tab.make_live_review_independent()
    task_id = _linked_task_id(topic)
    session_manager.start_pomodoro_session(25, 5, 4, "hard", [], [], source="task", event_id=task_id, event_title="Same")
    banner._tick()
    return topic, tab, banner, dialog


def _close(banner, dialog):
    banner._end_early()
    dialog.close()
    from PySide6.QtWidgets import QApplication
    QApplication.processEvents()


def test_a_first_attempt_left_running_when_the_pomodoro_ends_keeps_blocking(world):
    topic, tab, banner, dialog = _first_attempt_beside_pomodoro(world)
    banner._accumulated_seconds = 300                    # five minutes in, beside the pomodoro

    session_manager.end_session()                        # the Pomodoro ends, the first attempt keeps going
    banner._tick()

    status = session_manager.get_status()
    assert status["isActive"] and session_manager.is_first_attempt(status)
    assert banner._end_session_on_finish and banner._elapsed_seconds_now() >= 300
    assert review_store.saved_first_attempt() is None    # the session carries it now
    _close(banner, dialog)


def test_a_first_attempt_beside_a_pomodoro_is_saved_and_comes_back_after_a_restart(world):
    topic, tab, banner, dialog = _first_attempt_beside_pomodoro(world)
    banner._accumulated_seconds = 240
    banner._start_time = None
    banner._is_paused = True
    banner._publish_first_attempt()

    saved = review_store.saved_first_attempt()
    assert saved and saved["topic_id"] == topic["id"] and saved["elapsed_seconds"] == 240

    banner._tick_timer.stop()
    banner.hide()
    banner._first_attempt_callback = None                 # the banner dies with the process
    review_store._first_attempt = None
    view = tab._topic_views[topic["id"]]
    assert view._resume_saved_first_attempt()
    assert banner.is_running() and banner._first_attempt_callback is not None
    assert banner._elapsed_seconds_now() == 240 and banner._is_paused
    _close(banner, dialog)
    assert review_store.saved_first_attempt() is None
