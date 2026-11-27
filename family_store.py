"""What the family features remember between runs, in one small file (private/family_state.json).

It holds the last good copy of everything a guide sent (rules, study blocks, class windows), which commands were
already applied, the notes shown to the student, what was uploaded and the queues of what is still to upload.
The cached rules are what keeps being enforced when the server cannot be reached, so they are only ever replaced by
a complete, valid copy (see family_rules.sanitize_*).

Same habits as the other stores here: one lock, an atomic write (temp file then replace), a corrupt file is kept
aside as .corrupt-<time> and never silently overwritten, and a file of the wrong shape reads as empty.
The refresh token is NOT in here: it lives in the OS credential store (family_rest.py).
"""
import copy
import json
import os
import shutil
import threading
import time
import uuid

import paths

PATH = paths.private_path("family_state.json")

MAX_PROCESSED = 400
MAX_NOTICES = 120
MAX_SENT_LOG = 200
MAX_QUEUE = 2000

DEFAULTS = {
    "version": 1,
    "user": None,               # {"id", "displayName", "email"} of the signed-in family account
    "links": None,              # {"links": [...], "fetchedAtMs": n}
    "rules": None,              # {...sanitize_rules..., "fetchedAtMs": n}
    "blocks": None,             # {"blocks": [...], "fetchedAtMs": n}
    "classes": None,            # {"classes": [...], "fetchedAtMs": n}
    "conflictsSeen": [],        # conflict keys the student was already told about
    "skips": [],                # [{"blockId", "day", "reason", "atMs"}] study blocks the student ended early
    "processed": [],            # command ids already applied or declined
    "pendingAcks": [],          # [{"id", "applied"}] not yet delivered to the backend
    "notices": [],              # [{"id", "atMs", "kind", "text", "by", "url", "read"}]
    "pushedHosts": [],          # hosts of pushed links the student opened (allowed for a guide's running session)
    "tracked": None,            # the study-block session started here, so an early end can be told from the end of cover
    "uploadMarks": {},          # {"historyWatermark": str|None, "screenSent": {"<day>|<host>": seconds}, "statusSig", "statusAtMs"}
    "queues": {"summaries": [], "screentime": []},
    "sentLog": [],              # what this computer actually sent, with values (shown beside the server's own log)
    "mirror": {"audit": [], "sharedLog": [], "notifications": [], "notificationsUnread": 0, "fetchedAtMs": None},
    "skipsToPost": [],          # [{"blockId", "reason", "day"}] early-ended study blocks the backend has not been told about
    "deviceId": None,           # only set when the usual device id was already taken by another account
    "lastOkMs": None,           # last time the server answered anything
    "lastHeartbeatOkMs": None,
    "lastRulesOkMs": None,
}


class FamilyStore:
    def __init__(self, path=None):
        self._path = path
        self._lock = threading.RLock()
        self._data = None

    @property
    def path(self):
        return self._path or PATH

    def _read(self):
        path = self.path
        if not os.path.exists(path):
            return copy.deepcopy(DEFAULTS)
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except json.JSONDecodeError:
            try:
                shutil.copy2(path, f"{path}.corrupt-{int(time.time())}")
            except OSError:
                pass
            return copy.deepcopy(DEFAULTS)
        except OSError:
            return copy.deepcopy(DEFAULTS)
        if not isinstance(data, dict):
            return copy.deepcopy(DEFAULTS)
        merged = copy.deepcopy(DEFAULTS)
        for key, default in DEFAULTS.items():
            if key in data and type(data[key]) is type(default) or (key in data and default is None):
                merged[key] = data[key]
        # a queue or mirror of the wrong inner shape falls back to the default shape
        if not all(isinstance(merged["queues"].get(k), list) for k in ("summaries", "screentime")):
            merged["queues"] = copy.deepcopy(DEFAULTS["queues"])
        if not isinstance(merged["mirror"], dict):
            merged["mirror"] = copy.deepcopy(DEFAULTS["mirror"])
        return merged

    def _ensure(self):
        if self._data is None:
            self._data = self._read()
        return self._data

    def reload(self):
        with self._lock:
            self._data = None
            return self._ensure()

    def _write(self):
        path = self.path
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self._data, f, indent=1)
        os.replace(tmp, path)

    def get(self, key, default=None):
        with self._lock:
            return copy.deepcopy(self._ensure().get(key, default))

    def snapshot(self):
        with self._lock:
            return copy.deepcopy(self._ensure())

    def set(self, **values):
        with self._lock:
            data = self._ensure()
            changed = False
            for key, value in values.items():
                if data.get(key) != value:
                    data[key] = copy.deepcopy(value)
                    changed = True
            if changed:
                self._write()

    def update(self, mutator):
        """Read-modify-write under the lock. mutator(data) edits the dict in place."""
        with self._lock:
            data = self._ensure()
            before = json.dumps(data, sort_keys=True)
            mutator(data)
            if json.dumps(data, sort_keys=True) != before:
                self._write()

    def clear_account(self):
        """Forget everything about the account and what its guides sent (sign-out, or a different account). Notices
        stay: the student can still read what happened. Upload marks go, so a new account never inherits them."""
        def wipe(data):
            keep_notices = data.get("notices", [])
            fresh = copy.deepcopy(DEFAULTS)
            fresh["notices"] = keep_notices
            data.clear()
            data.update(fresh)
        self.update(wipe)

    # ---- small helpers the sync uses ----
    def add_notice(self, notice, now_ms):
        """Remember something a guide did (or a link change) for the student to read. Returns the stored notice."""
        entry = {"id": "n_" + uuid.uuid4().hex[:12],
                 "atMs": int(now_ms), "kind": notice.get("kind", "info"), "text": str(notice.get("text", ""))[:600],
                 "by": notice.get("by"), "url": notice.get("url"), "read": False,
                 "silent": bool(notice.get("silent"))}

        def add(data):
            data["notices"] = (data["notices"] + [entry])[-MAX_NOTICES:]
        self.update(add)
        return entry

    def mark_notices_read(self):
        def mark(data):
            for n in data["notices"]:
                n["read"] = True
        self.update(mark)

    def log_sent(self, kind, items, now_ms, **extra):
        entry = {"atMs": int(now_ms), "kind": kind, "count": len(items) if isinstance(items, list) else 1,
                 "items": (items[:40] if isinstance(items, list) else [items]), **extra}

        def add(data):
            data["sentLog"] = (data["sentLog"] + [entry])[-MAX_SENT_LOG:]
        self.update(add)

    def mark_processed(self, command_id):
        def add(data):
            if command_id not in data["processed"]:
                data["processed"] = (data["processed"] + [command_id])[-MAX_PROCESSED:]
        self.update(add)

    def queue_ack(self, command_id, applied):
        def add(data):
            if not any(a.get("id") == command_id for a in data["pendingAcks"]):
                data["pendingAcks"] = (data["pendingAcks"] + [{"id": command_id, "applied": bool(applied)}])[-200:]
        self.update(add)


_default = FamilyStore()


def default():
    """The one store the app uses (tests make their own with a temp path, or patch PATH)."""
    return _default
