"""Fakes for the sync tests: a transport that answers from a script, a host that records what was done."""
import family_rules as fr
from family_transport import FamilyError, FamilyTransport


class FakeTransport(FamilyTransport):
    name = "fake"

    def __init__(self):
        self.signed_in = True
        self.down = False
        self.lost = False
        self.base = "http://localhost:1"
        self.calls = []
        self.links = []
        self.rules_doc = {"allow": [], "deny": [], "byGuide": []}
        self.blocks_doc = {"blocks": []}
        self.classes_doc = []
        self.cmds = []
        self.acked = {}
        self.summaries, self.screen, self.statuses, self.hbs, self.skips = [], [], [], [], []
        self.raw = {}              # name -> raw value to return instead (to test malformed replies)

    def _go(self, name, value=None):
        self.calls.append(name)
        if self.down:
            raise FamilyError("offline", "down", 0, offline=True)
        return self.raw.get(name, value)

    def configure(self, base): self.base = base
    def login(self, email, password):
        if password != "pw":
            raise FamilyError("invalid_credentials", "no", 401)
        self.signed_in = True
        return {"id": "u_me", "email": email, "displayName": "Me"}
    def logout(self, everywhere=False): self.signed_in = False
    def has_saved_login(self): return self.signed_in
    def is_signed_in(self): return self.signed_in
    def ensure_fresh(self):
        if self.lost:
            raise FamilyError("session_lost", "refresh_expired", 401)
    def me(self): return self._go("me", {"user": {"id": "u_me", "email": "m@x", "displayName": "Me"}, "asStudent": {"links": self.links, "devices": []}})
    def heartbeat(self, payload): self.hbs.append(payload); return self._go("heartbeat", {"ok": True})
    def post_status(self, payload): r = self._go("status", {"ok": True}); self.statuses.append(payload); return r
    def post_summaries(self, items): r = self._go("summaries", {"accepted": len(items)}); self.summaries += items; return r
    def post_screentime(self, items): r = self._go("screentime", {"accepted": len(items)}); self.screen += items; return r
    def commands(self): return self._go("commands", [c for c in self.cmds if c["id"] not in self.acked])
    def ack_command(self, cid, applied): r = self._go("ack", {"ok": True}); self.acked[cid] = applied; return r
    def rules(self): return self._go("rules", self.rules_doc)
    def schedule(self): return self._go("schedule", self.blocks_doc)
    def schedule_conflicts(self): return self._go("conflicts", {"conflicts": []})
    def classes(self): return self._go("classes", self.classes_doc)
    def post_schedule_skip(self, block_id, reason, day): r = self._go("skip", {"skip": {}}); self.skips.append((block_id, day)); return r
    def notifications(self, limit=50): return self._go("notifications", {"notifications": [], "unread": 2})
    def mark_notifications_read(self): return self._go("read", {"ok": True})
    def audit(self, limit=200): return self._go("audit", [])
    def shared_log(self, limit=200): return self._go("shared", [])
    def revoke_link(self, link_id):
        self._go("revoke", {})
        for l in self.links:
            if l["id"] == link_id:
                l["status"] = "revoked"
        self.rules_doc = {"allow": [], "deny": [], "byGuide": []}
        self.cmds = []
        return {"link": {}}
    def redeem_code(self, code): return self._go("redeem", {})


class FakeHost:
    def __init__(self):
        self.session = {"isActive": False, "isPaused": False, "isBreak": False, "sessionId": None, "guide": None,
                        "lockMode": "soft", "domainWhitelist": [], "violationLog": [], "violationCount": 0}
        self.actions = []
        self.history = []
        self.domains = {}
        self.notices = []
        self.n = 0

    def session_view(self): return dict(self.session)
    def start_session(self, step):
        self.n += 1
        self.actions.append(("start", step["lockMode"], list(step["allow"]), step["guide"]["name"]))
        self.session = {**self.session, "isActive": True, "sessionId": f"s{self.n}", "guide": step["guide"], "lockMode": step["lockMode"],
                        "domainWhitelist": list(step["allow"]), "secondsRemaining": int(step["minutes"] * 60)}
        return f"s{self.n}"
    def update_session(self, step):
        self.actions.append(("update", step["lockMode"], list(step["allow"])))
        self.session.update(lockMode=step["lockMode"], domainWhitelist=list(step["allow"]), guide=step["guide"])
    def end_session(self, step):
        self.actions.append(("end", step.get("endType"), step.get("reason")))
        self.session = {**self.session, "isActive": False, "guide": None, "sessionId": None}
    def enforcing(self, s): return True
    def device_id(self): return "dev-1"
    def app_version(self): return "t"
    def now_local_iso(self): return "2026-11-23T00:00:00"
    def history_after(self, wm): return [e for e in self.history if wm is None or e["endTime"] > wm]
    def domain_seconds_today(self): return "2026-11-23", dict(self.domains)
    def notify(self, notice): self.notices.append(notice)


class DictSettings:
    def __init__(self, **kw): self.d = {"familyEnabled": True, "familyApiBase": "http://localhost:1", **kw}
    def get(self, k, default=None): return self.d.get(k, default)
    def set(self, **kw): self.d.update(kw)


def link(guide="u_dana", name="Dana", **kw):
    base = {"id": f"l_{guide}", "guideId": guide, "guide": {"id": guide, "displayName": name}, "relation": "parent", "status": "active",
            "scopes": ["status", "sessions", "screentime", "tasks", "score"], "detail": "summary", "canAssign": False, "canSetRules": True}
    base.update(kw)
    return base


def start_cmd(now_ms, cid="c1", minutes=30, lock="hard", allow=("khanacademy.org",), author="Dana", author_id="u_dana", **payload):
    return {"id": cid, "type": "start_session", "authorId": author_id, "authorName": author,
            "payload": {"allowlist": list(allow), "lockMode": lock, "minutes": minutes, **payload},
            "createdAt": fr.iso_from_ms(now_ms - 1000), "expiresAt": fr.iso_from_ms(now_ms + 9 * 60000)}
