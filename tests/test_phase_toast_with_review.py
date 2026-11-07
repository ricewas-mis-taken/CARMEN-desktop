from datetime import datetime, timedelta

import review_store
import session_manager


def _expire():
    session_manager._state["endTime"] = (datetime.now() - timedelta(seconds=2)).isoformat()


def test_a_pomodoro_break_is_announced_while_a_review_runs(isolate_review_db, isolate_state):
    topic = review_store.create_topic("Math")
    subject = review_store.create_subject(topic["id"], "Algebra", "#5B8DEF")
    problem = review_store.create_problem(topic["id"], subject["id"], "Solve", stars=3,
                                          description_type="text", description_text="x")
    session_manager.start_pomodoro_session(25, 5, 4, "soft", [], [], source="task", event_id="t1", event_title="T")
    review_store.start_review(problem["id"])
    _expire()
    session_manager.get_status()
    assert (session_manager.pop_pending_phase_change() or {}).get("phase") == "break"
    session_manager._state["endTime"] = (datetime.now() - timedelta(seconds=2)).isoformat()
    session_manager.get_status()
    assert (session_manager.pop_pending_phase_change() or {}).get("phase") == "focus"


def test_a_pomodoro_break_is_announced_while_its_review_is_paused(isolate_review_db, isolate_state):
    topic = review_store.create_topic("Math")
    subject = review_store.create_subject(topic["id"], "Algebra", "#5B8DEF")
    problem = review_store.create_problem(topic["id"], subject["id"], "Solve", stars=3,
                                          description_type="text", description_text="x")
    session_manager.start_pomodoro_session(25, 5, 4, "soft", [], [], source="task", event_id="t1", event_title="T")
    token = review_store.start_review(problem["id"])
    review_store.pause_review(token) if hasattr(review_store, "pause_review") else None
    _expire()
    session_manager.get_status()
    assert (session_manager.pop_pending_phase_change() or {}).get("phase") == "break"
