"""GET /events/wait -- long-poll wake-up hint for the browser extension."""
import threading
import time

import pytest

import api_server
import state_events


@pytest.fixture(autouse=True)
def fresh_counter(monkeypatch):
    monkeypatch.setattr(state_events, "_version", 0)
    monkeypatch.setattr(state_events, "_waiters", 0)


@pytest.fixture
def client():
    api_server.app.config["TESTING"] = True
    return api_server.app.test_client()


def test_without_since_returns_the_baseline_at_once(client):
    resp = client.get("/events/wait")
    assert resp.status_code == 200
    assert resp.get_json() == {"version": 0, "changed": False}


def test_times_out_when_nothing_changes(client):
    start = time.time()
    resp = client.get("/events/wait?since=0&timeout=0.2")
    assert resp.get_json() == {"version": 0, "changed": False}
    assert time.time() - start >= 0.15


def test_returns_as_soon_as_something_changes(client):
    threading.Timer(0.15, state_events.bump).start()
    start = time.time()
    resp = client.get("/events/wait?since=0&timeout=10")
    assert resp.get_json() == {"version": 1, "changed": True}
    assert time.time() - start < 3


def test_timeout_is_clamped(client):
    state_events.bump()
    assert client.get("/events/wait?since=0&timeout=999999").get_json()["changed"] is True


def test_rejects_non_numeric_arguments(client):
    assert client.get("/events/wait?since=abc").status_code == 400
    assert client.get("/events/wait?since=0&timeout=nan").status_code == 400
    assert client.get("/events/wait?since=0&timeout=x").status_code == 400
