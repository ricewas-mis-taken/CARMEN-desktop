"""Flask-level tests for POST /session/update -- editing an active session's
process blocklist, domain whitelist, and/or lock mode without ending and
restarting it. Before this endpoint existed, session_manager.update_blocklist()
had no caller at all (dead code) and there was no way to change lock_mode
mid-session whatsoever."""
import pytest

import api_server
import config
import session_manager


@pytest.fixture
def client(isolate_state):
    api_server.app.config["TESTING"] = True
    test_client = api_server.app.test_client()
    test_client.environ_base["HTTP_X_CARMEN_TOKEN"] = config.get_api_token()
    return test_client


def test_session_update_requires_active_session(client):
    resp = client.post("/session/update", json={"process_blocklist": ["bad.exe"]})
    assert resp.status_code == 400


def test_session_update_changes_process_blocklist(client):
    session_manager.start_session(25, "soft", ["old.exe"], [])
    resp = client.post("/session/update", json={"process_blocklist": ["new.exe"]})
    assert resp.status_code == 200
    assert resp.get_json()["processBlocklist"] == ["new.exe"]
    assert session_manager.get_status()["processBlocklist"] == ["new.exe"]


def test_session_update_changes_domain_whitelist(client):
    session_manager.start_session(25, "soft", [], ["old.com"])
    resp = client.post("/session/update", json={"domain_whitelist": ["new.com"]})
    assert resp.status_code == 200
    assert session_manager.get_status()["domainWhitelist"] == ["new.com"]


def test_session_update_changes_lock_mode_mid_session(client):
    session_manager.start_session(25, "soft", [], [])
    resp = client.post("/session/update", json={"lock_mode": "hard"})
    assert resp.status_code == 200
    assert resp.get_json()["lockMode"] == "hard"
    assert session_manager.get_status()["lockMode"] == "hard"


def test_session_update_rejects_invalid_lock_mode(client):
    session_manager.start_session(25, "soft", [], [])
    resp = client.post("/session/update", json={"lock_mode": "medium"})
    assert resp.status_code == 400
    assert session_manager.get_status()["lockMode"] == "soft"


def test_session_update_omitted_fields_keep_current_values(client):
    session_manager.start_session(25, "hard", ["kept.exe"], ["kept.com"])
    resp = client.post("/session/update", json={})
    assert resp.status_code == 200
    status = resp.get_json()
    assert status["processBlocklist"] == ["kept.exe"]
    assert status["domainWhitelist"] == ["kept.com"]
    assert status["lockMode"] == "hard"


def test_session_update_rejects_non_string_list(client):
    session_manager.start_session(25, "soft", [], [])
    resp = client.post("/session/update", json={"process_blocklist": [123]})
    assert resp.status_code == 400
