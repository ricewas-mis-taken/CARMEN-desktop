import pytest

import api_server
import config
import screentime_categories
import screentime_store


@pytest.fixture
def isolate_screentime(tmp_path, monkeypatch):
    monkeypatch.setattr(screentime_store, "STATE_PATH", str(tmp_path / "screentime.json"))
    monkeypatch.setattr(screentime_store, "_data", {})
    monkeypatch.setattr(screentime_store, "_last_flush", 0.0)
    yield


def test_add_app_seconds_accumulates_within_a_day(isolate_screentime):
    screentime_store.add_app_seconds("chrome.exe", 10, when=__import__("datetime").datetime(2026, 1, 1, 9))
    screentime_store.add_app_seconds("chrome.exe", 5, when=__import__("datetime").datetime(2026, 1, 1, 10))
    assert screentime_store.get_day("2026-01-01")["apps"] == {"chrome.exe": 15}


def test_add_domain_seconds_is_separate_from_apps(isolate_screentime):
    from datetime import datetime
    screentime_store.add_domain_seconds("youtube.com", 30, when=datetime(2026, 1, 1))
    screentime_store.add_app_seconds("chrome.exe", 30, when=datetime(2026, 1, 1))
    day = screentime_store.get_day("2026-01-01")
    assert day["domains"] == {"youtube.com": 30}
    assert day["apps"] == {"chrome.exe": 30}


def test_zero_or_negative_seconds_are_ignored(isolate_screentime):
    screentime_store.add_app_seconds("chrome.exe", 0)
    screentime_store.add_app_seconds("chrome.exe", -5)
    assert screentime_store.get_day(screentime_store._day_key()) == {"apps": {}, "domains": {}}


def test_get_range_sums_across_days(isolate_screentime):
    from datetime import datetime
    screentime_store.add_domain_seconds("github.com", 100, when=datetime(2026, 1, 1))
    screentime_store.add_domain_seconds("github.com", 50, when=datetime(2026, 1, 3))
    screentime_store.add_domain_seconds("github.com", 999, when=datetime(2026, 1, 10))  # outside range

    result = screentime_store.get_range("2026-01-01", "2026-01-07")
    assert result["domains"] == {"github.com": 150}


def test_flush_persists_to_disk_and_survives_reload(isolate_screentime, tmp_path):
    from datetime import datetime
    screentime_store.add_app_seconds("chrome.exe", 42, when=datetime(2026, 1, 1))
    screentime_store.flush()

    # Simulate a fresh process import by resetting the in-memory cache and
    # reloading from the (now isolated) STATE_PATH.
    screentime_store._data = {}
    screentime_store._load()
    assert screentime_store.get_day("2026-01-01")["apps"] == {"chrome.exe": 42}


def test_categorize_domain_known_and_unknown():
    assert screentime_categories.categorize_domain("youtube.com") == "Entertainment"
    assert screentime_categories.categorize_domain("www.github.com") == "Tools"
    assert screentime_categories.categorize_domain("mail.google.com") == "Tools"
    assert screentime_categories.categorize_domain("some-random-startup.io") == "Other"
    assert screentime_categories.categorize_domain(None) == "Other"


def test_categorize_app_known_and_unknown():
    assert screentime_categories.categorize_app("steam.exe") == "Games"
    assert screentime_categories.categorize_app("Code.exe") == "Tools"
    assert screentime_categories.categorize_app("some_unknown_app.exe") == "Other"


@pytest.fixture
def client(isolate_config, isolate_screentime):
    api_server.app.config["TESTING"] = True
    return api_server.app.test_client()


def test_screentime_domain_endpoint_requires_token(client):
    resp = client.post("/screentime/domain", json={"domain": "youtube.com", "seconds": 10})
    assert resp.status_code == 401


def test_screentime_domain_endpoint_records_seconds(client):
    token = config.get_api_token()
    resp = client.post(
        "/screentime/domain",
        json={"domain": "youtube.com", "seconds": 30},
        headers={"X-Carmen-Token": token},
    )
    assert resp.status_code == 200
    day = screentime_store.get_day(screentime_store._day_key())
    assert day["domains"]["youtube.com"] == 30


def test_screentime_domain_endpoint_rejects_bad_body(client):
    token = config.get_api_token()
    resp = client.post(
        "/screentime/domain", json={"domain": "", "seconds": 10}, headers={"X-Carmen-Token": token},
    )
    assert resp.status_code == 400
    resp = client.post(
        "/screentime/domain", json={"domain": "x.com", "seconds": -5}, headers={"X-Carmen-Token": token},
    )
    assert resp.status_code == 400
