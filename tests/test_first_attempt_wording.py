"""A first attempt is never a "review": every screen just says "First attempt"."""
import session_manager as sm
import qt_ui.history_viewer as history_viewer
import qt_ui.mini_timer as mini_timer
import qt_ui.review_tab as review_tab
import session_history


def _first_attempt_session():
    return sm.start_session(
        480, "soft", [], [], source="review", event_id="task-1", event_title="usaco - first attempt",
        review_problem_name=sm.FIRST_ATTEMPT_NAME,
    )


def _review_session():
    return sm.start_session(
        480, "soft", [], [], source="review", event_id="task-1", event_title="usaco - Sets review",
        review_problem_name="Two Sum", review_subject_name="Sets", review_problem_id=7,
    )


def test_status_says_when_a_session_is_a_first_attempt(isolate_state):
    assert _first_attempt_session()["isFirstAttempt"] is True
    sm.end_session()
    assert _review_session()["isFirstAttempt"] is False
    sm.end_session()
    assert sm.start_session(25, "soft", [], [])["isFirstAttempt"] is False


def test_a_problem_that_happens_to_be_called_first_attempt_is_still_a_review(isolate_state):
    status = sm.start_session(480, "soft", [], [], source="review", review_problem_name="First attempt",
                              review_problem_id=3)
    assert status["isFirstAttempt"] is False


def test_waiting_first_attempts_are_flagged_too(isolate_state):
    _first_attempt_session()
    sm.pause_session()
    sm.start_session(25, "soft", [], [], source="task", event_id="b", event_title="b")
    waiting = sm.get_status()["parkedSessions"]
    assert [p["isFirstAttempt"] for p in waiting] == [True]


def test_mini_timer_label(isolate_state):
    assert mini_timer.describe(_first_attempt_session())[0] == "First attempt"
    sm.end_session()
    assert mini_timer.describe(_review_session())[0] == "Review: Two Sum"


def test_history_label(isolate_state):
    _first_attempt_session()
    sm.end_session()
    _review_session()
    sm.end_session()
    labels = [history_viewer._format_source_label(e) for e in session_history.load_all()]
    assert labels[0].startswith("First attempt") and "Review" not in labels[0]
    assert labels[1].startswith("Review: Two Sum")


def test_a_banner_rebuilt_for_a_first_attempt_does_not_say_reviewing(qtbot, isolate_state, isolate_review_db):
    banner = review_tab._ReviewBanner(on_finished=lambda: None)
    qtbot.addWidget(banner)
    _first_attempt_session()
    banner.start({"name": sm.FIRST_ATTEMPT_NAME, "id": None}, token=None, end_session_on_finish=True)
    assert banner._problem_label.text() == "Timing first attempt"
    banner._tick_timer.stop()
    sm.end_session()
    _review_session()
    banner.start({"name": "Two Sum", "id": 7}, token=None, end_session_on_finish=True)
    assert banner._problem_label.text() == "Reviewing: Two Sum"
    banner._tick_timer.stop()
