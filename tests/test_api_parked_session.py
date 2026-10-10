"""POST /session/parked/switch and /session/parked/end -- what the browser
extension's popup uses to bring a waiting session back or end it."""
import pytest

import api_server
import config
import session_history
import session_manager as sm


@pytest.fixture
def client(isolate_state):
    api_server.app.config["TESTING"] = True
    test_client = api_server.app.test_client()
    test_client.environ_base["HTTP_X_CARMEN_TOKEN"] = config.get_api_token()
    return test_client


def _waiting(*names):
    for name in names:
        sm.start_session(30, "hard", [f"{name}.exe"], [], source="task", event_id=name, event_title=name)
        sm.pause_session()
    sm.start_session(30, "hard", ["running.exe"], [], source="task", event_id="run", event_title="run")
    return sm.get_status()["parkedSessions"]


def test_status_lists_every_waiting_session(client):
    _waiting("a", "b", "c")
    waiting = client.get("/status").get_json()["parkedSessions"]
    assert [p["eventTitle"] for p in waiting] == ["c", "b", "a"]
    assert all(p["parkId"] for p in waiting)


def test_switch_brings_the_chosen_session_to_the_front(client):
    waiting = _waiting("a", "b", "c")
    oldest = [p for p in waiting if p["eventTitle"] == "a"][0]
    resp = client.post("/session/parked/switch", json={"parkId": oldest["parkId"]})
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["eventTitle"] == "a" and not body["isPaused"]
    assert [p["eventTitle"] for p in body["parkedSessions"]] == ["run", "c", "b"]


def test_end_removes_only_the_chosen_waiting_session(client):
    waiting = _waiting("a", "b")
    resp = client.post("/session/parked/end", json={"parkId": waiting[0]["parkId"]})
    assert resp.status_code == 200 and resp.get_json()["eventTitle"] == "b"
    status = sm.get_status()
    assert status["eventTitle"] == "run" and [p["eventTitle"] for p in status["parkedSessions"]] == ["a"]
    assert [h["eventTitle"] for h in session_history.load_all()] == ["b"]


@pytest.mark.parametrize("route", ["/session/parked/switch", "/session/parked/end"])
def test_bad_or_unknown_ids_are_refused_and_change_nothing(client, route):
    _waiting("a")
    for body in ({}, {"parkId": ""}, {"parkId": 5}, {"parkId": "x" * 100}):
        assert client.post(route, json=body).status_code == 400
    assert client.post(route, json={"parkId": "nope"}).status_code == 404
    status = sm.get_status()
    assert status["eventTitle"] == "run" and len(status["parkedSessions"]) == 1


@pytest.mark.parametrize("route", ["/session/parked/switch", "/session/parked/end"])
def test_both_routes_need_the_token(client, route):
    waiting = _waiting("a")
    client.environ_base["HTTP_X_CARMEN_TOKEN"] = "wrong"
    assert client.post(route, json={"parkId": waiting[0]["parkId"]}).status_code in (401, 403)
    assert len(sm.get_status()["parkedSessions"]) == 1
