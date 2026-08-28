"""Flask-level tests for the browser-profile-blocking API surface:
GET /browser-profiles/running, POST /blocklist/browser-profiles, and
POST /session/start accepting blocked_browser_profiles."""
import pytest

import api_server
import session_manager


@pytest.fixture
def client():
    api_server.app.config["TESTING"] = True
    return api_server.app.test_client()


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


def test_session_start_falls_back_to_saved_browser_profile_blocklist(client, isolate_state):
    import config
    config.update_config(lambda cfg: cfg.update({"browserProfileBlocklist": ["Chrome"]}))

    resp = client.post(
        "/session/start",
        json={"duration_minutes": 10, "lock_mode": "soft", "process_blocklist": [], "domain_whitelist": []},
    )
    assert resp.status_code == 200
    assert session_manager.get_status()["blockedBrowserProfiles"] == ["Chrome"]
