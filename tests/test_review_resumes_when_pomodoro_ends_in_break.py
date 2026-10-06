"""An independent review auto-paused for a pomodoro break must be resumed when
the pomodoro ends during that break (manual end / nuclear / superseded), not
only when it ends naturally -- otherwise it stays paused as 'Resume (on break)'
with no break left to wait for."""
from datetime import datetime, timedelta

import pytest

import review_store
import session_manager


def _make_problem():
    topic = review_store.create_topic("Math")
    subject = review_store.create_subject(topic["id"], "A", "#111111")
    return review_store.create_problem(topic["id"], subject["id"], "P", 3, "text", description_text="x")


def _enter_break():
    session_manager._state["endTime"] = (datetime.now() - timedelta(seconds=1)).isoformat()
    assert session_manager.get_status()["isBreak"] is True


@pytest.mark.parametrize("how", ["manual", "superseded"])
def test_review_resumes_when_pomodoro_ends_during_break(isolate_state, isolate_review_db, how):
    problem = _make_problem()
    session_manager.start_pomodoro_session(25, 5, 2, "soft", [], [])
    token = review_store.start_review(problem["id"])
    assert token and review_store.get_active_review()["isPaused"] is False

    _enter_break()
    assert review_store.get_active_review()["autoPaused"] is True

    if how == "manual":
        session_manager.end_session()
    else:
        session_manager.start_session(25, "soft", [], [])

    active = review_store.get_active_review()
    assert active["isPaused"] is False
    assert active["autoPaused"] is False
