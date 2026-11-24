"""The family sync: lets a linked guide (parent or instructor) set rules and start sessions on this computer, and
lets the student see and control all of it. Runs only while the student is signed in to a family account
(config "familyEnabled", off by default); with it off nothing here does anything and the app behaves exactly as it
did before.

One pass (tick) every ~15 seconds, in this order -- the same order as the browser extension's tick:
    heartbeat -> links -> rules -> study schedule -> guide commands -> decide and act -> notes -> uploads
Everything before the heartbeat is local; everything after it tolerates the server being unreachable.

Rules this module lives by:
  * OFFLINE KEEPS ENFORCING. A pass that cannot reach the server changes nothing it already holds: the cached
    rules, the cached study schedule and any running session carry on. Never fail open (drop a rule because we
    could not hear about it) and never fail closed (invent a rule). A reply that is not exactly the expected shape
    counts as "could not reach" (see family_rules.sanitize_*).
  * NOTHING A GUIDE DOES IS SILENT. Every command, rules change, started session and link change leaves a note the
    student can read (Options -> Linked guides), and starts a desktop notice.
  * ONLY WHAT SOME ACTIVE LINK CAN SEE LEAVES THIS COMPUTER, hostname-only, and never anything from before consent
    (family_report.py). With no link, only the heartbeat goes.
  * The student can end a guide's session, leave a link, or sign out at any moment; the guide sees an early end.

This module knows nothing about Qt, win32 or any URL: it is given a FamilyTransport (the backend), a FamilyStore
(what to remember) and a host (the thin adapter onto session_manager; family_host.py). Tests give it fakes.
"""
import logging
import threading
import time

import family_rules as rules_mod
import family_report
from family_rules import clean_text, iso_from_ms
from family_transport import FamilyError

logger = logging.getLogger("carmen_family")

POLL_SECONDS = 15
OFFLINE_POLL_SECONDS = 30
STATUS_REFRESH_MS = 5 * 60 * 1000          # a status is re-sent at least this often so "last seen" stays true
HEARTBEAT_LOG_MS = 10 * 60 * 1000
MAX_SUMMARY_BATCH = 200
MAX_SCREEN_BATCH = 5000
RECENT_OK_MS = 5 * 60 * 1000

_FRIENDLY = {
    "invalid_credentials": "Incorrect email or password.",
    "validation": "Enter your email and password.",
    "bad_base": "That does not look like a server address.",
    "insecure_base": "Use an https:// address: a sign-in must not cross a network unencrypted.",
}


class FamilySync:
    def __init__(self, transport, store, host, settings, clock=None):
        self.t = transport
        self.store = store
        self.host = host
        self.settings = settings            # object with get(key, default) and set(**values)
        self._clock = clock or (lambda: time.time())
        self._tick_lock = threading.Lock()
        self._count = 0
        self._wake = threading.Event()
        self._last_report = {}
        self._hb_logged = (None, 0)
        self.last_error = None
        try:
            self.t.device_id = self.host.device_id()
        except Exception:
            pass

    # ---- time ----
    def _now_ms(self):
        return int(self._clock() * 1000)

    def _server_now_ms(self):
        """Now in the server's clock, so a command's age is judged the way the server made it."""
        return self._now_ms() + int(self.t.server_clock_offset_ms() or 0)

    # ---- switches ----
    def enabled(self):
        return bool(self.settings.get("familyEnabled", False))

    def poke(self):
        self._wake.set()

    # ---- sign-in and out ----
    def sign_in(self, email, password, base=None):
        """-> (ok, message). On success the family features are switched on and the first pass runs at once."""
        try:
            chosen = base if base is not None else self.settings.get("familyApiBase", "")
            self.t.configure(chosen)
            user = self.t.login(email, password)
        except FamilyError as err:
            return False, self._friendly(err)
        previous = self.store.get("user")
        if previous and previous.get("id") != user["id"]:
            self.store.clear_account()                 # a different account never inherits another's caches or marks
        self.store.set(user=user, deviceId=self.store.get("deviceId"))
        self.settings.set(familyEnabled=True, familyApiBase=self.t.base if hasattr(self.t, "base") else chosen)
        self.store.add_notice({"kind": "link", "text": f"Signed in to the family account {user.get('email') or ''}. "
                               "Nothing is shared until a parent or instructor links to you."}, self._now_ms())
        self.poke()
        return True, None

    def sign_out(self, everywhere=False, reason="Signed out"):
        """End the sign-in here and stop everything a guide set: their session, their blocks, their rules. Nothing of
        theirs applies once you are out. Leaves the notes so you can still read what happened."""
        self._end_guide_session(reason)
        try:
            self.t.logout(everywhere=everywhere)
        except FamilyError:
            pass
        self.settings.set(familyEnabled=False)
        self.store.clear_account()

    def _lost(self, err):
        self._end_guide_session("Signed out")
        self.settings.set(familyEnabled=False)
        self.store.clear_account()
        text = ("You were signed out of the family account for safety: your sign-in was used somewhere else, so it "
                "was ended. Sign in again. Your own sessions here are untouched."
                if err.message == "refresh_reused" else
                "You were signed out of the family account because your sign-in ran out. Sign in again to keep your "
                "links active. Your own sessions here are untouched.")
        self.store.add_notice({"kind": "security", "text": text}, self._now_ms())
        self._notify({"kind": "security", "by": None, "text": text})

    @staticmethod
    def _friendly(err):
        if err.offline:
            return "Couldn't reach the family server. Check the address and your connection."
        if err.code == "rate_limited":
            wait = f" Try again in {int(err.retry_after)} seconds." if isinstance(err.retry_after, (int, float)) else ""
            return "Too many tries." + wait
        return _FRIENDLY.get(err.code) or (err.message if err.message and err.code not in ("error",) else "Sign-in failed. Please try again.")

    def revoke_link(self, link_id):
        """The student ends one link, at once. -> (ok, message)."""
        try:
            self.t.revoke_link(link_id)
        except FamilyError as err:
            return False, "Couldn't reach the family server, so nothing changed. Try again." if err.offline else self._friendly(err)
        self.poke()
        self.tick(force=True)                       # the guide's rules and session stop in this very pass
        return True, None

    # ---- helpers ----
    def _notify(self, notice):
        try:
            self.host.notify(notice)
        except Exception:
            logger.debug("host.notify failed", exc_info=True)

    def _note(self, notice):
        """Store a note for the student and tell the desktop (a toast)."""
        self.store.add_notice(notice, self._now_ms())
        self._notify(notice)

    def _end_guide_session(self, reason):
        try:
            session = self.host.session_view()
            if session.get("isActive") and session.get("guide"):
                self.host.end_session({"endType": "manual", "reason": reason})
                self.store.set(tracked=None)
        except Exception:
            logger.warning("could not end the guide's session", exc_info=True)

    def _device_id(self):
        return self.store.get("deviceId") or self.host.device_id()

    # ---- the pass ----
    def tick(self, force=False):
        """One pass. Returns a small report (tests and the UI read it). Never raises."""
        with self._tick_lock:
            try:
                return self._tick(force)
            except Exception:
                logger.exception("family sync pass crashed")
                self.last_error = "crashed"
                return {"error": "crashed"}

    def _tick(self, force):
        report = {"reachable": False, "offline": False, "steps": [], "applied": [], "declined": []}
        if not self.enabled():
            report["skipped"] = "disabled"
            self._last_report = report
            return report
        self._count += 1
        count = self._count
        now = self._now_ms()

        try:
            self.t.ensure_fresh()
        except FamilyError as err:
            if err.code == "session_lost":
                self._lost(err)
                report["skipped"] = "session_lost"
                self._last_report = report
                return report
            report["offline"] = True
        if not self.t.is_signed_in():
            self.settings.set(familyEnabled=False)
            report["skipped"] = "not_signed_in"
            self._last_report = report
            return report

        def remote(name, fn, *args):
            """Run one call. -> (ok, value). An error never stops the pass; session_lost does (re-raised)."""
            try:
                value = fn(*args)
                report["reachable"] = True
                return True, value
            except FamilyError as err:
                if err.code == "session_lost":
                    raise
                report["steps"].append(f"{name}:{err.code}")
                if err.offline:
                    report["offline"] = True
                return False, err

        try:
            self._remote_pass(report, remote, force, count)
        except FamilyError as err:                    # only session_lost gets here
            self._lost(err)
            report["skipped"] = "session_lost"
        if report["reachable"]:
            self.store.set(lastOkMs=self._now_ms())
        self._last_report = report
        return report

    def _remote_pass(self, report, remote, force, count):
        store = self.store
        now = self._now_ms()
        server_now = self._server_now_ms()
        session = self.host.session_view()

        # 1. heartbeat
        links_cache = store.get("links")
        rules_cache = store.get("rules")
        recent = (store.get("lastRulesOkMs") or 0) > now - RECENT_OK_MS
        hb = {"deviceId": self._device_id(), "kind": "desktop", "version": self.host.app_version(),
              "enforcing": self.host.enforcing(session),
              "rules": rules_mod.rules_state(bool(rules_cache), recent),
              "sessionActive": bool(session.get("isActive"))}
        ok, value = remote("heartbeat", self.t.heartbeat, hb)
        if not ok and isinstance(value, FamilyError) and value.code == "device_taken":
            fresh = f"{hb['deviceId'][:40]}-{(store.get('user') or {}).get('id', 'x')[-8:]}"
            store.set(deviceId=fresh)
            hb["deviceId"] = fresh
            ok, value = remote("heartbeat", self.t.heartbeat, hb)
        if ok:
            store.set(lastHeartbeatOkMs=self._now_ms())
            sig = (hb["enforcing"], hb["rules"], hb["sessionActive"], hb["version"])
            if sig != self._hb_logged[0] or now - self._hb_logged[1] > HEARTBEAT_LOG_MS:
                store.log_sent("heartbeat", [hb], now)
                self._hb_logged = (sig, now)

        # 2. who is linked
        active_guides = None
        ok, value = remote("me", self.t.me)
        if ok:
            clean = rules_mod.sanitize_links(value)
            if clean is None:
                report["steps"].append("me:malformed")
            else:
                self._take_links(clean, now)
                links_cache = store.get("links")
                active_guides = {l["guideId"] for l in links_cache["links"] if rules_mod.is_active_link(l, now)}
        links = (links_cache or {}).get("links") or []

        # 3. rules (the cache is replaced only by a complete, valid copy)
        rules_changed = False
        ok, value = remote("rules", self.t.rules)
        if ok:
            clean = rules_mod.sanitize_rules(value)
            if clean is None:
                report["steps"].append("rules:malformed")
            else:
                before = (store.get("rules") or {}).get("version")
                rules_changed = before is not None and before != clean["version"]
                if before != clean["version"] or force:
                    store.set(rules={**clean, "fetchedAtMs": now})
                store.set(lastRulesOkMs=self._now_ms())
        rules_cache = store.get("rules")

        # 4. study schedule, class windows
        if count % 2 == 1 or force or not store.get("blocks"):
            ok, value = remote("schedule", self.t.schedule)
            if ok:
                blocks = rules_mod.sanitize_blocks(value)
                if blocks is None:
                    report["steps"].append("schedule:malformed")
                else:
                    store.set(blocks={"blocks": blocks, "fetchedAtMs": now})
        if count % 4 == 1 or force or store.get("classes") is None:
            ok, value = remote("classes", self.t.classes)
            if ok:
                classes = rules_mod.sanitize_classes(value)
                if classes is None:
                    report["steps"].append("classes:malformed")
                else:
                    store.set(classes={"classes": classes, "fetchedAtMs": now})
            ok, value = remote("conflicts", self.t.schedule_conflicts)
            if ok:
                self._take_conflicts(value, now)

        # 5. acknowledgements that could not be delivered earlier, then new commands
        self._flush_acks(report, remote)
        good, bad, fetched = [], [], False
        ok, value = remote("commands", self.t.commands)
        if ok:
            parsed = rules_mod.sanitize_commands(value)
            if parsed is None:
                report["steps"].append("commands:malformed")
            else:
                good, bad = parsed
                fetched = True

        # 6. decide and act
        session = self.host.session_view()
        snap = {"commands": good, "bad": bad, "processed": set(store.get("processed")),
                "rules": rules_cache, "blocks": (store.get("blocks") or {}).get("blocks") or [],
                "skips": store.get("skips"), "activeGuideIds": active_guides, "session": session,
                "tracked": store.get("tracked"), "pushedHosts": store.get("pushedHosts")}
        steps = rules_mod.decide(server_now, snap)
        rules_command_applied = self._act(steps, report, now)
        if rules_changed and not rules_command_applied:
            names = ", ".join(g["name"] for g in (rules_cache or {}).get("byGuide", []) if g.get("name")) or "A guide"
            self._note({"kind": "rules", "by": names, "text": f"{names} changed your rules. They apply during sessions."})
        self._flush_acks(report, remote)
        self._post_skips(remote)

        # 7. notes from the server (the inbox), the logs the student can read
        if count % 4 == 0 or force:
            ok, value = remote("notifications", self.t.notifications, 20)
            if ok:
                self._take_notifications(value, now)
        if count % 20 == 3 or force:
            self.refresh_mirror(remote)

        # 8. uploads: only what some active link may see
        self._uploads(report, remote, links, force, now, bool(steps))

    # ---- links ----
    def _take_links(self, clean, now):
        had_cache = self.store.get("links") is not None
        before = {l["id"]: l for l in ((self.store.get("links") or {}).get("links") or []) if rules_mod.is_active_link(l, now)}
        self.store.set(links={"links": clean["links"], "fetchedAtMs": now}, user=clean["user"])
        after = {l["id"]: l for l in clean["links"] if rules_mod.is_active_link(l, now)}
        for link in after.values():
            if link["id"] not in before and had_cache:
                rel = "instructor" if link["relation"] == "instructor" else "parent"
                self._note({"kind": "link", "by": link["guideName"],
                            "text": f"You are now linked to {link['guideName']} ({rel}). They can see what is listed under their name here, and you can end the link at any time."})
        for link_id, old in before.items():
            if link_id not in after:
                self._note({"kind": "link", "by": old["guideName"],
                            "text": f"{old['guideName']}: the link ended. They no longer see anything and their rules stop applying."})
            else:
                new = after[link_id]
                if sorted(old["scopes"]) != sorted(new["scopes"]) or old["detail"] != new["detail"]:
                    self._note({"kind": "link", "by": new["guideName"],
                                "text": f"What {new['guideName']} sees changed: {', '.join(new['scopes']) or 'nothing'} ({new['detail']})."})

    def _take_conflicts(self, value, now):
        conflicts = rules_mod.sanitize_conflicts(value)
        if conflicts is None:
            return
        seen = set(self.store.get("conflictsSeen"))
        names = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]
        for c in conflicts:
            if c["key"] in seen:
                continue
            self._note({"kind": "schedule", "by": f"{c['aName']} and {c['bName']}",
                        "text": f"{c['aName']} and {c['bName']} both have something on {names[c['dow']]} {c['start']}-{c['end']}. "
                                "If they disagree, the stricter lock applies. You may want to tell them."})
        self.store.set(conflictsSeen=sorted(c["key"] for c in conflicts))

    def _take_notifications(self, value, now):
        if not isinstance(value, dict) or not isinstance(value.get("notifications"), list):
            return
        items = []
        for n in value["notifications"][:30]:
            if isinstance(n, dict) and isinstance(n.get("id"), str):
                items.append({"id": n["id"], "type": clean_text(n.get("type"), 40), "title": clean_text(n.get("title"), 120),
                              "body": clean_text(n.get("body"), 300), "at": clean_text(n.get("at"), 40), "read": bool(n.get("read"))})
        unread = value.get("unread") if isinstance(value.get("unread"), int) and not isinstance(value.get("unread"), bool) else 0

        def put(data):
            data["mirror"]["notifications"] = items
            data["mirror"]["notificationsUnread"] = max(0, unread)
        self.store.update(put)

    def refresh_mirror(self, remote=None):
        """Fetch what the student is entitled to read: the audit trail of every guide action and the log of what this
        account has uploaded. Capped, text-only. Safe to call from the UI thread's helper."""
        def call(name, fn, *args):
            try:
                return True, fn(*args)
            except FamilyError as err:
                if err.code == "session_lost":
                    raise
                return False, err
        run = remote or call
        audit_ok, audit = run("audit", self.t.audit, 100)
        log_ok, shared = run("shared-log", self.t.shared_log, 100)
        now = self._now_ms()

        def put(data):
            if audit_ok and isinstance(audit, list):
                data["mirror"]["audit"] = [{"at": clean_text(a.get("at"), 40), "actorName": clean_text(a.get("actorName"), 60),
                                            "action": clean_text(a.get("action"), 40), "detail": clean_text(a.get("detail"), 300)}
                                           for a in audit[-100:] if isinstance(a, dict)]
            if log_ok and isinstance(shared, list):
                data["mirror"]["sharedLog"] = [{"at": clean_text(s.get("at"), 40), "kind": clean_text(s.get("kind"), 30),
                                                "count": s.get("count") if isinstance(s.get("count"), int) else 0,
                                                "fields": [clean_text(f, 60) for f in (s.get("fields") or [])[:30] if isinstance(f, str)]}
                                               for s in shared[-100:] if isinstance(s, dict)]
            data["mirror"]["fetchedAtMs"] = now
        self.store.update(put)

    # ---- acknowledgements ----
    def _flush_acks(self, report, remote):
        pending = self.store.get("pendingAcks")
        keep = []
        for ack in pending:
            ok, value = remote("ack", self.t.ack_command, ack["id"], ack["applied"])
            if not ok and (value.offline or value.status in (401, 429)):
                keep.append(ack)              # offline: next pass. 404/403/409 mean gone or settled: do not retry forever
            elif ok:
                report.setdefault("acked", []).append(ack["id"])
        if keep != pending:
            self.store.set(pendingAcks=keep)

    # ---- acting on decisions ----
    def _act(self, steps, report, now):
        """Carry out decide()'s steps in order. Returns True if a set_rules command was applied."""
        rules_command = False
        for step in steps:
            action, cid = step["action"], step.get("commandId")
            applied = bool(step.get("applied"))
            why = step.get("why")
            try:
                if action == "start_session":
                    session_id = self.host.start_session(step)
                    tracked = None
                    if step["guide"].get("kind") == "block":
                        tracked = {"kind": "block", "blockIds": step["guide"]["blockIds"], "day": step.get("day"),
                                   "endsAtMs": step["endsAtMs"], "sessionId": session_id}
                    self.store.set(tracked=tracked, pushedHosts=[])
                    self._note(step["notice"])
                elif action == "update_session":
                    self.host.update_session(step)
                    if step["guide"].get("kind") == "block":
                        tracked = self.store.get("tracked")
                        if tracked:
                            tracked["blockIds"] = step["guide"]["blockIds"]
                            self.store.set(tracked=tracked)
                elif action == "end_session":
                    self.host.end_session(step)
                    self.store.set(tracked=None, pushedHosts=[])
                    if step.get("notice"):
                        self._note(step["notice"])
                elif action == "skip_block":
                    self._record_skip(step, now)
                elif action == "notice":
                    self._note(step["notice"])
                    rules_command = rules_command or bool(step.get("refreshRules"))
                elif action == "ignore" and not applied and not step.get("reack"):
                    self.store.add_notice({"kind": "info", "by": None, "silent": True,
                                           "text": f"A request from your guide was not applied ({why or 'not applicable'})."}, now)
            except Exception:
                logger.warning("could not carry out %s", action, exc_info=True)
                applied, why = False, "error"
            if cid:
                if step.get("reack"):
                    self.store.queue_ack(cid, True)
                else:
                    self.store.mark_processed(cid)
                    self.store.queue_ack(cid, applied)
                    (report["applied"] if applied else report["declined"]).append(cid)
        report["actions"] = [s["action"] for s in steps if s["action"] != "ignore"]
        return rules_command

    def _record_skip(self, step, now):
        reason = "Ended from Carmen on this computer"
        day = step.get("day")

        def add(data):
            for block_id in step["blockIds"]:
                data["skips"] = (data["skips"] + [{"blockId": block_id, "day": day, "reason": reason, "atMs": now}])[-200:]
                data["skipsToPost"] = (data["skipsToPost"] + [{"blockId": block_id, "reason": reason, "day": day}])[-50:]
            data["tracked"] = None
        self.store.update(add)
        self._note(step["notice"])

    def _post_skips(self, remote):
        pending = self.store.get("skipsToPost")
        keep = []
        for skip in pending:
            ok, value = remote("skip", self.t.post_schedule_skip, skip["blockId"], skip["reason"], skip["day"])
            if not ok and value.offline:
                keep.append(skip)             # a 404 (block gone) or 409 (already skipped) is not retried
        if keep != pending:
            self.store.set(skipsToPost=keep)

    # ---- uploads ----
    def _uploads(self, report, remote, links, force, now, acted):
        store = self.store
        flags = rules_mod.share_flags(links, now)
        session = self.host.session_view()

        # live status
        payload = family_report.status_payload(session, now, flags)
        if payload is not None:
            sig = repr(sorted(payload.items()))
            marks = store.get("uploadMarks")
            if force or acted or marks.get("statusSig") != sig or now - (marks.get("statusAtMs") or 0) > STATUS_REFRESH_MS:
                ok, _ = remote("status", self.t.post_status, payload)
                if ok:
                    marks.update(statusSig=sig, statusAtMs=now)
                    store.set(uploadMarks=marks)
                    store.log_sent("status", [payload], now)

        # finished sessions (nothing from before consent: the first pass only sets the mark)
        marks = store.get("uploadMarks")
        entries = self.host.history_after(marks.get("historyWatermark"))
        if marks.get("historyWatermark") is None:
            marks["historyWatermark"] = self.host.now_local_iso()
            store.set(uploadMarks=marks)
            entries = []
        if entries:
            newest = max((e.get("endTime") or "") for e in entries)
            built = [s for s in (family_report.summary_from_history(e, flags) for e in entries) if s] if flags["sessions"] else []

            def put(data):
                data["uploadMarks"]["historyWatermark"] = newest
                if built:
                    data["queues"]["summaries"] = (data["queues"]["summaries"] + built)[-rules_mod.MAX_ENTRIES:]
            store.update(put)
        if not flags["sessions"]:
            store.update(lambda d: d["queues"].__setitem__("summaries", []))
        self._flush_queue("summaries", self.t.post_summaries, remote, now, MAX_SUMMARY_BATCH)

        # screen time (website names only; the seconds since the last pass)
        local_day, domains = self.host.domain_seconds_today()
        rows, sent = family_report.screentime_rows(domains, store.get("uploadMarks").get("screenSent"), local_day, now)
        sent = {k: v for k, v in sent.items() if k.startswith(f"{local_day}|")}      # yesterday's marks are done with

        def put_screen(data):
            data["uploadMarks"]["screenSent"] = sent
            if flags["screentime"] and rows:
                data["queues"]["screentime"] = family_report.merge_screentime(data["queues"]["screentime"], rows)
            if not flags["screentime"]:
                data["queues"]["screentime"] = []
        store.update(put_screen)
        self._flush_queue("screentime", self.t.post_screentime, remote, now, MAX_SCREEN_BATCH)

    def _flush_queue(self, name, send, remote, now, batch_size):
        queue = self.store.get("queues")[name]
        if not queue:
            return
        batch = queue[:batch_size]
        ok, value = remote(name, send, batch)
        if ok:
            self.store.update(lambda d: d["queues"].__setitem__(name, d["queues"][name][len(batch):]))
            self.store.log_sent(name, batch, now)
        elif not value.offline and value.status not in (401, 429) and 400 <= value.status < 500:
            # refused as invalid: retrying would wedge the queue forever. Drop it, and say so.
            self.store.update(lambda d: d["queues"].__setitem__(name, d["queues"][name][len(batch):]))
            self.store.log_sent(name, [], now, rejected=value.code, dropped=len(batch))

    # ---- what the rest of the app asks ----
    def effective_rules(self):
        """The guide rules in force on this computer, read-only, for the browser extension (GET /family/rules) and the
        UI. Always safe to call: offline or signed out it reports the cached copy, or nothing."""
        now = self._now_ms()
        store = self.store.snapshot()
        enabled = self.enabled()
        rules = store.get("rules") or {}
        blocks = (store.get("blocks") or {}).get("blocks") or []
        cover = rules_mod.evaluate_blocks(blocks, now, store.get("skips")) if enabled else None
        classes = (store.get("classes") or {}).get("classes") or []
        window = rules_mod.active_class_window(classes, now) if enabled else None
        recent = (store.get("lastRulesOkMs") or 0) > now - RECENT_OK_MS
        by_guide = rules.get("byGuide") or []
        window_deny = []
        if window:
            guide_id = (window["cls"].get("guide") or {}).get("id")
            for g in by_guide:
                if g.get("guideId") == guide_id:
                    window_deny = list(g.get("deny") or [])
        return {
            "enabled": enabled,
            "signedIn": bool(enabled and self.t.is_signed_in()),
            "allow": list(rules.get("allow") or []) if enabled else [],
            "deny": list(rules.get("deny") or []) if enabled else [],
            "byGuide": [{"guideId": g["guideId"], "name": g["name"], "allow": g["allow"], "deny": g["deny"], "note": g.get("note", "")}
                        for g in by_guide] if enabled else [],
            "version": rules.get("version", "") if enabled else "",
            "status": rules_mod.rules_state(bool(rules), recent) if enabled else "off",
            "fetchedAtMs": rules.get("fetchedAtMs"),
            "studyBlock": ({"lockMode": cover["lockMode"], "allow": cover["allow"], "endsAt": iso_from_ms(cover["endsAtMs"]),
                            "by": cover["by"]} if cover else None),
            "classWindow": ({"name": window["cls"]["name"], "by": window["cls"]["guide"]["displayName"],
                             "endsAt": iso_from_ms(window["endsAtMs"]), "deny": window_deny} if window else None),
        }

    def snapshot(self):
        """Everything the Linked guides panel shows, as plain data."""
        now = self._now_ms()
        data = self.store.snapshot()
        links = [l for l in ((data.get("links") or {}).get("links") or [])]
        out_links = []
        for link in links:
            info = rules_mod.describe_link(link)
            out_links.append({**link, "active": rules_mod.is_active_link(link, now), "sees": info["sees"], "can": info["can"]})
        last_ok = data.get("lastOkMs")
        return {
            "enabled": self.enabled(), "signedIn": bool(self.enabled() and self.t.is_signed_in()),
            "base": self.settings.get("familyApiBase", ""), "user": data.get("user"),
            "links": out_links, "notices": list(reversed(data.get("notices") or [])),
            "unreadNotices": sum(1 for n in data.get("notices") or [] if not n.get("read") and not n.get("silent")),
            "rules": self.effective_rules(),
            "reachable": bool(last_ok and now - last_ok < 2 * 60 * 1000),
            "lastOkMs": last_ok, "lastHeartbeatOkMs": data.get("lastHeartbeatOkMs"),
            "mirror": data.get("mirror"), "sentLog": list(reversed(data.get("sentLog") or [])),
            "classes": (data.get("classes") or {}).get("classes") or [],
            "blocks": (data.get("blocks") or {}).get("blocks") or [],
            "queues": {k: len(v) for k, v in (data.get("queues") or {}).items()},
        }

    def session_attribution(self):
        """'Set by Ms. Okafor' for the running session, or '' when no guide set it."""
        try:
            return rules_mod.attribution(self.host.session_view().get("guide"))
        except Exception:
            return ""

    # ---- the background loop ----
    def run(self, stop_event):
        failures = 0
        while not stop_event.is_set():
            self._wake.clear()
            report = self.tick()
            offline = bool(report.get("offline")) and not report.get("reachable")
            failures = failures + 1 if offline else 0
            wait = OFFLINE_POLL_SECONDS if offline else POLL_SECONDS
            if report.get("skipped") in ("disabled", "not_signed_in", "session_lost"):
                wait = POLL_SECONDS * 2
            self._wake.wait(wait)

    def start(self, stop_event):
        thread = threading.Thread(target=self.run, args=(stop_event,), daemon=True, name="family-sync")
        thread.start()
        return thread


# ---------------------------------------------------------------------------------------------------------
# the instance the app uses (built on first use so importing this module costs nothing)
# ---------------------------------------------------------------------------------------------------------

_instance = None
_instance_lock = threading.Lock()


class _ConfigSettings:
    """settings adapter over config.py: only the family keys, thread-safe through config.update_config."""

    def get(self, key, default=None):
        import config
        return config.load_config().get(key, default)

    def set(self, **values):
        import config
        config.update_config(lambda cfg: cfg.update(values))


def make_transport():
    """The ONE place that chooses the backend. Supabase later: return family_supabase.SupabaseFamilyTransport()."""
    import family_rest
    import config
    return family_rest.FamilyRest(config.load_config().get("familyApiBase") or family_rest.DEFAULT_BASE)


def instance():
    global _instance
    with _instance_lock:
        if _instance is None:
            import family_host
            import family_store
            _instance = FamilySync(make_transport(), family_store.default(), family_host.DesktopHost(), _ConfigSettings())
        return _instance


def set_instance(sync):
    """For tests: swap the app's instance (None resets)."""
    global _instance
    with _instance_lock:
        _instance = sync


def is_enabled():
    """Cheap check the rest of the app uses to stay out of the way when the family features are off."""
    try:
        import config
        return bool(config.load_config().get("familyEnabled", False))
    except Exception:
        return False


def start(stop_event):
    """Start the background pass. It idles (and changes nothing) until the family features are switched on."""
    try:
        return instance().start(stop_event)
    except Exception:
        logger.exception("could not start the family sync; the rest of the app is unaffected")
        return None


def poke():
    if _instance is not None:
        _instance.poke()
