"""Review API create routes must validate their parents: a subject needs an
existing topic; a problem needs an existing topic and a subject that belongs
to THAT topic. Otherwise orphan rows are written (review_store does not
enforce foreign keys) or a problem is hidden after another topic's deletion."""
import pytest

import api_server
import config
import review_store


@pytest.fixture
def client(isolate_state, isolate_review_db):
    return api_server.app.test_client()


def _hdr():
    return {"X-Carmen-Token": config.get_api_token()}


def _problem_form(subject_id):
    return {"name": "P", "subject_id": str(subject_id), "stars": "3",
            "description_type": "text", "description_text": "x"}


def _count(table):
    return review_store._get_conn().execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


def test_subject_under_missing_topic_is_404_and_writes_nothing(client):
    resp = client.post("/review/topics/999/subjects", json={"name": "S", "color": "#ff0000"}, headers=_hdr())
    assert resp.status_code == 404
    assert _count("review_subjects") == 0


def test_problem_with_missing_subject_is_404_and_writes_nothing(client):
    topic = client.post("/review/topics", json={"name": "T1"}, headers=_hdr()).get_json()
    resp = client.post(f"/review/topics/{topic['id']}/problems", data=_problem_form(999), headers=_hdr())
    assert resp.status_code == 404
    assert _count("review_problems") == 0


def test_problem_with_subject_from_another_topic_is_rejected(client):
    t1 = client.post("/review/topics", json={"name": "T1"}, headers=_hdr()).get_json()
    t2 = client.post("/review/topics", json={"name": "T2"}, headers=_hdr()).get_json()
    s2 = client.post(f"/review/topics/{t2['id']}/subjects", json={"name": "S", "color": "#ff0000"}, headers=_hdr()).get_json()
    resp = client.post(f"/review/topics/{t1['id']}/problems", data=_problem_form(s2["id"]), headers=_hdr())
    assert resp.status_code == 404
    assert _count("review_problems") == 0


def test_valid_problem_still_created(client):
    t1 = client.post("/review/topics", json={"name": "T1"}, headers=_hdr()).get_json()
    s1 = client.post(f"/review/topics/{t1['id']}/subjects", json={"name": "S", "color": "#ff0000"}, headers=_hdr()).get_json()
    resp = client.post(f"/review/topics/{t1['id']}/problems", data=_problem_form(s1["id"]), headers=_hdr())
    assert resp.status_code == 201
