"""Flask-level tests for the browser-profile-blocking API surface:
GET /browser-profiles/running, POST /blocklist/browser-profiles, and
POST /session/start accepting blocked_browser_profiles."""
import pytest

import api_server
import config
import session_manager


@pytest.fixture
def client(isolate_config):
    api_server.app.config["TESTING"] = True
    test_client = api_server.app.test_client()
    test_client.environ_base["HTTP_X_CARMEN_TOKEN"] = config.get_api_token()
    return test_client


def test_browser_profiles_running_lists_open_profile_windows(client, monkeypatch):
    monkeypatch.setattr(
        api_server.window_tracker,
        "list_browser_profile_windows",
        lambda: [{"process_name": "chrome.exe", "aumi": "Chrome", "label": "chrome.exe — Default", "window_title": "x"}],
    )
    resp = client.get("/browser-profiles/running")
    assert resp.status_code == 200
    assert resp.get_json() == [
        {"process_name": "chrome.exe", "aumi": "Chrome", "label": "chrome.exe — Default", "window_title": "x"}
    ]


def test_blocklist_browser_profiles_rejects_non_list(client, isolate_state):
    resp = client.post("/blocklist/browser-profiles", json={"browser_profile_blocklist": "not-a-list"})
    assert resp.status_code == 400


def test_blocklist_browser_profiles_saves_to_config(client, isolate_state):
    resp = client.post("/blocklist/browser-profiles", json={"browser_profile_blocklist": ["Chrome.UserData.Profile4"]})
    assert resp.status_code == 200
    assert resp.get_json()["browserProfileBlocklist"] == ["Chrome.UserData.Profile4"]

    import config
    assert config.load_config()["browserProfileBlocklist"] == ["Chrome.UserData.Profile4"]


def test_session_start_accepts_blocked_browser_profiles(client, isolate_state):
    resp = client.post(
        "/session/start",
        json={
            "duration_minutes": 25,
            "lock_mode": "hard",
            "process_blocklist": [],
            "domain_whitelist": [],
            "blocked_browser_profiles": ["Chrome.UserData.Profile4"],
        },
    )
    assert resp.status_code == 200
    assert session_manager.get_status()["blockedBrowserProfiles"] == ["Chrome.UserData.Profile4"]


def test_session_start_rejects_an_absurdly_large_process_blocklist(client, isolate_state):
    """Regression test: a 2,000,000-entry process_blocklist used to be
    accepted with no complaint, bloating session_state.json to 44.7MB and
    making every subsequent violation-tick rewrite of that file (done from
    scratch on every violation, see session_manager.py's own _save())
    take ~0.6s -- degrading every poll for the rest of the session. A real
    hand-built blocklist never comes anywhere close to this size."""
    huge_blocklist = [f"proc{i}.exe" for i in range(2000)] + ["one_too_many.exe"]
    resp = client.post(
        "/session/start",
        json={
            "duration_minutes": 25,
            "lock_mode": "soft",
            "process_blocklist": huge_blocklist,
            "domain_whitelist": [],
        },
    )
    assert resp.status_code == 400
    assert not session_manager.get_status()["isActive"]


def test_session_start_rejects_an_absurdly_long_domain_entry(client, isolate_state):
    resp = client.post(
        "/session/start",
        json={
            "duration_minutes": 25,
            "lock_mode": "soft",
            "process_blocklist": [],
            "domain_whitelist": ["a" * 501 + ".com"],
        },
    )
    assert resp.status_code == 400
    assert not session_manager.get_status()["isActive"]


def test_session_start_falls_back_to_saved_browser_profile_blocklist(client, isolate_state):
    import config
    config.update_config(lambda cfg: cfg.update({"browserProfileBlocklist": ["Chrome"]}))

    resp = client.post(
        "/session/start",
        json={"duration_minutes": 10, "lock_mode": "soft", "process_blocklist": [], "domain_whitelist": []},
    )
    assert resp.status_code == 200
    assert session_manager.get_status()["blockedBrowserProfiles"] == ["Chrome"]
