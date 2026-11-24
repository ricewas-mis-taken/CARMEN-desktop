import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

import family_report as rep
import family_rest
import family_store
from family_transport import FamilyError

FULL = {"any": True, "status": True, "sessions": True, "screentime": True, "tasks": True, "detail": True}
NONE = {k: False for k in FULL}


def entry(**kw):
    base = {"startTime": "2026-11-23T10:00:00", "endTime": "2026-11-23T10:30:00", "endType": "natural", "lockMode": "hard", "violationCount": 1,
            "violationLog": [], "sessionId": "s1", "eventTitle": "Essay", "source": "task"}
    base.update(kw)
    return base


def test_summary_shape_and_endtypes():
    s = rep.summary_from_history(entry(), FULL)
    assert s["endType"] == "completed" and s["focusedMin"] == 30 and s["source"] == "desktop" and s["id"] == "s1"
    assert rep.summary_from_history(entry(endType="manual"), FULL)["endType"] == "ended_early"
    assert rep.summary_from_history(entry(endType="nuclear"), FULL)["endType"] == "nuclear"
    assert rep.summary_from_history(entry(endType="superseded"), FULL)["endType"] == "ended_early"


def test_pauses_are_subtracted_and_counted():
    log = [{"kind": "pause", "timestamp": "2026-11-23T10:10:00", "pauseReason": "break"}, {"kind": "resume", "timestamp": "2026-11-23T10:15:00"},
           {"kind": "pause", "timestamp": "2026-11-23T10:20:00", "pauseReason": "checking"}, {"kind": "resume", "timestamp": "2026-11-23T10:22:00"}]
    s = rep.summary_from_history(entry(violationLog=log), FULL)
    assert s["focusedMin"] == 23 and s["pauses"] == {"free": 1, "checking": 1, "minutes": 7}


def test_no_detail_without_detail_flag_and_no_task_without_scope():
    e = entry(endType="nuclear", reason="why", violationLog=[{"kind": "domain", "url": "https://a.com/p?x=1"}])
    s = rep.summary_from_history(e, {**FULL, "detail": False, "tasks": False})
    assert "reason" not in s and "violationHosts" not in s and s["taskName"] == ""


def test_review_problem_never_leaks():
    s = rep.summary_from_history(entry(source="review", reviewProblemName="Secret problem"), FULL)
    assert s["taskName"] == "Review" and "Secret" not in json.dumps(s)


def test_app_names_and_ips_are_never_hostnames():
    e = entry(violationLog=[{"kind": "process", "process": "discord.exe"}, {"kind": "domain", "url": "http://192.168.0.1/x"}, {"kind": "domain", "url": "https://www.reddit.com/r/x"}])
    assert rep.summary_from_history(e, FULL)["violationHosts"] == ["reddit.com"]


@pytest.mark.parametrize("bad", [None, 5, {}, {"startTime": "x"}, entry(endTime="2026-11-23T09:00:00"), entry(beside=True), entry(startTime=None)])
def test_unreportable_entries_are_skipped(bad):
    assert rep.summary_from_history(bad, FULL) is None


def test_guide_session_summary_uses_the_guides_planned_minutes_and_class():
    s = rep.summary_from_history(entry(source="guide", guide={"name": "Okafor", "plannedMin": 45, "className": "Algebra", "classSessionId": "cs1"}), NONE | {"sessions": True})
    assert s["plannedMin"] == 45 and s["taskName"] == "Algebra" and s["classSessionId"] == "cs1"


def test_status_payload_gated_by_flag():
    assert rep.status_payload({"isActive": True}, 0, NONE) is None
    assert rep.status_payload({"isActive": False}, 0, FULL) == {"state": "idle"}


def test_status_live_fields():
    p = rep.status_payload({"isActive": True, "isPaused": False, "lockMode": "hard", "secondsRemaining": 600, "startTime": "2026-11-23T10:00:00",
                            "violationCount": 3, "violationLog": [], "source": "manual"}, 1_795_428_000_000, FULL)
    assert p["state"] == "live" and p["lockMode"] == "hard" and p["violations"] == 3 and p["endsAt"].endswith("Z")


def test_screentime_rows_deltas_and_unshareable_hosts():
    rows, sent = rep.screentime_rows({"khanacademy.org": 100, "localhost": 40, "10.0.0.1": 9}, {}, "2026-11-23", 1_795_428_000_000)
    assert [(r["hostname"], r["seconds"]) for r in rows] == [("khanacademy.org", 100)]
    rows2, sent2 = rep.screentime_rows({"khanacademy.org": 130}, sent, "2026-11-23", 1_795_428_060_000)
    assert [(r["hostname"], r["seconds"]) for r in rows2] == [("khanacademy.org", 30)]
    assert rep.screentime_rows({"khanacademy.org": 130}, sent2, "2026-11-23", 0)[0] == []


def test_merge_screentime_same_minute():
    a = {"day": "d", "hostname": "a.com", "seconds": 5, "at": "t"}
    assert rep.merge_screentime([a], [{**a, "seconds": 7}])[0]["seconds"] == 12


# ---- store
def test_store_roundtrip_corrupt_and_wrong_shape(tmp_path):
    p = tmp_path / "f.json"
    s = family_store.FamilyStore(str(p))
    s.set(rules={"allow": ["a"]})
    assert family_store.FamilyStore(str(p)).get("rules") == {"allow": ["a"]}
    p.write_text("{not json")
    assert family_store.FamilyStore(str(p)).get("rules") is None and list(tmp_path.glob("f.json.corrupt-*"))
    p.write_text(json.dumps([1, 2]))
    assert family_store.FamilyStore(str(p)).get("processed") == []
    p.write_text(json.dumps({"processed": "oops", "queues": {"summaries": 5}}))
    st = family_store.FamilyStore(str(p))
    assert st.get("processed") == [] and st.get("queues") == {"summaries": [], "screentime": []}


def test_clear_account_keeps_notices(tmp_path):
    s = family_store.FamilyStore(str(tmp_path / "f.json"))
    s.set(rules={"x": 1}, processed=["a"])
    s.add_notice({"text": "hello"}, 1)
    s.clear_account()
    assert s.get("rules") is None and s.get("processed") == [] and s.get("notices")[0]["text"] == "hello"


# ---- rest
class _H(BaseHTTPRequestHandler):
    log = []
    refresh_ok = True

    def log_message(self, *a): pass

    def _send(self, code, obj, headers=None):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(n) or b"{}")
        _H.log.append((self.path, self.headers.get("Authorization")))
        if self.path == "/auth/login":
            return self._send(200, {"token": "t1", "expiresAt": "2099-01-01T00:00:00Z", "refreshToken": "r1", "user": {"id": "u", "email": "e", "displayName": "d"}})
        if self.path == "/auth/refresh":
            import time; time.sleep(0.05)
            if not _H.refresh_ok or body.get("refreshToken") != "r1" and body.get("refreshToken") != "r2":
                return self._send(401, {"error": "refresh_reused", "message": "x"})
            return self._send(200, {"token": "t2", "expiresAt": "2099-01-01T00:00:00Z", "refreshToken": "r2", "user": {"id": "u"}})
        if self.path == "/me/heartbeat":
            if self.headers.get("Authorization") == "Bearer t1":
                return self._send(401, {"error": "unauthorized"})
            return self._send(200, {"ok": True})
        self._send(404, {"error": "not_found"})

    def do_GET(self):
        _H.log.append((self.path, self.headers.get("Authorization")))
        if self.path == "/garbage":
            self.send_response(200); self.end_headers(); self.wfile.write(b"<html>"); return
        if self.path == "/boom":
            return self._send(503, {"error": "x"})
        self._send(200, {"v": 1}, {"ETag": 'W/"1"'} if self.headers.get("If-None-Match") is None else {})


@pytest.fixture()
def server():
    _H.log, _H.refresh_ok = [], True
    srv = HTTPServer(("127.0.0.1", 0), _H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_port}"
    srv.shutdown()


def test_login_saves_refresh_token_and_401_refreshes_once_then_retries(server):
    store = family_rest.MemoryTokenStore()
    t = family_rest.FamilyRest(server, store)
    t.login("a@b.c", "pw")
    assert store.get() == "r1"
    assert t.heartbeat({"x": 1}) == {"ok": True}
    assert t.refresh_count == 1 and store.get() == "r2"


def test_concurrent_401s_refresh_only_once(server):
    store = family_rest.MemoryTokenStore()
    t = family_rest.FamilyRest(server, store)
    t.login("a@b.c", "pw")
    results = []
    threads = [threading.Thread(target=lambda: results.append(t.heartbeat({}))) for _ in range(6)]
    [x.start() for x in threads]; [x.join() for x in threads]
    assert len(results) == 6 and t.refresh_count == 1


def test_refused_refresh_is_session_lost_and_forgets(server):
    store = family_rest.MemoryTokenStore("r-bad")
    t = family_rest.FamilyRest(server, store)
    with pytest.raises(FamilyError) as e:
        t.ensure_fresh()
    assert e.value.code == "session_lost" and store.get() is None and not t.is_signed_in()


def test_unreachable_server_is_offline_and_keeps_the_token():
    store = family_rest.MemoryTokenStore("r1")
    t = family_rest.FamilyRest("http://127.0.0.1:1", store)
    with pytest.raises(FamilyError) as e:
        t.ensure_fresh()
    assert e.value.offline and store.get() == "r1"


def test_garbage_and_5xx_count_as_unreachable(server):
    t = family_rest.FamilyRest(server, family_rest.MemoryTokenStore())
    t._access = "x"
    for path in ("/garbage", "/boom"):
        with pytest.raises(FamilyError) as e:
            t._request("GET", path)
        assert e.value.offline


@pytest.mark.parametrize("base,ok", [("http://localhost:8420", True), ("http://127.0.0.1:1", True), ("https://family.example.com", True),
                                     ("family.example.com", True), ("http://family.example.com", False), ("ftp://x.com", False), ("", False),
                                     ("https://user:pw@x.com", False)])
def test_base_url_rules(base, ok):
    if ok:
        assert family_rest.normalize_base(base).startswith("http")
    else:
        with pytest.raises(FamilyError):
            family_rest.normalize_base(base)
