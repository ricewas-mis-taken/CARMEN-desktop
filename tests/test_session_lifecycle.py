"""No Qt involved -- confirms session_manager's business logic still works
end-to-end after the UI-layer migration, i.e. that the Tk->Qt port hasn't
accidentally coupled UI changes to business-logic behavior. Runs on every
stage per the migration plan's verification approach."""
import session_history
import session_manager


def test_start_and_natural_end_lifecycle(isolate_state):
    assert not session_manager.is_active()

    session_manager.start_session(25, "soft", ["good.exe"], ["good.com"])
    assert session_manager.is_active()
    status = session_manager.get_status()
    assert status["lockMode"] == "soft"
    assert status["processBlocklist"] == ["good.exe"]

    summary = session_manager.end_session(end_type="manual")
    assert not session_manager.is_active()
    assert summary["endType"] == "manual"

    history = session_history.load_all()
    assert len(history) == 1
    assert history[0]["lockMode"] == "soft"


def test_nuclear_end_records_reason(isolate_state):
    session_manager.start_session(10, "hard", [], [])
    summary = session_manager.end_session(end_type="nuclear", reason="testing nuclear end")
    assert summary["endType"] == "nuclear"
    assert summary["reason"] == "testing nuclear end"

    history = session_history.load_all()
    assert history[-1]["endType"] == "nuclear"
    assert history[-1]["reason"] == "testing nuclear end"


def test_pause_resume_round_trip(isolate_state):
    session_manager.start_session(25, "soft", [], [])
    assert not session_manager.get_status()["isPaused"]

    session_manager.pause_session()
    assert session_manager.get_status()["isPaused"]

    session_manager.resume_session()
    assert not session_manager.get_status()["isPaused"]


def test_review_problem_id_persists_through_pause_and_clears_on_end(isolate_state):
    """reviewProblemId is what lets a review resumed after an app restart
    (session_manager's own persisted state survives; review_store's
    start_review() token does not) still get logged on Finish -- see
    qt_ui/review_tab.py's _TopicView._resume_if_active."""
    session_manager.start_session(
        25, "soft", [], [], source="review",
        review_problem_name="Solve it", review_subject_name="Algebra", review_problem_id=42,
    )
    assert session_manager.get_status()["reviewProblemId"] == 42

    session_manager.pause_session()
    assert session_manager.get_status()["reviewProblemId"] == 42

    session_manager.end_session()
    assert session_manager.get_status()["reviewProblemId"] is None
