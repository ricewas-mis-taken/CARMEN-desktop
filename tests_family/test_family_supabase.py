"""The Supabase transport against a fake Supabase (GoTrue + PostgREST) on a local port: no network, no real project."""
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlsplit

import pytest

import family_supabase as fs
from family_rest import MemoryTokenStore
from family_transport import FamilyError

UID = "11111111-1111-1111-1111-111111111111"
CMD = "22222222-2222-2222-2222-222222222222"


def _jwt(sub=UID, email="kid@x.test"):
    import base64
    enc = lambda o: base64.urlsafe_b64encode(json.dumps(o).encode()).decode().rstrip("=")
    return f"{enc({'alg': 'none'})}.{enc({'sub': sub, 'email': email})}.sig"


class State:
    def __init__(self):
        self.log = []                 # (method, path, apikey, authorization, body)
        self.good_access = {"A1"}
        self.refresh_tokens = {"R1": ("A2", "R2")}
        self.refresh_error = None
        self.rpc_answers = {}
        self.rpc_errors = {}          # name -> (status, body)
        self.profile = {"displayName": "Kid", "birthYear": 2011}
        self.login_error = None
        self.devices = []
        self.refresh_delay = 0.0


@pytest.fixture()
def fake():
    st = State()

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a): pass

        def _send(self, code, obj, headers=None):
            body = json.dumps(obj).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            for k, v in (headers or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(body)

        def _body(self):
            n = int(self.headers.get("Content-Length") or 0)
            return json.loads(self.rfile.read(n) or b"null")

        def _record(self, body):
            st.log.append((self.command, self.path, self.headers.get("apikey"), self.headers.get("Authorization"), body))

        def do_POST(self):
            body = self._body()
            self._record(body)
            parts = urlsplit(self.path)
            if parts.path == "/auth/v1/token":
                if "password" in parts.query:
                    if st.login_error:
                        return self._send(*st.login_error)
                    return self._send(200, {"access_token": "A1", "refresh_token": "R1", "expires_in": 3600, "expires_at": int(time.time()) + 3600,
                                            "user": {"id": UID, "email": "kid@x.test", "user_metadata": {"display_name": "Kid", "birth_year": 2011}}})
                time.sleep(st.refresh_delay)
                if st.refresh_error:
                    return self._send(*st.refresh_error)
                new = st.refresh_tokens.get(body.get("refresh_token"))
                if not new:
                    return self._send(400, {"error_code": "refresh_token_already_used", "msg": "Invalid Refresh Token: Already Used"})
                st.good_access.add(new[0])
                return self._send(200, {"access_token": new[0], "refresh_token": new[1], "expires_in": 3600, "expires_at": int(time.time()) + 3600,
                                        "user": {"id": UID, "email": "kid@x.test"}})
            if parts.path == "/auth/v1/logout":
                return self._send(204, {})
            if parts.path.startswith("/rest/v1/rpc/"):
                name = parts.path.rsplit("/", 1)[1]
                if self.headers.get("Authorization", "")[7:] not in st.good_access:
                    return self._send(401, {"code": "PGRST301", "message": "JWT expired"})
                if name in st.rpc_errors:
                    return self._send(*st.rpc_errors[name])
                if name == "my_profile":
                    return self._send(200, st.profile)
                return self._send(200, st.rpc_answers.get(name, {"ok": True}), {"Date": "Thu, 26 Nov 2026 10:00:00 GMT"})
            self._send(404, {"code": "PGRST125", "message": "nope"})

        def do_GET(self):
            self._record(None)
            if self.headers.get("Authorization", "")[7:] not in st.good_access:
                return self._send(401, {"code": "PGRST301", "message": "JWT expired"})
            self._send(200, st.devices)

    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    st.url = f"http://127.0.0.1:{srv.server_port}"
    yield st
    srv.shutdown()


def make(fake, token=None):
    store = MemoryTokenStore(token)
    return fs.SupabaseFamilyTransport(fake.url, "sb_publishable_test", store), store


def test_login_uses_password_grant_with_apikey_and_saves_refresh_token(fake):
    t, store = make(fake)
    user = t.login(" Kid@X.test ", "pw")
    assert user == {"id": UID, "email": "kid@x.test", "displayName": "Kid"}
    method, path, key, auth, body = fake.log[0]
    assert path == "/auth/v1/token?grant_type=password" and key == "sb_publishable_test" and auth is None
    assert body == {"email": "kid@x.test", "password": "pw"}
    assert store.get() == "R1" and t.is_signed_in()
    assert not any("signup" in e[1] for e in fake.log)


@pytest.mark.parametrize("answer,code,offline", [
    ((400, {"error_code": "invalid_credentials", "msg": "x"}), "invalid_credentials", False),
    ((400, {"error_code": "email_not_confirmed"}), "email_not_confirmed", False),
    ((429, {"error_code": "over_request_rate_limit"}), "rate_limited", False),
    ((503, {"msg": "down"}), "upstream_error", True),
])
def test_login_errors_are_mapped(fake, answer, code, offline):
    fake.login_error = answer
    t, store = make(fake)
    with pytest.raises(FamilyError) as e:
        t.login("a@b.co", "pw")
    assert e.value.code == code and e.value.offline is offline and store.get() is None


def test_unreachable_is_offline_not_invalid():
    t = fs.SupabaseFamilyTransport("http://127.0.0.1:1", "k", MemoryTokenStore())
    with pytest.raises(FamilyError) as e:
        t.login("a@b.co", "pw")
    assert e.value.offline and e.value.code == "offline"


def test_https_is_required_off_loopback():
    with pytest.raises(FamilyError) as e:
        fs.SupabaseFamilyTransport().configure("http://example.com")
    assert e.value.code == "insecure_base"
    assert fs.SupabaseFamilyTransport().base == fs.DEFAULT_URL


def test_expired_token_refreshes_once_and_retries(fake):
    t, store = make(fake)
    t.login("a@b.co", "pw")
    fake.good_access = set()
    fake.good_access.add("A2-will-exist")      # A1 now rejected
    fake.rpc_answers["my_rules"] = {"allow": [], "deny": [], "byGuide": []}
    assert t.rules() == {"allow": [], "deny": [], "byGuide": []}
    assert t.refresh_count == 1 and store.get() == "R2"
    refreshes = [e for e in fake.log if "refresh_token" in e[1]]
    assert len(refreshes) == 1 and refreshes[0][4] == {"refresh_token": "R1"}


def test_concurrent_expired_calls_share_one_refresh(fake):
    t, store = make(fake)
    t.login("a@b.co", "pw")
    fake.good_access = set()
    fake.refresh_delay = 0.1
    out = []
    ths = [threading.Thread(target=lambda: out.append(t.rules())) for _ in range(6)]
    [x.start() for x in ths]; [x.join() for x in ths]
    assert len(out) == 6 and t.refresh_count == 1


def test_refused_refresh_is_session_lost_and_forgets(fake):
    t, store = make(fake, token="R-old")
    with pytest.raises(FamilyError) as e:
        t.ensure_fresh()
    assert e.value.code == "session_lost" and store.get() is None and not t.is_signed_in()


def test_refresh_hiccup_keeps_the_token(fake):
    fake.refresh_error = (502, {"msg": "bad gateway"})
    t, store = make(fake, token="R1")
    with pytest.raises(FamilyError) as e:
        t.ensure_fresh()
    assert e.value.offline and store.get() == "R1" and t.is_signed_in()


def test_saved_login_alone_is_used_at_first_call(fake):
    t, store = make(fake, token="R1")
    assert t.has_saved_login() and t.is_signed_in()
    fake.rpc_answers["my_rules"] = {"allow": ["a.com"], "deny": [], "byGuide": []}
    assert t.rules()["allow"] == ["a.com"] and t.refresh_count == 1


def test_logout_scopes_and_works_offline(fake):
    t, store = make(fake)
    t.login("a@b.co", "pw")
    t.logout(everywhere=True)
    assert any(e[1] == "/auth/v1/logout?scope=global" for e in fake.log) and store.get() is None and not t.is_signed_in()
    t2 = fs.SupabaseFamilyTransport("http://127.0.0.1:1", "k", MemoryTokenStore("R"))
    t2._access = "x"
    t2.logout()
    assert not t2.has_saved_login()


def test_rpc_sends_the_users_token_and_rest_errors_map(fake):
    t, _ = make(fake)
    t.login("a@b.co", "pw")
    fake.rpc_errors["my_rules"] = (403, {"code": "42501", "message": "denied"})
    with pytest.raises(FamilyError) as e:
        t.rules()
    assert e.value.code == "no_access" and e.value.status == 403 and not e.value.offline
    fake.rpc_errors["my_rules"] = (500, {"message": "boom"})
    with pytest.raises(FamilyError) as e:
        t.rules()
    assert e.value.offline
    fake.rpc_errors["my_rules"] = (404, {"code": "PGRST202", "message": "no function"})
    with pytest.raises(FamilyError) as e:
        t.rules()
    assert e.value.code == "schema_out_of_date" and e.value.offline
    assert [e for e in fake.log if e[1].endswith("my_rules")][0][3] == "Bearer A1"


def test_raise_exception_text_maps(fake):
    t, _ = make(fake)
    t.login("a@b.co", "pw")
    fake.rpc_errors["me_heartbeat"] = (400, {"code": "P0001", "message": "device_taken: that deviceId belongs to another account"})
    with pytest.raises(FamilyError) as e:
        t.heartbeat({"deviceId": "d", "kind": "desktop", "version": "1", "enforcing": True, "rules": "ok", "sessionActive": False})
    assert e.value.code == "device_taken" and e.value.status == 409


def test_heartbeat_status_summary_screentime_shapes(fake):
    t, _ = make(fake)
    t.login("a@b.co", "pw")
    t.heartbeat({"deviceId": "dev1", "kind": "desktop", "version": "1.2", "enforcing": True, "rules": "stale", "sessionActive": True})
    t.post_status({"state": "live", "lockMode": "hard", "startedAt": "2026-11-26T10:00:00Z", "endsAt": "2026-11-26T10:30:00+00:00", "violations": 2})
    t.post_status({"state": "idle"})
    t.post_summaries([{"id": "s1", "startedAt": "2026-11-26T10:00:00Z", "endedAt": "2026-11-26T10:30:00Z", "plannedMin": 30, "focusedMin": 28, "taskName": "Essay",
                       "lockMode": "hard", "endType": "ended_early", "violations": 1, "pauses": {"free": 1, "checking": 0, "minutes": 2}, "source": "desktop",
                       "violationHosts": ["reddit.com"], "unblockPlans": [{"host": "a.com", "reason": "r", "plan": "p", "minutes": 5}]}])
    t.post_screentime([{"day": "2026-11-26", "hostname": "www.Khanacademy.org", "seconds": 60, "at": "2026-11-26T10:01:00Z"}])
    calls = {e[1].rsplit("/", 1)[1]: e[4] for e in fake.log if "/rpc/" in e[1]}
    assert calls["me_heartbeat"] == {"p_device": "dev1", "p_kind": "desktop", "p_version": "1.2", "p_enforcing": True, "p_rules": "stale", "p_session_active": True}
    status = [e[4] for e in fake.log if e[1].endswith("me_push_status")]
    assert status[0]["p_data"] == {"isActive": True, "isPaused": False, "isBreak": False, "lockMode": "hard", "startTime": "2026-11-26T10:00:00.000Z",
                                   "endTime": "2026-11-26T10:30:00.000Z", "state": "live", "violations": 2}
    assert status[1]["p_data"]["isActive"] is False and status[1]["p_fields"] == ["state"]
    item = calls["me_push_summaries"]["p_items"][0]
    assert item["id"] == "s1" and item["data"]["endType"] == "manual" and item["data"]["violationLog"] == [{"url": "https://reddit.com/"}]
    assert item["data"]["startTime"] == "2026-11-26T10:00:00.000Z" and item["data"]["pauseCounts"] == {"free": 1, "checking": 0}
    row = calls["me_push_screentime"]["p_rows"][0]
    assert row == {"day": "2026-11-26", "hostname": "khanacademy.org", "seconds": 60, "at": "2026-11-26T10:01:00.000Z", "category": "Learning"}


@pytest.mark.parametrize("bad", [
    {"state": "nope"}, {"state": "live", "startedAt": "yesterday"}, {"state": "live", "pausedMin": 5000}])
def test_bad_status_is_rejected_before_the_network(fake, bad):
    t, _ = make(fake)
    t.login("a@b.co", "pw")
    n = len(fake.log)
    with pytest.raises(FamilyError) as e:
        t.post_status(bad)
    assert e.value.code == "validation" and len(fake.log) == n


def test_bad_summary_and_hostname_rejected(fake):
    t, _ = make(fake)
    t.login("a@b.co", "pw")
    base = {"id": "s", "startedAt": "2026-11-26T10:00:00Z", "endedAt": "2026-11-26T09:00:00Z", "plannedMin": 1, "focusedMin": 1, "lockMode": "soft",
            "endType": "completed", "source": "desktop"}
    with pytest.raises(FamilyError):
        t.post_summaries([base])
    with pytest.raises(FamilyError) as e:
        t.post_screentime([{"day": "2026-11-26", "hostname": "https://a.com/x", "seconds": 5}])
    assert e.value.code == "invalid_hostname"


def test_category_table_matches_the_adapter():
    assert fs.category_of("mail.google.com") == "Other" and fs.category_of("m.youtube.com") == "Video"
    assert fs.category_of("en.wikipedia.org") == "Reference" and fs.category_of("unknown.example") == "Other"


def test_commands_ack_and_id_validation(fake):
    t, _ = make(fake)
    t.login("a@b.co", "pw")
    fake.rpc_answers["my_commands"] = [{"id": CMD, "type": "message", "payload": {}}]
    assert t.commands()[0]["id"] == CMD
    t.ack_command(CMD, True)
    ack = [e[4] for e in fake.log if e[1].endswith("/ack_command")][0]
    assert ack == {"p_id": CMD, "p_applied": True}
    with pytest.raises(FamilyError) as e:
        t.ack_command("../x", True)
    assert e.value.status == 404
    fake.rpc_errors["ack_command"] = (403, {"code": "P0001", "message": "no_access"})
    with pytest.raises(FamilyError) as e:
        t.ack_command(CMD, False)
    assert e.value.code == "not_found"


def test_malformed_replies_count_as_unreachable(fake):
    t, _ = make(fake)
    t.login("a@b.co", "pw")
    fake.rpc_answers["my_commands"] = {"not": "a list"}
    with pytest.raises(FamilyError) as e:
        t.commands()
    assert e.value.offline


def test_me_merges_profile_links_and_devices(fake):
    t, _ = make(fake)
    t.login("a@b.co", "pw")
    fake.rpc_answers["my_links"] = {"asStudent": [{"id": "l1", "status": "active", "expiresAt": "2000-01-01T00:00:00Z"}], "asGuide": []}
    fake.devices = [{"device_id": "d", "kind": "desktop", "version": "1", "enforcing": True, "rules_state": "ok", "session_active": False,
                     "last_seen": "2026-11-26T10:00:00.123456+00:00"}]
    me = t.me()
    assert me["user"]["id"] == UID and me["asStudent"]["links"][0]["expired"] is True
    assert me["asStudent"]["devices"][0]["lastSeenAt"] == "2026-11-26T10:00:00.123Z"
    get = [e for e in fake.log if e[0] == "GET"][0]
    assert f"user_id=eq.{UID}" in get[1] and "revoked=eq.false" in get[1]


def test_audit_schedule_conflicts_and_notifications(fake):
    t, _ = make(fake)
    t.login("a@b.co", "pw")
    fake.rpc_answers["my_audit_page"] = {"total": 1, "items": [{"at": "x", "actorName": "A", "action": "a", "detail": "d"}]}
    assert t.audit(10)[0]["actorName"] == "A"
    fake.rpc_answers["my_schedule_json"] = {"blocks": [
        {"id": "b1", "guideId": "g1", "guideName": "A", "title": "T", "dow": 1, "start": "10:00", "end": "11:00", "tzOffsetMin": 0, "lockMode": "hard", "allowlist": ["a.com"]}]}
    fake.rpc_answers["my_class_memberships"] = [{"classId": "c1", "name": "Alg", "tzOffsetMin": 0, "guide": {"id": "g2", "displayName": "B"},
                                                 "windows": [{"dow": 1, "start": "10:30", "end": "12:00"}]}]
    c = t.schedule_conflicts()
    assert c["count"] == 1 and c["conflicts"][0]["overlap"] == {"dow": 1, "start": "10:30", "end": "11:00", "minutes": 30, "tzOffsetMin": 0}
    fake.rpc_answers["my_notifications"] = {"items": [{"id": "n"}], "unread": 1, "total": 1}
    assert t.notifications(20)["unread"] == 1


def test_redeem_code_errors_and_revoke(fake):
    t, _ = make(fake)
    t.login("a@b.co", "pw")
    fake.rpc_answers["redeem_invite_json"] = {"error": "expired"}
    with pytest.raises(FamilyError) as e:
        t.redeem_code("ABC")
    assert e.value.code == "expired" and e.value.status == 400
    fake.rpc_answers["revoke_link_json"] = {"link": {"id": CMD, "status": "revoked"}}
    assert t.revoke_link(CMD)["link"]["status"] == "revoked"
    with pytest.raises(FamilyError) as e:
        t.revoke_link("nope")
    assert e.value.status == 404


def test_server_clock_offset_is_learned(fake):
    t, _ = make(fake)
    t.login("a@b.co", "pw")
    t.rules()
    assert t.server_clock_offset_ms() != 0


def test_make_transport_picks_the_backend(monkeypatch):
    import config
    import family_sync
    monkeypatch.setattr(config, "load_config", lambda: {"familyBackend": "supabase"})
    assert family_sync.make_transport().name == "supabase"
    monkeypatch.setattr(config, "load_config", lambda: {})
    assert family_sync.make_transport().name == "rest"


def test_sign_in_with_a_backend_choice_swaps_the_transport_and_saves_it(fake, tmp_path):
    import family_store
    import family_sync
    from tests_family.fakes import DictSettings, FakeHost, FakeTransport
    settings = DictSettings()
    made = []

    def factory(backend, base, key):
        made.append((backend, base, key))
        return fs.SupabaseFamilyTransport(base, key, MemoryTokenStore())
    fake.rpc_answers["my_profile"] = {"displayName": "Kid"}
    sync = family_sync.FamilySync(FakeTransport(), family_store.FamilyStore(str(tmp_path / "f.json")), FakeHost(), settings, transport_factory=factory)
    assert sync.sign_in("a@b.co", "pw", fake.url, "supabase", "sb_publishable_x") == (True, None)
    assert made == [("supabase", fake.url, "sb_publishable_x")] and sync.t.name == "supabase"
    assert settings.d["familyBackend"] == "supabase" and settings.d["familySupabaseUrl"] == fake.url and settings.d["familySupabaseKey"] == "sb_publishable_x"
    assert sync.snapshot()["backend"] == "supabase"


def test_sign_in_without_a_backend_choice_is_unchanged(tmp_path):
    import family_store
    import family_sync
    from tests_family.fakes import DictSettings, FakeHost, FakeTransport
    settings = DictSettings()
    sync = family_sync.FamilySync(FakeTransport(), family_store.FamilyStore(str(tmp_path / "f.json")), FakeHost(), settings)
    assert sync.sign_in("a@b.co", "pw", "http://localhost:1") == (True, None)
    assert "familyBackend" not in settings.d
