"""Flask-level tests for the /review/* endpoints in api_server.py -- thin
wiring around review_store.py, so these mostly check status codes, request
parsing (including multipart problem creation), and error responses rather
than re-testing the scheduling/persistence logic itself (see
tests/test_review_store.py for that)."""
import io

import pytest

import api_server


@pytest.fixture
def client():
    api_server.app.config["TESTING"] = True
    return api_server.app.test_client()


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


def test_start_review_unknown_problem_404(client, isolate_review_db):
    resp = client.post("/review/problems/999999/start")
    assert resp.status_code == 404


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
