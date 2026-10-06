"""Regression: duplicate topic/subject/problem names (and a reused subject
colour) must be a clean 409, not an unhandled DuplicateNameError -> 500."""
import pytest

import api_server
import config


@pytest.fixture
def client(isolate_config, isolate_review_db):
    api_server.app.config["TESTING"] = False  # real 500 handling, not exception propagation
    c = api_server.app.test_client()
    c.environ_base["HTTP_X_CARMEN_TOKEN"] = config.get_api_token()
    return c


def test_duplicate_topic_is_409(client):
    assert client.post("/review/topics", json={"name": "Math"}).status_code == 201
    assert client.post("/review/topics", json={"name": "math"}).status_code == 409


def test_duplicate_subject_name_and_color_are_409(client):
    t = client.post("/review/topics", json={"name": "Math"}).get_json()
    url = f"/review/topics/{t['id']}/subjects"
    assert client.post(url, json={"name": "Algebra", "color": "#111111"}).status_code == 201
    assert client.post(url, json={"name": "algebra", "color": "#222222"}).status_code == 409
    assert client.post(url, json={"name": "Calc", "color": "#111111"}).status_code == 409


def test_duplicate_problem_is_409(client):
    t = client.post("/review/topics", json={"name": "Math"}).get_json()
    s = client.post(f"/review/topics/{t['id']}/subjects", json={"name": "A", "color": "#111111"}).get_json()
    form = {"name": "P1", "subject_id": str(s["id"]), "stars": "3", "description_type": "text", "description_text": "x"}
    url = f"/review/topics/{t['id']}/problems"
    assert client.post(url, data=form).status_code == 201
    assert client.post(url, data=form).status_code == 409
