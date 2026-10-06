"""Flask-level tests for the /review/* endpoints in api_server.py -- thin
wiring around review_store.py, so these mostly check status codes, request
parsing (including multipart problem creation), and error responses rather
than re-testing the scheduling/persistence logic itself (see
tests/test_review_store.py for that)."""
import io

import pytest

import api_server
import config


@pytest.fixture
def client(isolate_config):
    api_server.app.config["TESTING"] = True
    test_client = api_server.app.test_client()
    # Every mutating route now requires this header (see api_server.py's
    # _require_token) -- attached once here so every existing test keeps
    # exercising its actual behavior instead of hitting 401 first.
    test_client.environ_base["HTTP_X_CARMEN_TOKEN"] = config.get_api_token()
    return test_client


def _create_topic(client, name="Math"):
    return client.post("/review/topics", json={"name": name}).get_json()


def _create_subject(client, topic_id, name="Quadratics", color="#4A90D9"):
    return client.post(f"/review/topics/{topic_id}/subjects", json={"name": name, "color": color}).get_json()


def test_create_and_list_topics(client, isolate_review_db):
    assert client.get("/review/topics").get_json() == []
    topic = _create_topic(client)
    assert topic["name"] == "Math"
    assert client.get("/review/topics").get_json() == [topic]


def test_create_topic_rejects_empty_name(client, isolate_review_db):
    resp = client.post("/review/topics", json={"name": "  "})
    assert resp.status_code == 400


def test_create_and_list_subjects(client, isolate_review_db):
    topic = _create_topic(client)
    subject = _create_subject(client, topic["id"])
    assert subject["color"] == "#4A90D9"
    assert client.get(f"/review/topics/{topic['id']}/subjects").get_json() == [subject]


def test_create_problem_with_text_description(client, isolate_review_db):
    topic = _create_topic(client)
    subject = _create_subject(client, topic["id"])

    resp = client.post(
        f"/review/topics/{topic['id']}/problems",
        data={
            "name": "Solve x^2-5x+6",
            "subject_id": str(subject["id"]),
            "stars": "3",
            "description_type": "text",
            "description_text": "factor it",
        },
    )
    assert resp.status_code == 201
    problem = resp.get_json()
    assert problem["name"] == "Solve x^2-5x+6"
    assert problem["descriptionText"] == "factor it"
    assert problem["subjectColor"] == "#4A90D9"


def test_create_problem_with_photo_description(client, isolate_review_db):
    topic = _create_topic(client)
    subject = _create_subject(client, topic["id"])

    resp = client.post(
        f"/review/topics/{topic['id']}/problems",
        data={
            "name": "Diagram problem",
            "subject_id": str(subject["id"]),
            "stars": "2",
            "description_type": "photo",
            "description_photo": (io.BytesIO(b"fake-png-bytes"), "diagram.png"),
        },
        content_type="multipart/form-data",
    )
    assert resp.status_code == 201
    problem = resp.get_json()
    assert problem["descriptionPhotoPath"] is not None
    assert problem["descriptionPhotoPath"].endswith(".png")


def test_create_problem_missing_photo_file_rejected(client, isolate_review_db):
    topic = _create_topic(client)
    subject = _create_subject(client, topic["id"])
    resp = client.post(
        f"/review/topics/{topic['id']}/problems",
        data={
            "name": "No photo",
            "subject_id": str(subject["id"]),
            "stars": "2",
            "description_type": "photo",
        },
    )
    assert resp.status_code == 400


def test_create_problem_invalid_stars_rejected(client, isolate_review_db):
    topic = _create_topic(client)
    subject = _create_subject(client, topic["id"])
    resp = client.post(
        f"/review/topics/{topic['id']}/problems",
        data={
            "name": "Bad stars",
            "subject_id": str(subject["id"]),
            "stars": "9",
            "description_type": "text",
            "description_text": "x",
        },
    )
    assert resp.status_code == 400


def test_problem_detail_not_found(client, isolate_review_db):
    resp = client.get("/review/problems/999999")
    assert resp.status_code == 404


def test_start_and_finish_review_flow(client, isolate_review_db):
    topic = _create_topic(client)
    subject = _create_subject(client, topic["id"])
    created = client.post(
        f"/review/topics/{topic['id']}/problems",
        data={
            "name": "Solve it", "subject_id": str(subject["id"]), "stars": "3",
            "description_type": "text", "description_text": "x",
        },
    ).get_json()

    start_resp = client.post(f"/review/problems/{created['id']}/start")
    assert start_resp.status_code == 200
    token = start_resp.get_json()["sessionToken"]

    finish_resp = client.post(f"/review/problems/{created['id']}/finish", json={"session_token": token})
    assert finish_resp.status_code == 200
    finished = finish_resp.get_json()
    assert finished["reviewCount"] == 1

    # Token is single-use.
    reuse_resp = client.post(f"/review/problems/{created['id']}/finish", json={"session_token": token})
    assert reuse_resp.status_code == 409


def test_finish_review_missing_token_rejected(client, isolate_review_db):
    resp = client.post("/review/problems/1/finish", json={})
    assert resp.status_code == 400


def test_finish_review_sent_to_wrong_problem_id_does_not_commit_the_outcome(client, isolate_review_db):
    """Regression test: the route used to call review_store.finish_review()
    -- which commits its side effects (logs the review, bumps review_count,
    reschedules the problem) immediately -- BEFORE checking whether the
    token's own problem_id matched the URL's problem_id. A token sent to
    the wrong problem's finish URL got silently recorded against the
    CORRECT problem while the client was told 409, and the token was left
    permanently burned with no way to retry against the right URL."""
    topic = _create_topic(client)
    subject = _create_subject(client, topic["id"])
    real_problem = client.post(
        f"/review/topics/{topic['id']}/problems",
        data={
            "name": "Solve it", "subject_id": str(subject["id"]), "stars": "3",
            "description_type": "text", "description_text": "x",
        },
    ).get_json()
    other_problem = client.post(
        f"/review/topics/{topic['id']}/problems",
        data={
            "name": "Solve another", "subject_id": str(subject["id"]), "stars": "3",
            "description_type": "text", "description_text": "x",
        },
    ).get_json()

    token = client.post(f"/review/problems/{real_problem['id']}/start").get_json()["sessionToken"]

    wrong_resp = client.post(f"/review/problems/{other_problem['id']}/finish", json={"session_token": token})
    assert wrong_resp.status_code == 409

    # The review must NOT have been silently recorded against real_problem
    # -- retrying against the correct problem_id with the same token must
    # still work, not 409 as "already used".
    right_resp = client.post(f"/review/problems/{real_problem['id']}/finish", json={"session_token": token})
    assert right_resp.status_code == 200
    assert right_resp.get_json()["reviewCount"] == 1


def test_finish_review_forwards_self_solved_shakiness_and_duration(client, isolate_review_db):
    """Regression test: the route never read self_solved/shakiness/
    duration_seconds out of the request body at all -- every review
    finished over HTTP was unconditionally logged as solved, shakiness 3,
    no matter what the caller actually sent."""
    import review_store

    topic = _create_topic(client)
    subject = _create_subject(client, topic["id"])
    problem = client.post(
        f"/review/topics/{topic['id']}/problems",
        data={
            "name": "Solve it", "subject_id": str(subject["id"]), "stars": "3",
            "description_type": "text", "description_text": "x",
        },
    ).get_json()
    token = client.post(f"/review/problems/{problem['id']}/start").get_json()["sessionToken"]

    resp = client.post(
        f"/review/problems/{problem['id']}/finish",
        json={"session_token": token, "self_solved": False, "shakiness": 5, "duration_seconds": 42},
    )
    assert resp.status_code == 200

    sessions = review_store.list_sessions(problem["id"])
    assert len(sessions) == 1
    assert sessions[0]["selfSolved"] is False
    # shakiness only applies when self_solved=True (see
    # _apply_review_outcome's own docstring) -- stored as None here since
    # self_solved=False, which is itself proof the field was actually
    # forwarded and consulted rather than silently defaulted to True.
    assert sessions[0]["shakiness"] is None
    assert sessions[0]["durationSeconds"] == 42


def test_finish_review_forwards_shakiness_when_self_solved(client, isolate_review_db):
    import review_store

    topic = _create_topic(client)
    subject = _create_subject(client, topic["id"])
    problem = client.post(
        f"/review/topics/{topic['id']}/problems",
        data={
            "name": "Solve it", "subject_id": str(subject["id"]), "stars": "3",
            "description_type": "text", "description_text": "x",
        },
    ).get_json()
    token = client.post(f"/review/problems/{problem['id']}/start").get_json()["sessionToken"]

    resp = client.post(
        f"/review/problems/{problem['id']}/finish",
        json={"session_token": token, "self_solved": True, "shakiness": 5},
    )
    assert resp.status_code == 200

    sessions = review_store.list_sessions(problem["id"])
    assert sessions[0]["selfSolved"] is True
    assert sessions[0]["shakiness"] == 5


def test_finish_review_rejects_invalid_shakiness(client, isolate_review_db):
    topic = _create_topic(client)
    subject = _create_subject(client, topic["id"])
    problem = client.post(
        f"/review/topics/{topic['id']}/problems",
        data={
            "name": "Solve it", "subject_id": str(subject["id"]), "stars": "3",
            "description_type": "text", "description_text": "x",
        },
    ).get_json()
    token = client.post(f"/review/problems/{problem['id']}/start").get_json()["sessionToken"]

    resp = client.post(
        f"/review/problems/{problem['id']}/finish",
        json={"session_token": token, "shakiness": 99},
    )
    assert resp.status_code == 400
    # Token must still be usable afterward -- a rejected malformed request
    # must not consume it.
    ok_resp = client.post(f"/review/problems/{problem['id']}/finish", json={"session_token": token})
    assert ok_resp.status_code == 200


def test_start_review_unknown_problem_404(client, isolate_review_db):
    resp = client.post("/review/problems/999999/start")
    assert resp.status_code == 404


def test_start_review_while_one_already_in_progress_409(client, isolate_review_db):
    topic = _create_topic(client)
    subject = _create_subject(client, topic["id"])
    created = client.post(
        f"/review/topics/{topic['id']}/problems",
        data={
            "name": "Solve it", "subject_id": str(subject["id"]), "stars": "3",
            "description_type": "text", "description_text": "x",
        },
    ).get_json()
    other = client.post(
        f"/review/topics/{topic['id']}/problems",
        data={
            "name": "Solve another", "subject_id": str(subject["id"]), "stars": "3",
            "description_type": "text", "description_text": "x",
        },
    ).get_json()

    first_start = client.post(f"/review/problems/{created['id']}/start")
    assert first_start.status_code == 200

    # A second start -- even for a different, perfectly valid problem --
    # must be rejected distinctly from "not found" while one review is
    # already being timed (review_store.start_review()'s one-at-a-time
    # guard, previously unenforced by this route at all).
    second_start = client.post(f"/review/problems/{other['id']}/start")
    assert second_start.status_code == 409


def test_due_only_query_param(client, isolate_review_db):
    topic = _create_topic(client)
    subject = _create_subject(client, topic["id"])
    client.post(
        f"/review/topics/{topic['id']}/problems",
        data={
            "name": "Not due yet", "subject_id": str(subject["id"]), "stars": "1",
            "description_type": "text", "description_text": "x",
        },
    )
    due = client.get(f"/review/topics/{topic['id']}/problems?due_only=true").get_json()
    all_problems = client.get(f"/review/topics/{topic['id']}/problems?due_only=false").get_json()
    assert due == []
    assert len(all_problems) == 1


def test_finish_review_over_a_week_is_rejected_without_burning_the_token(client, isolate_review_db):
    topic = _create_topic(client)
    subject = _create_subject(client, topic["id"])
    created = client.post(
        f"/review/topics/{topic['id']}/problems",
        data={
            "name": "Solve it", "subject_id": str(subject["id"]), "stars": "3",
            "description_type": "text", "description_text": "x",
        },
    ).get_json()
    token = client.post(f"/review/problems/{created['id']}/start").get_json()["sessionToken"]

    bad = client.post(f"/review/problems/{created['id']}/finish", json={"session_token": token, "duration_seconds": 10 ** 30})
    assert bad.status_code == 400

    ok = client.post(f"/review/problems/{created['id']}/finish", json={"session_token": token})
    assert ok.status_code == 200


def test_finish_review_whose_write_fails_can_be_retried(isolate_review_db, monkeypatch):
    import review_store

    topic = review_store.create_topic("Math")
    subject = review_store.create_subject(topic["id"], "Quadratics", "#4A90D9")
    problem = review_store.create_problem(
        topic["id"], subject["id"], "Solve it", stars=3, description_type="text", description_text="x",
    )
    token = review_store.start_review(problem["id"])

    real = review_store._apply_review_outcome
    monkeypatch.setattr(review_store, "_apply_review_outcome", lambda *a, **kw: None)
    assert review_store.finish_review(token) is None

    monkeypatch.setattr(review_store, "_apply_review_outcome", real)
    assert review_store.finish_review(token) is not None
