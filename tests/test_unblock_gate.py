import pytest

import api_server
import config
import score_store
import session_manager
import unblock_gate


@pytest.fixture
def client(isolate_state):
    unblock_gate._waiting.clear()
    session_manager.start_session(25, "soft", ["x.exe"], ["ok.com"])
    c = api_server.app.test_client()
    c.environ_base["HTTP_X_CARMEN_TOKEN"] = config.get_api_token()
    return c


def _unblock(client, domain, **extra):
    return client.post("/whitelist/domains/add", json={"domain": domain, "reason": "need it", **extra})


def test_a_detailed_unblock_is_stored_and_earns_a_point(client):
    r = _unblock(client, "docs.example.com", plan="read one page", minutes=5)
    assert r.status_code == 200
    addition = r.get_json()["addition"]
    assert addition["plan"] == "read one page" and addition["minutes"] == 5
    events = [e for e in score_store.load()["events"] if e["kind"] == "unblocks"]
    assert len(events) == 1 and events[0]["points"] == unblock_gate.UNBLOCK_BONUS


def test_an_unblock_with_no_plan_still_works_and_is_not_penalised(client):
    r = _unblock(client, "a.example.com")
    assert r.status_code == 200 and "plan" not in r.get_json()["addition"]
    assert [e for e in score_store.load()["events"] if e["kind"] == "unblocks"] == []


def test_past_the_daily_budget_the_first_try_waits_and_a_later_one_goes_through(client, monkeypatch):
    for i in range(unblock_gate.DAILY_FREE):
        assert _unblock(client, f"s{i}.example.com").status_code == 200
    first = _unblock(client, "late.example.com")
    assert first.status_code == 429
    body = first.get_json()
    assert body["error"] == "wait" and body["waitSeconds"] == unblock_gate.WAIT_SECONDS
    assert body["usedToday"] == unblock_gate.DAILY_FREE
    again = _unblock(client, "late.example.com")                 # too soon
    assert again.status_code == 429 and again.get_json()["waitSeconds"] <= unblock_gate.WAIT_SECONDS
    unblock_gate._waiting["domain:late.example.com"] -= unblock_gate.WAIT_SECONDS + 1
    done = _unblock(client, "late.example.com")
    assert done.status_code == 200
    costs = [e["points"] for e in score_store.load()["events"] if e["kind"] == "unblocks"]
    assert costs == [-unblock_gate.OVER_BUDGET_COST]


def test_a_bad_minutes_value_is_dropped_not_rejected(client):
    r = _unblock(client, "b.example.com", plan="x", minutes=9999)
    assert r.status_code == 200 and r.get_json()["addition"]["minutes"] is None


def test_apps_are_gated_the_same_way(client):
    for i in range(unblock_gate.DAILY_FREE):
        r = client.post("/blocklist/apps/remove", json={"process_name": f"a{i}.exe", "reason": "r"})
        assert r.status_code == 200
    r = client.post("/blocklist/apps/remove", json={"process_name": "z.exe", "reason": "r"})
    assert r.status_code == 429
