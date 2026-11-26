"""The Supabase transport against the REAL project, with throwaway users (marker `supabase`).

Skipped unless CARMENLINKED_DIR names the carmenlinked checkout (the folder holding client/ and supabase/). Accounts are
created by SQL as ct-<run>-<tag>@carmenlinked.test through tests_family/tools/ct_bridge.mjs (never GoTrue sign-up), the
guide is played by the JS adapter, and every account is removed at the end (and by the harness's exit hooks if this
process is killed). Run it under the project lock:   flock /tmp/ct-supabase.lock python -m pytest -m supabase tests_family
"""
import json
import os
import subprocess
import time

import pytest

import family_store
import family_supabase as fs
import family_sync
from family_rest import MemoryTokenStore
from family_transport import FamilyError
from tests_family.fakes import DictSettings, FakeHost

CL_DIR = os.environ.get("CARMENLINKED_DIR", "")
BRIDGE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tools", "ct_bridge.mjs")
pytestmark = [pytest.mark.supabase,
              pytest.mark.skipif(not CL_DIR or not os.path.isdir(CL_DIR), reason="set CARMENLINKED_DIR to run against the real Supabase project")]


class Bridge:
    def __init__(self):
        env = dict(os.environ, NODE_USE_ENV_PROXY="1")
        self.p = subprocess.Popen(["node", "--no-warnings", BRIDGE, CL_DIR], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, env=env)

    def call(self, **msg):
        self.p.stdin.write(json.dumps(msg) + "\n")
        self.p.stdin.flush()
        line = self.p.stdout.readline()
        assert line, "bridge ended"
        answer = json.loads(line)
        assert "error" not in answer, answer["error"]
        return answer["ok"]

    def guide(self, method, path, body=None):
        r = self.call(op="guide", method=method, path=path, body=body)
        return r["status"], r["body"]

    def close(self):
        try:
            self.p.stdin.write(json.dumps({"op": "quit"}) + "\n")
            self.p.stdin.flush()
            self.p.wait(180)
        except Exception:
            self.p.kill()


class World:
    pass


@pytest.fixture(scope="module")
def world():
    w = World()
    w.b = Bridge()
    try:
        info = w.b.call(op="init")
        w.sid, w.email, w.password, w.gid = info["student"]["id"], info["student"]["email"], info["student"]["password"], info["guide"]["id"]
        w.store = MemoryTokenStore()
        w.t = fs.SupabaseFamilyTransport(fs.DEFAULT_URL, fs.DEFAULT_KEY, w.store)
        w.user = w.t.login(w.email, w.password)
        yield w
    finally:
        w.b.close()


def test_01_login_identity_and_wrong_password(world):
    assert world.user["id"] == world.sid and world.user["email"] == world.email and world.user["displayName"] == "CT Kid"
    assert world.store.get()
    bad = fs.SupabaseFamilyTransport(fs.DEFAULT_URL, fs.DEFAULT_KEY, MemoryTokenStore())
    with pytest.raises(FamilyError) as e:
        bad.login(world.email, "definitely-wrong")
    assert e.value.code == "invalid_credentials" and not e.value.offline


def test_02_refresh_rotates_and_a_saved_token_resumes(world):
    before = world.store.get()
    world.t.refresh(world.t._access)
    assert world.store.get() != before and world.t.refresh_count == 1
    again = fs.SupabaseFamilyTransport(fs.DEFAULT_URL, fs.DEFAULT_KEY, MemoryTokenStore(world.store.get()))
    assert again.is_signed_in()
    again.refresh(None)                                   # resumes from the saved token alone
    assert again.rpc("my_profile")["displayName"] == "CT Kid"


def test_03_link_by_guide_code_then_me(world):
    # the clone above rotated the shared session; make the main transport sign in again to stay independent of that
    world.t.login(world.email, world.password)
    status, inv = world.b.guide("POST", "/links/invite", {"side": "guide", "relation": "parent", "scopes": ["status", "sessions", "screentime"],
                                                          "detail": "detail", "canAssign": True, "canSetRules": True})
    assert status == 201, inv
    r = world.t.redeem_code(inv["code"])
    world.link = r["link"]
    assert world.link["status"] == "active" and world.link["canSetRules"] is True
    me = world.t.me()
    assert me["user"]["id"] == world.sid and [l["id"] for l in me["asStudent"]["links"]] == [world.link["id"]]


def test_04_rules_from_the_guide(world):
    status, body = world.b.guide("POST", f"/students/{world.sid}/rules", {"allow": ["khanacademy.org"], "deny": ["tiktok.com"], "note": "Homework"})
    assert status == 201, body
    rules = world.t.rules()
    assert rules["allow"] == ["khanacademy.org"] and rules["deny"] == ["tiktok.com"]
    assert rules["byGuide"][0]["guideId"] == world.gid and rules["byGuide"][0]["note"] == "Homework"


def test_05_command_received_and_acknowledged(world):
    status, cmd = world.b.guide("POST", f"/students/{world.sid}/commands", {"type": "message", "payload": {"text": "Hello from the test"}})
    assert status == 201, cmd
    cmd = cmd["command"]
    got = [c for c in world.t.commands() if c["id"] == cmd["id"]]
    assert got and got[0]["type"] == "message" and got[0]["authorId"] == world.gid
    world.t.ack_command(cmd["id"], True)
    assert not [c for c in world.t.commands() if c["id"] == cmd["id"]]
    status, mine = world.b.guide("GET", f"/students/{world.sid}/commands")
    assert status == 200 and [c["status"] for c in mine if c["id"] == cmd["id"]] == ["applied"], mine


def test_06_heartbeat_registers_the_device(world):
    world.dev = "ct-dev-" + world.sid[:8]
    r = world.t.heartbeat({"deviceId": world.dev, "kind": "desktop", "version": "test", "enforcing": True, "rules": "ok", "sessionActive": False})
    assert r["ok"] is True
    devices = world.t.me()["asStudent"]["devices"]
    assert [d["deviceId"] for d in devices] == [world.dev] and devices[0]["kind"] == "desktop"
    rows = world.b.call(op="sql", q=f"select device_id, enforcing from devices_registry where user_id = '{world.sid}'")
    assert rows == [{"device_id": world.dev, "enforcing": True}]


def test_07_status_summary_screentime_reach_the_guide(world):
    now = time.time()
    iso = lambda s: time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(s))
    world.t.post_status({"state": "live", "lockMode": "hard", "taskName": "Essay", "startedAt": iso(now - 600), "endsAt": iso(now + 600)})
    world.t.post_summaries([{"id": "ct-s1", "startedAt": iso(now - 3600), "endedAt": iso(now - 1800), "plannedMin": 30, "focusedMin": 29, "taskName": "Essay",
                             "lockMode": "hard", "endType": "completed", "violations": 0, "pauses": {"free": 0, "checking": 0, "minutes": 0}, "source": "desktop"}])
    world.t.post_screentime([{"day": time.strftime("%Y-%m-%d", time.gmtime(now)), "hostname": "khanacademy.org", "seconds": 120, "at": iso(now)}])
    status, st = world.b.guide("GET", f"/students/{world.sid}/status")
    assert status == 200 and st["state"] == "live" and st["lockMode"] == "hard", st
    status, sums = world.b.guide("GET", f"/students/{world.sid}/summaries")
    assert status == 200 and [s["id"] for s in sums] == ["ct-s1"] and sums[0]["endType"] == "completed", sums
    status, sc = world.b.guide("GET", f"/students/{world.sid}/screentime")
    assert status == 200 and any(r.get("category") == "Learning" for r in sc), sc
    rows = world.b.call(op="sql", q=f"select table_name, updated_at from sync_records where user_id = '{world.sid}' order by updated_at")
    assert sorted(r["table_name"] for r in rows) == ["live_session", "session_history"]
    stamps = [r["updated_at"] for r in rows]
    assert stamps == sorted(set(stamps))                  # strictly increasing, aware timestamps
    world.t.post_status({"state": "idle"})
    assert world.b.guide("GET", f"/students/{world.sid}/status")[1]["state"] != "live"


def test_08_transparency_lists_and_notifications(world):
    shared = world.t.shared_log(50)
    assert {"heartbeat", "status", "summaries", "screentime"} <= {s["kind"] for s in shared}
    audit = world.t.audit(50)
    assert audit and all({"at", "actorName", "action", "detail"} <= set(a) for a in audit)
    n = world.t.notifications(20)
    assert isinstance(n["notifications"], list) and "unread" in n
    world.t.mark_notifications_read()
    assert world.t.notifications(20)["unread"] == 0


def test_09_schedule_classes_and_conflicts(world):
    status, blk = world.b.guide("POST", f"/students/{world.sid}/schedule",
                                {"title": "Study", "dow": 2, "start": "16:00", "end": "17:00", "tzOffsetMin": 0, "allowlist": ["khanacademy.org"], "lockMode": "soft"})
    assert status == 201, blk
    blk = blk.get("block", blk)
    blocks = world.t.schedule()["blocks"]
    assert [b["title"] for b in blocks] == ["Study"] and blocks[0]["guideId"] == world.gid
    assert world.t.schedule_conflicts()["conflicts"] == [] and world.t.classes() == []
    world.t.post_schedule_skip(blk["id"], "sick", None)


def test_10_unreachable_keeps_cached_rules_and_revoke_stops_everything(world, tmp_path):
    host, settings = FakeHost(), DictSettings(familyBackend="supabase")
    sync = family_sync.FamilySync(world.t, family_store.FamilyStore(str(tmp_path / "f.json")), host, settings)
    sync.tick(force=True)
    assert sync.effective_rules()["deny"] == ["tiktok.com"]
    real = world.t._base
    world.t._base = "http://127.0.0.1:1"                  # the project cannot be reached
    try:
        sync.tick(force=True)
        assert sync.effective_rules()["deny"] == ["tiktok.com"] and not any(a[0] == "end" for a in host.actions)
    finally:
        world.t._base = real
    sync.revoke_link(world.link["id"])                    # the student leaves
    assert world.t.rules()["byGuide"] == [] and world.t.commands() == []
    status, _ = world.b.guide("GET", f"/students/{world.sid}/status")
    assert status in (403, 404)
    assert sync.effective_rules()["deny"] == []
    assert [l["status"] for l in world.t.me()["asStudent"]["links"]] == ["revoked"]


def test_11_logout_ends_the_session_so_the_refresh_token_is_refused(world):
    refresh = world.store.get()
    world.t.logout(everywhere=True)
    assert not world.t.is_signed_in() and world.store.get() is None
    again = fs.SupabaseFamilyTransport(fs.DEFAULT_URL, fs.DEFAULT_KEY, MemoryTokenStore(refresh))
    with pytest.raises(FamilyError) as e:                 # (an access token already issued stays valid until it expires, up to 1 h: JWTs are stateless)
        again.ensure_fresh()
    assert e.value.code == "session_lost" and again.session_lost
