"""The family features' hooks into the desktop: what changes when a guide set a session, and proof that nothing
changes for anyone else (the features are off by default)."""
import json

import pytest

import api_server
import config
import family_host
import family_sync
import session_history
import session_manager
from tests_family.fakes import DictSettings, FakeTransport, link


def client():
    api_server.app.config["TESTING"] = True
    return api_server.app.test_client()


GUIDE = {"kind": "command", "id": "u_okafor", "name": "Ms. Okafor", "commandId": "c1", "plannedMin": 30, "baseAllow": ["a.com"]}


# ---- regression: disabled / ordinary sessions are exactly as before -------------------------------

def test_family_is_off_by_default():
    assert config.DEFAULT_CONFIG["familyEnabled"] is False
    assert "familyEnabled" not in config.SHARED_SETTINGS and "familyApiBase" not in config.SHARED_SETTINGS


def test_ordinary_session_has_no_guide_and_history_has_no_new_key(isolate_state):
    session_manager.start_session(25, "hard", ["x.exe"], ["a.com"])
    assert session_manager.get_status()["guide"] is None
    session_manager.end_session()
    entry = session_history.load_all()[-1]
    assert "guide" not in entry
    assert set(entry) == {"startTime", "endTime", "endType", "reason", "lockMode", "processBlocklist", "domainWhitelist", "violationCount",
                          "violationLog", "domainWhitelistAdditions", "processBlocklistExceptions", "source", "eventId", "eventTitle",
                          "reviewProblemName", "reviewSubjectName", "pomodoro", "sessionId", "isBurnout"}


def test_ordinary_session_can_still_add_a_site(isolate_state):
    session_manager.start_session(25, "hard", [], ["a.com"])
    domains, addition = session_manager.add_domain_to_whitelist("b.com", "need it")
    assert addition is not None and "b.com" in domains


def test_family_rules_route_is_the_empty_off_shape_by_default(isolate_state):
    data = client().get("/family/rules").get_json()
    assert data["enabled"] is False and data["allow"] == [] and data["deny"] == [] and data["status"] == "off" and data["studyBlock"] is None


def test_existing_status_fields_unchanged_plus_guide_none(isolate_state):
    data = client().get("/status").get_json()
    assert data["isActive"] is False and data["guide"] is None and "lockMode" in data and "domainWhitelist" in data


def test_whitelist_add_route_unchanged_for_ordinary_sessions(isolate_state):
    session_manager.start_session(25, "hard", [], [])
    token = config.get_api_token()
    r = client().post("/whitelist/domains/add", json={"domain": "b.com", "reason": "need"}, headers={"X-Carmen-Token": token})
    assert r.status_code != 403


# ---- a session a guide set ------------------------------------------------------------------------

def test_guide_session_is_labelled_filed_under_the_guide_and_refuses_unblocks(isolate_state):
    session_manager.start_session(30, "hard", [], ["a.com"], source="guide", event_title="Session from Ms. Okafor", guide=GUIDE)
    status = session_manager.get_status()
    assert status["guide"]["name"] == "Ms. Okafor" and status["source"] == "guide"
    assert session_manager.add_domain_to_whitelist("b.com", "please") == (None, None)
    token = config.get_api_token()
    r = client().post("/whitelist/domains/add", json={"domain": "b.com", "reason": "need"}, headers={"X-Carmen-Token": token})
    assert r.status_code == 403 and "Ms. Okafor" in r.get_json()["error"]
    session_manager.end_session(end_type="manual", reason="Ended by me")
    entry = session_history.load_all()[-1]
    assert entry["guide"]["name"] == "Ms. Okafor" and entry["source"] == "guide"
    assert session_manager.get_status()["guide"] is None


def test_guide_record_is_cleaned_and_survives_a_restart(isolate_state):
    session_manager.start_session(30, "soft", [], [], source="guide", guide={**GUIDE, "name": "  " + "N" * 200, "extra": object()})
    assert len(session_manager.get_status()["guide"]["name"]) == 60 and "extra" not in session_manager.get_status()["guide"]
    session_manager.start_session(30, "soft", [], [], guide="nonsense")
    assert session_manager.get_status()["guide"] is None
    session_manager.start_session(30, "soft", [], [], guide={"name": ""})
    assert session_manager.get_status()["guide"] is None


@pytest.mark.parametrize("junk", [None, "x", 5, [], {"name": 5}, {"name": ""}, {"name": "ok", "blockIds": "x"}])
def test_remote_session_with_a_junk_guide_is_cleaned(junk):
    clean = session_manager._clean_remote_session({"isActive": True, "guide": junk})
    assert clean.get("guide") is None or isinstance(clean["guide"], dict)


def test_update_guide_only_touches_a_guide_session(isolate_state):
    session_manager.start_session(30, "soft", [], [])
    session_manager.update_guide(GUIDE)
    assert session_manager.get_status()["guide"] is None


def test_host_starts_the_session_with_the_guides_sites_and_the_students_own_app_list(isolate_state):
    config.update_config(lambda c: c.update(processBlocklist=["discord.exe"]))
    from family_rules import decide_command, sanitize_commands
    from tests_family.fakes import start_cmd
    import time
    cmd = sanitize_commands([start_cmd(int(time.time() * 1000), allow=("khanacademy.org",))])[0][0]
    step = decide_command(cmd, int(time.time() * 1000), {"session": {"isActive": False}})
    sid = family_host.DesktopHost().start_session(step)
    s = session_manager.get_status()
    assert sid == s["sessionId"] and s["lockMode"] == "hard" and s["domainWhitelist"] == ["khanacademy.org"]
    assert s["processBlocklist"] == ["discord.exe"] and s["guide"]["name"] == "Dana" and 28 < s["secondsRemaining"] / 60 <= 30


def test_host_update_keeps_the_session_running(isolate_state):
    session_manager.start_session(30, "soft", ["x.exe"], ["a.com"], source="guide", guide=GUIDE)
    family_host.DesktopHost().update_session({"allow": ["a.com", "b.com"], "lockMode": "hard", "guide": GUIDE})
    s = session_manager.get_status()
    assert s["isActive"] and s["lockMode"] == "hard" and s["domainWhitelist"] == ["a.com", "b.com"] and s["processBlocklist"] == ["x.exe"]


# ---- the extension's read-only route when on -------------------------------------------------------

def test_family_rules_route_serves_the_cached_rules_when_on(isolate_state, tmp_path, monkeypatch):
    import family_store
    store = family_store.FamilyStore(str(tmp_path / "f.json"))
    t = FakeTransport()
    t.links = [link()]
    t.rules_doc = {"allow": ["a.com"], "deny": ["b.com"], "byGuide": [{"guideId": "u_dana", "name": "Dana", "allow": ["a.com"], "deny": ["b.com"], "note": "n"}]}
    sync = family_sync.FamilySync(t, store, family_host.DesktopHost(), DictSettings())
    config.update_config(lambda c: c.update(familyEnabled=True))
    family_sync.set_instance(sync)
    try:
        sync.tick()
        data = client().get("/family/rules").get_json()
        assert data["enabled"] and data["deny"] == ["b.com"] and data["byGuide"][0]["name"] == "Dana" and data["status"] == "ok"
        t.down = True
        sync.tick()
        assert client().get("/family/rules").get_json()["deny"] == ["b.com"]       # offline: the cached copy
    finally:
        family_sync.set_instance(None)


def test_family_rules_route_answers_503_not_empty_on_internal_error(isolate_state, monkeypatch):
    class Boom:
        def effective_rules(self): raise RuntimeError("x")
    config.update_config(lambda c: c.update(familyEnabled=True))
    family_sync.set_instance(Boom())
    try:
        assert client().get("/family/rules").status_code == 503
    finally:
        family_sync.set_instance(None)
