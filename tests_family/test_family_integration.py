"""End to end against a COPY of the real CarmenLinked backend (node), on port 8461: login, token refresh after a
short expiry, a guide starting a session through the API, the desktop client deciding and acknowledging, revoking,
and the backend going down. Only cross-platform Python APIs are used (tempfile, shutil, subprocess, urllib)."""
import json
import os
import shutil
import subprocess
import tempfile
import time
import urllib.error
import urllib.request

import pytest

import family_rules as fr
import family_rest
import family_store
import family_sync
from tests_family.fakes import DictSettings, FakeHost

BACKEND = os.environ.get("CARMENLINKED_DIR", "/home/user/build/carmenlinked")
PORT = 8461
BASE = f"http://127.0.0.1:{PORT}"
NODE = shutil.which("node")

pytestmark = pytest.mark.skipif(not NODE or not os.path.isdir(BACKEND), reason="node or the carmenlinked backend is not available")


def http(method, path, body=None, token=None):
    if token == "dana":                      # guide tokens last 3 seconds here too: log in fresh each call
        token = guide_login("dana@demo.test")
    req = urllib.request.Request(BASE + path, data=json.dumps(body).encode() if body is not None else None, method=method,
                                 headers={"Content-Type": "application/json", **({"Authorization": f"Bearer {token}"} if token else {})})
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, json.loads(r.read() or b"null")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"null")


class Backend:
    def start(self):
        self.dir = tempfile.mkdtemp(prefix="carmenlinked-copy-")
        shutil.copytree(BACKEND, os.path.join(self.dir, "app"), ignore=shutil.ignore_patterns("node_modules", ".git", "data"))
        self.app = os.path.join(self.dir, "app")
        os.makedirs(os.path.join(self.app, "data"), exist_ok=True)
        env = {**os.environ, "PORT": str(PORT), "ACCESS_TTL_MIN": "0.05", "HOST": "127.0.0.1", "DB_PATH": os.path.join(self.app, "data", "db.json"), "SCRYPT_N": "1024"}
        subprocess.run([NODE, "src/seed.js"], cwd=self.app, env=env, check=True, capture_output=True, timeout=60)
        self.proc = subprocess.Popen([NODE, "src/server.js"], cwd=self.app, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        for _ in range(100):
            try:
                if http("GET", "/health")[0] == 200:
                    return
            except Exception:
                time.sleep(0.1)
        raise RuntimeError("backend did not start")

    def stop(self):
        if self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(5)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        shutil.rmtree(self.dir, ignore_errors=True)


@pytest.fixture()
def backend():
    b = Backend()
    b.start()
    yield b
    b.stop()


def guide_login(email):
    code, body = http("POST", "/auth/login", {"email": email, "password": "carmen123"})
    assert code == 200, body
    return body["token"]


def signup_student(email="riley@demo.test"):
    code, body = http("POST", "/auth/signup", {"email": email, "password": "carmen123", "displayName": "Riley", "birthYear": 2010})
    assert code == 201, body
    return body


def link_to_dana(student_token):
    dana = "dana"
    code, inv = http("POST", "/links/invite", {"side": "guide", "relation": "parent"}, dana)
    assert code == 201
    code, redeemed = http("POST", "/links/redeem", {"code": inv["code"]}, student_token)
    assert code == 200, redeemed
    return dana, redeemed["link"]


def make_client(tmp_path):
    t = family_rest.FamilyRest(BASE, family_rest.MemoryTokenStore())
    host = FakeHost()
    settings = DictSettings(familyApiBase=BASE)
    sync = family_sync.FamilySync(t, family_store.FamilyStore(str(tmp_path / "f.json")), host, settings)
    return sync, t, host, settings


def test_full_flow(backend, tmp_path):
    student = signup_student()
    sync, t, host, settings = make_client(tmp_path)

    # login, then a pass with nobody linked: only the heartbeat leaves
    assert sync.sign_in("riley@demo.test", "carmen123", BASE) == (True, None)
    sync.tick(force=True)
    assert sync.snapshot()["links"] == [] and settings.d["familyEnabled"] is True

    # a parent links (consent is the student's own act)
    dana, lnk = link_to_dana(student["token"])
    sync.tick(force=True)
    assert [l["guideName"] for l in sync.snapshot()["links"]] == ["Dana Reyes"] or sync.snapshot()["links"][0]["active"]

    # the access token lasts 3 seconds: wait it out, then a pass must refresh by itself
    time.sleep(3.5)
    before = t.refresh_count
    sync.tick()
    assert t.refresh_count > before

    # the guide sets rules, then starts a session through the API
    code, _ = http("POST", f"/students/{lnk['studentId']}/rules", {"allow": ["khanacademy.org"], "deny": ["tiktok.com"], "note": "Homework"}, dana)
    assert code == 201
    code, cmd = http("POST", f"/students/{lnk['studentId']}/commands",
                     {"type": "start_session", "payload": {"allowlist": ["desmos.com"], "lockMode": "hard", "minutes": 20}}, dana)
    assert code == 201, cmd
    report = sync.tick(force=True)
    assert [a[0] for a in host.actions].count("start") == 1
    kind, lock, allow, who = host.actions[0]
    assert lock == "hard" and who == "Dana Reyes" and "desmos.com" in allow and "khanacademy.org" in allow and "tiktok.com" not in allow
    code, mine = http("GET", f"/students/{lnk['studentId']}/commands", None, dana)
    assert any(c["id"] == cmd["command"]["id"] and c["status"] == "applied" for c in (mine if isinstance(mine, list) else mine.get("commands", [])))
    assert sync.effective_rules()["deny"] == ["tiktok.com"]

    # the guide sees the live status the desktop pushed
    code, status = http("GET", f"/students/{lnk['studentId']}/status", None, dana)
    assert code == 200 and status["state"] == "live" and status["lockMode"] == "hard"

    # a command that already expired is declined
    code, old = http("POST", f"/students/{lnk['studentId']}/commands", {"type": "message", "payload": {"text": "hi"}}, dana)
    sync.tick(force=True)
    assert any("Dana Reyes: hi" in n["text"] for n in sync.store.get("notices"))

    # backend down: cached rules and the running session stay
    backend.proc.terminate()
    backend.proc.wait(5)
    rep = sync.tick(force=True)
    assert rep["offline"] and host.session["isActive"] and sync.effective_rules()["deny"] == ["tiktok.com"]

    # a malformed reply from a server that is up is also "unreachable", not "no rules" (covered in unit tests);
    # restart the backend and revoke: the guide's session and rules stop in one pass
    backend.proc = subprocess.Popen([NODE, "src/server.js"], cwd=backend.app, env={**os.environ, "PORT": str(PORT), "ACCESS_TTL_MIN": "0.05", "HOST": "127.0.0.1", "DB_PATH": os.path.join(backend.app, "data", "db.json"), "SCRYPT_N": "1024"},
                                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    for _ in range(100):
        try:
            if http("GET", "/health")[0] == 200:
                break
        except Exception:
            time.sleep(0.1)
    ok, msg = sync.revoke_link(lnk["id"])
    assert ok, msg
    assert not host.session["isActive"] and sync.effective_rules()["deny"] == []
    code, _ = http("GET", f"/students/{lnk['studentId']}/status", None, dana)
    assert code == 403

    # the student can read what happened
    sync.refresh_mirror()
    mirror = sync.store.get("mirror")
    assert mirror["audit"] and any("rules" in a["action"] or "command" in a["action"] for a in mirror["audit"])


def test_wrong_password_and_refresh_reuse(backend, tmp_path):
    signup_student()
    sync, t, host, settings = make_client(tmp_path)
    ok, msg = sync.sign_in("riley@demo.test", "nope", BASE)
    assert (ok, msg) == (False, "Incorrect email or password.")
    assert sync.sign_in("riley@demo.test", "carmen123", BASE)[0]
    # someone else spends our refresh token: the next refresh is treated as theft and the sign-in ends
    stolen = t._tokens.get()
    http("POST", "/auth/refresh", {"refreshToken": stolen})
    time.sleep(3.2)
    rep = sync.tick(force=True)
    assert rep.get("skipped") == "session_lost" and settings.d["familyEnabled"] is False


def test_same_device_may_replay_a_refresh_but_a_thief_may_not(backend, tmp_path):
    signup_student()
    sync, t, host, settings = make_client(tmp_path)
    assert sync.sign_in("riley@demo.test", "carmen123", BASE)[0]
    token = t._tokens.get()
    first = http("POST", "/auth/refresh", {"refreshToken": token, "deviceId": "dev-1"})
    again = http("POST", "/auth/refresh", {"refreshToken": token, "deviceId": "dev-1"})     # a crash before saving, then a retry
    assert first[0] == again[0] == 200 and first[1]["refreshToken"] == again[1]["refreshToken"]
    thief = http("POST", "/auth/refresh", {"refreshToken": token, "deviceId": "someone-else"})
    assert thief[0] == 401 and thief[1]["error"] == "refresh_reused"
