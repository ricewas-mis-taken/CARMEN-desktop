"""family_sync with a fake backend and a fake desktop: what a pass decides, applies, acknowledges and uploads."""
import pytest

import family_rules as fr
import family_store
import family_sync
from tests_family.fakes import DictSettings, FakeHost, FakeTransport, link, start_cmd

NOW = [1_795_428_000.0]    # 2026-11-23T10:00:00Z (a Monday), in seconds


def make(tmp_path, **settings):
    t, h, s = FakeTransport(), FakeHost(), DictSettings(**settings)
    store = family_store.FamilyStore(str(tmp_path / "family_state.json"))
    sync = family_sync.FamilySync(t, store, h, s, clock=lambda: NOW[0])
    return sync, t, h, s, store


@pytest.fixture(autouse=True)
def _clock():
    NOW[0] = 1_795_428_000.0


def now_ms():
    return int(NOW[0] * 1000)


def test_disabled_does_nothing_at_all(tmp_path):
    sync, t, h, s, store = make(tmp_path, familyEnabled=False)
    assert sync.tick()["skipped"] == "disabled" and t.calls == [] and h.actions == []


def test_start_session_command_is_applied_and_acknowledged(tmp_path):
    sync, t, h, s, store = make(tmp_path)
    t.links = [link()]
    t.cmds = [start_cmd(now_ms())]
    report = sync.tick()
    assert h.actions == [("start", "hard", ["khanacademy.org"], "Dana")]
    assert t.acked == {"c1": True} and report["applied"] == ["c1"]
    assert any("Dana started a session" in n["text"] for n in store.get("notices"))


def test_the_same_command_is_not_applied_twice(tmp_path):
    sync, t, h, s, store = make(tmp_path)
    t.links = [link()]
    t.cmds = [start_cmd(now_ms())]
    sync.tick()
    t.acked.clear()                      # the server "forgot" the ack: the command comes back
    sync.tick()
    assert len([a for a in h.actions if a[0] == "start"]) == 1 and t.acked == {"c1": True}


def test_malformed_command_is_declined(tmp_path):
    sync, t, h, s, store = make(tmp_path)
    t.links = [link()]
    c = start_cmd(now_ms(), lock="medium")
    t.cmds = [c]
    sync.tick()
    assert h.actions == [] and t.acked == {"c1": False}


def test_ack_lost_while_offline_is_retried(tmp_path):
    sync, t, h, s, store = make(tmp_path)
    t.links = [link()]
    t.cmds = [start_cmd(now_ms())]
    orig = t.ack_command
    t.ack_command = lambda *a: (_ for _ in ()).throw(__import__("family_transport").FamilyError("offline", "x", 0, offline=True))
    sync.tick()
    assert t.acked == {} and store.get("pendingAcks") == [{"id": "c1", "applied": True}]
    t.ack_command = orig
    sync.tick()
    assert t.acked == {"c1": True} and store.get("pendingAcks") == []


def test_offline_keeps_cached_rules_and_running_session(tmp_path):
    sync, t, h, s, store = make(tmp_path)
    t.links = [link()]
    t.rules_doc = {"allow": ["a.com"], "deny": ["b.com"], "byGuide": []}
    t.cmds = [start_cmd(now_ms())]
    sync.tick()
    before = store.get("rules")["version"]
    t.down = True
    report = sync.tick()
    assert report["offline"] and not report["reachable"]
    assert store.get("rules")["version"] == before and h.session["isActive"] and sync.effective_rules()["status"] in ("ok", "stale")
    assert sync.effective_rules()["deny"] == ["b.com"]


def test_malformed_rules_reply_keeps_the_cache(tmp_path):
    sync, t, h, s, store = make(tmp_path)
    t.links = [link()]
    t.rules_doc = {"allow": ["a.com"], "deny": ["b.com"], "byGuide": []}
    sync.tick()
    t.raw["rules"] = {"allow": None}
    report = sync.tick()
    assert "rules:malformed" in report["steps"] and sync.effective_rules()["deny"] == ["b.com"]


def test_malformed_schedule_reply_keeps_the_hard_block(tmp_path):
    sync, t, h, s, store = make(tmp_path)
    t.links = [link()]
    t.blocks_doc = {"blocks": [{"id": "b1", "title": "HW", "dow": 1, "start": "09:00", "end": "11:00", "tzOffsetMin": 0, "lockMode": "hard", "allowlist": ["a.com"], "guideName": "Dana"}]}
    sync.tick()
    assert h.actions[0][:2] == ("start", "hard")
    t.raw["schedule"] = {"blocks": [{"id": "b1"}]}
    NOW[0] += 60
    sync.tick(force=True)
    assert h.session["isActive"] and h.session["lockMode"] == "hard"


def test_block_session_starts_and_ends_with_the_block(tmp_path):
    sync, t, h, s, store = make(tmp_path)
    t.links = [link()]
    t.blocks_doc = {"blocks": [{"id": "b1", "title": "HW", "dow": 1, "start": "09:00", "end": "10:30", "tzOffsetMin": 0, "lockMode": "soft", "allowlist": ["a.com"], "guideName": "Dana", "guideId": "u_dana"}]}
    sync.tick()
    assert h.session["isActive"] and h.session["guide"]["kind"] == "block"
    NOW[0] += 31 * 60
    sync.tick()
    assert not h.session["isActive"] and h.actions[-1][0] == "end"


def test_student_ending_a_block_early_skips_it_and_tells_the_backend(tmp_path):
    sync, t, h, s, store = make(tmp_path)
    t.links = [link()]
    t.blocks_doc = {"blocks": [{"id": "b1", "title": "HW", "dow": 1, "start": "09:00", "end": "11:00", "tzOffsetMin": 0, "lockMode": "soft", "allowlist": ["a.com"], "guideName": "Dana", "guideId": "u_dana"}]}
    sync.tick()
    h.session = {**h.session, "isActive": False, "guide": None, "sessionId": None}      # the student ends it
    NOW[0] += 120
    sync.tick()
    assert t.skips == [("b1", "2026-11-23")] and not h.session["isActive"]
    NOW[0] += 120
    sync.tick()
    assert not h.session["isActive"]                              # not started again today


def test_revoking_a_link_ends_that_guides_session_and_rules(tmp_path):
    sync, t, h, s, store = make(tmp_path)
    t.links = [link()]
    t.rules_doc = {"allow": ["a.com"], "deny": ["b.com"], "byGuide": [{"guideId": "u_dana", "name": "Dana", "allow": ["a.com"], "deny": ["b.com"]}]}
    t.cmds = [start_cmd(now_ms())]
    sync.tick()
    assert h.session["isActive"]
    ok, _ = sync.revoke_link("l_u_dana")
    assert ok and not h.session["isActive"] and sync.effective_rules()["deny"] == []
    assert any("link ended" in n["text"] for n in store.get("notices"))


def test_session_lost_signs_out_and_ends_guide_session(tmp_path):
    sync, t, h, s, store = make(tmp_path)
    t.links = [link()]
    t.cmds = [start_cmd(now_ms())]
    sync.tick()
    t.lost = True
    assert sync.tick()["skipped"] == "session_lost"
    assert s.d["familyEnabled"] is False and not h.session["isActive"]
    assert any(n["kind"] == "security" for n in store.get("notices"))


def test_sign_in_errors_are_friendly_and_success_enables(tmp_path):
    sync, t, h, s, store = make(tmp_path, familyEnabled=False)
    assert sync.sign_in("a@b.c", "bad") == (False, "Incorrect email or password.")
    assert s.d["familyEnabled"] is False
    assert sync.sign_in("a@b.c", "pw") == (True, None) and s.d["familyEnabled"] is True


def test_sign_out_disables_ends_session_and_forgets_rules(tmp_path):
    sync, t, h, s, store = make(tmp_path)
    t.links = [link()]
    t.rules_doc = {"allow": ["a.com"], "deny": [], "byGuide": []}
    t.cmds = [start_cmd(now_ms())]
    sync.tick()
    sync.sign_out()
    assert not h.session["isActive"] and s.d["familyEnabled"] is False and store.get("rules") is None


def test_uploads_only_what_a_link_allows_and_nothing_without_one(tmp_path):
    sync, t, h, s, store = make(tmp_path)
    h.domains = {"khanacademy.org": 120, "localhost": 50}
    sync.tick()                                                   # no links
    h.domains["khanacademy.org"] = 300
    sync.tick()
    assert t.screen == [] and t.statuses == [] and t.summaries == [] and t.hbs
    t.links = [link(scopes=["status", "screentime", "score"])]
    h.domains["khanacademy.org"] = 400
    sync.tick()
    assert [(r["hostname"], r["seconds"]) for r in t.screen] == [("khanacademy.org", 100)]   # only time after consent, hostnames only
    assert t.statuses and t.statuses[-1] == {"state": "idle"}


def test_finished_sessions_upload_after_consent_with_detail_redaction(tmp_path):
    sync, t, h, s, store = make(tmp_path)
    t.links = [link(scopes=["sessions", "status", "score"], detail="summary")]
    sync.tick()                                                   # sets the watermark at "now"
    h.history.append({"startTime": "2026-11-23T10:00:00", "endTime": "2026-11-23T10:30:00", "endType": "nuclear", "reason": "private reason",
                      "lockMode": "hard", "violationCount": 2, "violationLog": [{"kind": "domain", "url": "https://tiktok.com/x?q=1", "timestamp": "2026-11-23T10:05:00"}],
                      "sessionId": "sid1", "eventTitle": "Secret task"})
    sync.tick()
    assert len(t.summaries) == 1
    sm = t.summaries[0]
    assert sm["id"] == "sid1" and sm["endType"] == "nuclear" and sm["taskName"] == "" and "reason" not in sm and "violationHosts" not in sm and sm["source"] == "desktop"


def test_detail_link_gets_reasons_and_hostnames_only(tmp_path):
    sync, t, h, s, store = make(tmp_path)
    t.links = [link(scopes=["sessions", "tasks", "score"], detail="detail")]
    sync.tick()
    h.history.append({"startTime": "2026-11-23T10:00:00", "endTime": "2026-11-23T10:30:00", "endType": "nuclear", "reason": "too tired", "lockMode": "soft",
                      "violationCount": 1, "violationLog": [{"kind": "domain", "url": "https://tiktok.com/x?q=secret", "timestamp": "2026-11-23T10:05:00"}], "sessionId": "sid2", "eventTitle": "Essay"})
    sync.tick()
    sm = t.summaries[0]
    assert sm["reason"] == "too tired" and sm["violationHosts"] == ["tiktok.com"] and sm["taskName"] == "Essay"
    assert "secret" not in str(sm)


def test_history_from_before_consent_is_never_sent(tmp_path):
    sync, t, h, s, store = make(tmp_path)
    h.history.append({"startTime": "2026-11-22T10:00:00", "endTime": "2026-11-22T10:30:00", "endType": "natural", "lockMode": "soft", "sessionId": "old"})
    t.links = [link(scopes=["sessions", "score"])]
    sync.tick()
    sync.tick()
    assert t.summaries == []


def test_links_changing_are_announced_not_silent(tmp_path):
    sync, t, h, s, store = make(tmp_path)
    sync.tick()
    t.links = [link()]
    sync.tick()
    assert any("You are now linked to Dana" in n["text"] for n in store.get("notices")) and h.notices


def test_rules_change_announced_once(tmp_path):
    sync, t, h, s, store = make(tmp_path)
    t.links = [link()]
    sync.tick()
    t.rules_doc = {"allow": ["a.com"], "deny": [], "byGuide": [{"guideId": "u_dana", "name": "Dana", "allow": ["a.com"], "deny": []}]}
    sync.tick()
    sync.tick()
    assert len([n for n in store.get("notices") if "changed your rules" in n["text"]]) == 1


def test_effective_rules_view_when_disabled_is_empty(tmp_path):
    sync, t, h, s, store = make(tmp_path, familyEnabled=False)
    v = sync.effective_rules()
    assert v["enabled"] is False and v["allow"] == [] and v["status"] == "off"


def test_snapshot_lists_what_each_guide_sees(tmp_path):
    sync, t, h, s, store = make(tmp_path)
    t.links = [link(canSetRules=True)]
    sync.tick()
    snap = sync.snapshot()
    assert snap["links"][0]["active"] and any("Carmen Score" in x for x in snap["links"][0]["sees"]) and snap["links"][0]["can"]


def test_rules_state_in_heartbeat(tmp_path):
    sync, t, h, s, store = make(tmp_path)
    t.links = [link()]
    sync.tick()
    assert t.hbs[-1]["kind"] == "desktop" and t.hbs[-1]["deviceId"] == "dev-1"
