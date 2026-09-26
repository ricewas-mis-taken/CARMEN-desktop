"""Tests for GET /status merging in review_store's independent
review-in-progress state (see review_store.get_active_review()'s
docstring) -- a review started while a different session (e.g. a
pomodoro) is already active used to be entirely invisible here."""
import api_server
import review_store
import session_manager


def _make_topic_and_subject():
    topic = review_store.create_topic("Math")
    subject = review_store.create_subject(topic["id"], "Quadratics", "#4A90D9")
    return topic, subject


def client():
    api_server.app.config["TESTING"] = True
    return api_server.app.test_client()


def test_status_review_in_progress_is_none_by_default(isolate_state, isolate_review_db):
    resp = client().get("/status")
    assert resp.get_json()["reviewInProgress"] is None


def test_status_surfaces_a_review_started_alongside_an_unrelated_session(isolate_state, isolate_review_db):
    topic, subject = _make_topic_and_subject()
    problem = review_store.create_problem(
        topic["id"], subject["id"], "Solve it", stars=3, description_type="text", description_text="x",
    )
    session_manager.start_session(45, "soft", [], [], source="task", event_id="t1", event_title="School")
    review_store.start_review(problem["id"])

    resp = client().get("/status")
    data = resp.get_json()
    assert data["isActive"] is True
    assert data["source"] == "task"
    assert data["reviewInProgress"]["problemId"] == problem["id"]
    assert data["reviewInProgress"]["problemName"] == "Solve it"


def test_status_review_in_progress_survives_the_other_session_ending(isolate_state, isolate_review_db):
    topic, subject = _make_topic_and_subject()
    problem = review_store.create_problem(
        topic["id"], subject["id"], "Solve it", stars=3, description_type="text", description_text="x",
    )
    session_manager.start_session(45, "soft", [], [], source="task", event_id="t1", event_title="School")
    review_store.start_review(problem["id"])

    session_manager.end_session()

    resp = client().get("/status")
    data = resp.get_json()
    assert data["isActive"] is False
    assert data["reviewInProgress"]["problemId"] == problem["id"]


def test_status_does_not_duplicate_a_review_that_is_itself_the_active_session(isolate_state, isolate_review_db):
    topic, subject = _make_topic_and_subject()
    problem = review_store.create_problem(
        topic["id"], subject["id"], "Solve it", stars=3, description_type="text", description_text="x",
    )
    review_store.start_review(problem["id"])
    session_manager.start_session(
        45, "soft", [], [], source="review", event_id="t1", event_title="School",
        review_problem_id=problem["id"],
    )

    resp = client().get("/status")
    data = resp.get_json()
    assert data["reviewProblemId"] == problem["id"]
    assert data["reviewInProgress"] is None
