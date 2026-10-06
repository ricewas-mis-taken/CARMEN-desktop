"""Unbounded string inputs on token-gated routes bloated session_state.json /
config.json / screentime.json (rewritten on every tick), same class as the
_MAX_LIST_ENTRIES cap. These routes now reject oversized values with 400."""
import pytest

import api_server
import config
import screentime_store


@pytest.fixture
def client(isolate_state, tmp_path, monkeypatch):
    monkeypatch.setattr(screentime_store, "STATE_PATH", str(tmp_path / "screentime.json"))
    monkeypatch.setattr(screentime_store, "_data", {})
    api_server.app.config["TESTING"] = True
    c = api_server.app.test_client()
    h = {"X-Carmen-Token": config.get_api_token()}
    c.post("/session/start", headers=h, json={
        "duration_minutes": 25, "lock_mode": "soft",
        "process_blocklist": ["a.exe"], "domain_whitelist": ["a.com"]})
    return c, h


def test_violation_rejects_huge_url(client):
    c, h = client
    assert c.post("/violation", headers=h, json={"url": "x" * 100_000}).status_code == 400


def test_whitelist_add_rejects_huge_domain_or_reason(client):
    c, h = client
    assert c.post("/whitelist/domains/add", headers=h, json={"domain": "d" * 100_000, "reason": "r"}).status_code == 400
    assert c.post("/whitelist/domains/add", headers=h, json={"domain": "d.com", "reason": "r" * 100_000}).status_code == 400


def test_blocklist_remove_rejects_huge_reason(client):
    c, h = client
    assert c.post("/blocklist/apps/remove", headers=h, json={"process_name": "a.exe", "reason": "r" * 100_000}).status_code == 400


def test_focus_rules_rejects_oversized_list_and_entries(client):
    c, h = client
    assert c.post("/api/focus/rules", headers=h, json={"domainWhitelist": ["d%d.com" % i for i in range(5000)]}).status_code == 400
    assert c.post("/api/focus/rules", headers=h, json={"domainWhitelist": ["d" * 100_000]}).status_code == 400


def test_screentime_domain_rejects_huge_domain(client):
    c, h = client
    assert c.post("/screentime/domain", headers=h, json={"domain": "z" * 100_000, "seconds": 1}).status_code == 400


def test_normal_values_still_accepted(client):
    c, h = client
    assert c.post("/violation", headers=h, json={"url": "https://example.com/x"}).status_code == 200
    assert c.post("/whitelist/domains/add", headers=h, json={"domain": "b.com", "reason": "need it"}).status_code == 200
    assert c.post("/api/focus/rules", headers=h, json={"domainWhitelist": ["a.com"]}).status_code == 200
    assert c.post("/screentime/domain", headers=h, json={"domain": "a.com", "seconds": 3}).status_code == 200
