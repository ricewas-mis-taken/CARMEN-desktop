"""Tests for api_server.py's _require_token guard -- every state-changing
endpoint used to have zero authentication, so anything on the machine that
could reach 127.0.0.1:5847 could end a session, kill the app, or unblock any
app with a one-word "reason"."""
import pytest

import api_server
import config


@pytest.fixture
def client(isolate_config):
    api_server.app.config["TESTING"] = True
    return api_server.app.test_client()


def test_mutating_endpoint_rejects_missing_token(client):
    resp = client.post("/session/pause", json={})
    assert resp.status_code == 401


def test_mutating_endpoint_rejects_wrong_token(client):
    resp = client.post("/session/pause", json={}, headers={"X-Carmen-Token": "not-the-real-token"})
    assert resp.status_code == 401


def test_mutating_endpoint_accepts_correct_token(client):
    token = config.get_api_token()
    resp = client.post("/session/pause", json={}, headers={"X-Carmen-Token": token})
    assert resp.status_code == 200


def test_internal_quit_requires_token(client):
    calls = []
    api_server.register_quit_callback(lambda: calls.append(True))
    resp = client.post("/internal/quit")
    assert resp.status_code == 401
    assert calls == []


@pytest.mark.parametrize(
    "path",
    ["/health", "/status", "/history", "/apps/running", "/whitelist/domains", "/api/focus/rules"],
)
def test_read_only_endpoints_stay_open(client, path):
    resp = client.get(path)
    assert resp.status_code != 401
