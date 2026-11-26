"""The Supabase transport: the family features against the real Supabase project (ref wwkoiwegmgweblyaxmdw).

Same FamilyTransport interface and the same return shapes as family_rest.py, so family_sync.py, the store and the UI
do not know which backend they talk to. It is a port of the CarmenLinked JS adapter (carmenlinked/client/src: supabase.js,
errors.js, routes/*.js, docs/SUPABASE.md): every call is either a GoTrue request or a PostgREST request made with the
STUDENT'S OWN access token, so Row Level Security (not this code) decides what is visible and writable.

  sign-in            POST {url}/auth/v1/token?grant_type=password      (never /signup: accounts are not created here)
  renewal            POST {url}/auth/v1/token?grant_type=refresh_token (single-flight; the token is spent on every use)
  sign-out           POST {url}/auth/v1/logout?scope=local|global
  everything else    POST {url}/rest/v1/rpc/<function>  (the SQL functions of carmenlinked/supabase/*.sql, which answer
                     in the dev backend's JSON, so the rest of the app needs no second set of shapes), and
                     GET  {url}/rest/v1/devices_registry (the student's own devices, for GET /me).

Writes that the schema does not let a client make directly (student_status, session_summaries, screentime_samples,
heartbeats) go through the SQL functions me_push_status / me_push_summaries / me_push_screentime / me_heartbeat. The
status and summary functions store the desktop's own sync_records shapes ('live_session', 'session_history') and give each
write an updated_at strictly greater than any earlier one of that user (push_sync(), aware UTC), so a puller that
remembers the newest updated_at cannot miss one; this client sends the shapes (toSessionHistory below) and the server
owns the clock and the ordering. Deleting a summary is not something the desktop does (no tombstones are sent).

Auth settings are never touched and sign-up is never called. The publishable key (sb_publishable_...) only identifies
the project and is public by design; it travels in the `apikey` header with every request.

Differences from family_rest.py, all deliberate:
  * Refresh: Supabase rotates the refresh token on every use and tolerates a re-use for about 10 seconds only (the
    dev backend's 60-second same-device grace does not exist), so a crash between "server spent the token" and "new
    one saved" is survivable only if the app restarts within that window; otherwise the student signs in again.
    Concurrent refreshes in this process share one (single-flight).
  * ETag: PostgREST answers RPC calls with POST and no ETag, so nothing is cached with If-None-Match here; the sync
    pass already runs only every 15 s and compares what it fetched.
  * https is required unless the project URL is on this computer (a local test server).
"""
import base64
import json
import logging
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid as uuid_mod
from email.utils import parsedate_to_datetime

from family_rest import KeyringTokenStore, MemoryTokenStore, normalize_base      # noqa: F401  (re-exported for callers and tests)
from family_rules import iso_from_ms, parse_iso_ms
from family_transport import FamilyError, FamilyTransport

logger = logging.getLogger("carmen_family")

SUPABASE_PROJECT_REF = "wwkoiwegmgweblyaxmdw"
DEFAULT_URL = f"https://{SUPABASE_PROJECT_REF}.supabase.co"
DEFAULT_KEY = "sb_publishable_6TwTWF_E7TjqOckpgEC1vQ_WuSkwxsi"     # public by design: it only identifies the project
KEYRING_USERNAME = "family_refresh_token_supabase"                 # its own entry: a dev-backend token is not a GoTrue token
REQUEST_TIMEOUT_SECONDS = 8.0
EARLY_REFRESH_MS = 2 * 60 * 1000
REFUSED_REFRESH_CODES = ("refresh_reused", "invalid_refresh", "refresh_expired")

# The tables and SQL functions this client uses (a test checks each against carmenlinked/supabase/*.sql).
SUPABASE_TABLES = ("devices_registry",)
SUPABASE_RPCS = (
    "my_profile", "set_my_profile", "my_links", "me_heartbeat", "me_push_status", "me_push_summaries", "me_push_screentime",
    "me_push_violations", "my_commands", "ack_command", "my_rules", "my_schedule_json", "my_class_memberships", "add_schedule_skip",
    "my_notifications", "read_all_notifications", "my_audit_page", "my_shared_log_page", "revoke_link_json",
    "redeem_invite_json", "create_request", "my_requests",
)

_UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)
_HOST_RE = re.compile(r"^(?=.{1,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z][a-z0-9-]{1,62}$")
_DAY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_ISO_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T")


class SupabaseTokenStore(KeyringTokenStore):
    """The refresh token in the OS credential store under its own entry name."""

    def __init__(self):
        super().__init__(username=KEYRING_USERNAME)


# ---- small helpers ported from the adapter (client/vendor/util.js, categories.js, timewin.js) ---------------------

def _bad(field, message=None):
    return FamilyError("validation", message or f"{field} is not valid", 400, body={"field": field})


def norm_host(value):
    """A bare lower-case hostname with no www., or None (no scheme, path, port, query, space, IP address)."""
    if not isinstance(value, str):
        return None
    text = value.strip().lower()
    if text.startswith("www."):
        text = text[4:]
    return text if _HOST_RE.match(text) else None


def is_iso(value):
    return isinstance(value, str) and len(value) <= 40 and bool(_ISO_RE.match(value)) and parse_iso_ms(value) is not None


def iso_utc(value):
    """Any ISO-8601 timestamp as 2026-11-23T10:00:00.000Z (like Date.toISOString())."""
    return iso_from_ms(parse_iso_ms(value))


def _is_int(value, lo, hi):
    return isinstance(value, int) and not isinstance(value, bool) and lo <= value <= hi


def _opt_int(obj, key, lo, hi):
    value = obj.get(key)
    if value is None:
        return None
    if not _is_int(value, lo, hi):
        raise _bad(key, f"{key} must be an integer {lo}..{hi}")
    return value


def _text(obj, key, lo=0, hi=500, required=True):
    value = obj.get(key)
    if value is None:
        if required:
            raise _bad(key, f"{key} is required")
        return None
    if not isinstance(value, str):
        raise _bad(key, f"{key} must be a string")
    value = value.strip()
    if len(value) < lo or len(value) > hi:
        raise _bad(key, f"{key} must be {lo}-{hi} characters")
    return value


def _one_of(obj, key, options, required=True):
    value = obj.get(key)
    if value is None:
        if required:
            raise _bad(key, f"{key} is required")
        return None
    if not isinstance(value, str) or value not in options:
        raise _bad(key, f"{key} must be one of {'|'.join(options)}")
    return value


def _iso_field(obj, key, required=False):
    value = obj.get(key)
    if value is None:
        if required:
            raise _bad(key, f"{key} is required")
        return None
    if not is_iso(value):
        raise _bad(key, f"{key} must be an ISO-8601 timestamp")
    return iso_utc(value)


def _clean(mapping):
    return {k: v for k, v in mapping.items() if v is not None}


_CATEGORIES = None


def _category_table():
    global _CATEGORIES
    if _CATEGORIES is None:
        table = {}
        try:
            path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "family_site_categories.json")
            with open(path, "r", encoding="utf-8") as handle:
                for category, hosts in json.load(handle).items():
                    for host in hosts:
                        table[host] = category
        except (OSError, ValueError):
            logger.warning("family_site_categories.json is missing; screen time is filed as Other")
        _CATEGORIES = table
    return _CATEGORIES


def category_of(hostname):
    """The family-facing bucket of a hostname (the adapter's categoryOf: subdomains fall back to the parent domain)."""
    host = str(hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    parts = host.split(".")
    table = _category_table()
    for i in range(len(parts) - 1):
        found = table.get(".".join(parts[i:]))
        if found:
            return found
    return "Other"


def validate_summary(s, index):
    """One finished session, validated like validateSummary() in the adapter's routes/student-data.js."""
    if not isinstance(s, dict):
        raise _bad(f"[{index}]", f"item {index} must be an object")
    o = {"id": _text(s, "id", 1, 64), "startedAt": _iso_field(s, "startedAt", True), "endedAt": _iso_field(s, "endedAt", True)}
    if parse_iso_ms(o["endedAt"]) < parse_iso_ms(o["startedAt"]):
        raise _bad("endedAt", f"item {index}: endedAt is before startedAt")
    o["plannedMin"] = _need_int(s, "plannedMin", 0, 1440)
    o["focusedMin"] = _need_int(s, "focusedMin", 0, 1440)
    o["taskName"] = _text(s, "taskName", 0, 80, required=False) or ""
    task_id = _text(s, "taskId", 0, 64, required=False)
    if task_id:
        o["taskId"] = task_id
    o["lockMode"] = _one_of(s, "lockMode", ("soft", "hard"))
    o["endType"] = _one_of(s, "endType", ("completed", "ended_early", "nuclear"))
    o["violations"] = _opt_int(s, "violations", 0, 100000) or 0
    pauses = s.get("pauses", {})
    if not isinstance(pauses, dict):
        raise _bad("pauses")
    o["pauses"] = {"free": _opt_int(pauses, "free", 0, 1000) or 0, "checking": _opt_int(pauses, "checking", 0, 1000) or 0,
                   "minutes": _opt_int(pauses, "minutes", 0, 1440) or 0}
    if "pauseMin" in s:
        pm = s["pauseMin"]
        if not isinstance(pm, dict):
            raise _bad("pauseMin")
        o["pauseMin"] = {"free": _opt_int(pm, "free", 0, 1440) or 0, "checking": _opt_int(pm, "checking", 0, 1440) or 0}
    o["source"] = _one_of(s, "source", ("desktop", "extension"))
    reason = _text(s, "reason", 0, 500, required=False)
    if reason:
        o["reason"] = reason
    o["unblocks"] = _opt_int(s, "unblocks", 0, 1000) or 0
    if "violationHosts" in s:
        o["violationHosts"] = _host_list(s["violationHosts"], "violationHosts", 100)
    if "unblockPlans" in s:
        plans = s["unblockPlans"]
        if not isinstance(plans, list) or len(plans) > 20:
            raise _bad("unblockPlans")
        out = []
        for p in plans:
            if not isinstance(p, dict):
                raise _bad("unblockPlans")
            host = norm_host(p.get("host"))
            if not host:
                raise FamilyError("invalid_hostname", "unblockPlans[].host must be a bare hostname", 400)
            out.append({"host": host, "reason": _text(p, "reason", 0, 300, required=False) or "",
                        "plan": _text(p, "plan", 0, 300, required=False) or "", "minutes": _opt_int(p, "minutes", 0, 240) or 0})
        o["unblockPlans"] = out
    cs = _text(s, "classSessionId", 0, 64, required=False)
    if cs:
        o["classSessionId"] = cs
    return o


def _need_int(obj, key, lo, hi):
    if obj.get(key) is None:
        raise _bad(key, f"{key} is required")
    return _opt_int(obj, key, lo, hi)


def _host_list(value, key, limit):
    if not isinstance(value, list) or len(value) > limit:
        raise _bad(key, f"{key} must be an array of at most {limit} hostnames")
    out = []
    for item in value:
        host = norm_host(item)
        if not host:
            raise FamilyError("invalid_hostname", f"{key} must contain bare hostnames only (no scheme, path or port)", 400)
        if host not in out:
            out.append(host)
    return out


def to_session_history(o):
    """The desktop's own `session_history` record for a validated summary (what the projection trigger reads)."""
    pm = o.get("pauseMin") or {}
    out = {
        "startTime": o["startedAt"], "endTime": o["endedAt"],
        "endType": "manual" if o["endType"] == "ended_early" else o["endType"],
        "lockMode": o["lockMode"], "source": o["source"], "eventTitle": o["taskName"], "violationCount": o["violations"],
        "pauseSummary": {"free_seconds": (pm.get("free") or 0) * 60, "escape_seconds": (pm.get("checking") or 0) * 60},
        "pauseCounts": {"free": o["pauses"]["free"], "checking": o["pauses"]["checking"]}, "pauseMinutes": o["pauses"]["minutes"],
        "plannedMin": o["plannedMin"], "focusedMin": o["focusedMin"], "unblockCount": o["unblocks"],
        "taskId": o.get("taskId"), "classSessionId": o.get("classSessionId"), "reason": o.get("reason"),
        "domainWhitelistAdditions": o.get("unblockPlans"),
    }
    if o.get("violationHosts") is not None:
        out["violationLog"] = [{"url": f"https://{h}/"} for h in o["violationHosts"]]
    return _clean(out)


SUMMARY_FIELDS = ["id", "startedAt", "endedAt", "plannedMin", "focusedMin", "taskName", "lockMode", "endType", "violations", "pauses",
                  "source", "reason*", "unblockPlans*", "violationHosts*", "(* only shown to guides you gave detail)"]


def status_data(b):
    """-> (data, fields): the desktop's `live_session` shape and the names of the fields sent (logged for the student)."""
    if not isinstance(b, dict):
        raise FamilyError("invalid_body", "Request body must be a JSON object", 400)
    st = {"state": _one_of(b, "state", ("live", "paused", "break", "idle", "offline"))}
    task = _text(b, "taskName", 0, 80, required=False)
    if task:
        st["taskName"] = task
    lock = _one_of(b, "lockMode", ("soft", "hard"), required=False)
    if lock:
        st["lockMode"] = lock
    started = _iso_field(b, "startedAt")
    if started:
        st["startedAt"] = started
    ends = _iso_field(b, "endsAt")
    if ends:
        st["endsAt"] = ends
    paused = b.get("pausedMin")
    if paused is not None:
        if isinstance(paused, bool) or not isinstance(paused, (int, float)) or not 0 <= paused <= 1440:
            raise _bad("pausedMin")
        st["pausedMin"] = paused
    violations = _opt_int(b, "violations", 0, 100000)
    if violations is not None:
        st["violations"] = violations
    state = st["state"]
    data = _clean({
        "isActive": state in ("live", "paused", "break"), "isPaused": state == "paused", "isBreak": state == "break",
        "lockMode": st.get("lockMode"), "startTime": st.get("startedAt"), "endTime": st.get("endsAt"), "eventTitle": st.get("taskName"),
        "pausedMin": st.get("pausedMin"), "state": state, "violations": st.get("violations")})
    return data, list(st.keys())


def _overlap(w1, w2):
    """The adapter's timewin.overlap(): the longest overlap of two weekly windows on a circular week of UTC minutes."""
    week = 10080

    def to_min(t):
        return int(t[:2]) * 60 + int(t[3:5])

    def span(w):
        a = (w["dow"] * 1440 + to_min(w["start"]) - (w.get("tzOffsetMin") or 0)) % week
        return a, a + (to_min(w["end"]) - to_min(w["start"]))

    a1, b1 = span(w1)
    a2, b2 = span(w2)
    best = None
    for shift in (-week, 0, week):
        a, b = max(a1, a2 + shift), min(b1, b2 + shift)
        if b > a and (best is None or b - a > best[1] - best[0]):
            best = (a, b)
    if best is None:
        return None
    local_start = (best[0] + (w1.get("tzOffsetMin") or 0)) % week
    dow, m = local_start // 1440, local_start % 1440

    def fmt(x):
        return f"{(x // 60) % 24:02d}:{x % 60:02d}"
    return {"dow": dow, "start": fmt(m), "end": fmt(m + best[1] - best[0]), "minutes": best[1] - best[0], "tzOffsetMin": w1.get("tzOffsetMin") or 0}


def compute_conflicts(blocks, classes):
    """The adapter's computeConflicts(): overlaps between two different guides' study blocks and class windows."""
    items, names = [], {}
    for b in blocks:
        names[b["guideId"]] = b.get("guideName")
        items.append({"id": b["id"], "source": "block", "guideId": b["guideId"], "title": b.get("title"), "dow": b["dow"], "start": b["start"],
                      "end": b["end"], "tzOffsetMin": b.get("tzOffsetMin") or 0, "lockMode": b.get("lockMode"), "allowlist": b.get("allowlist")})
    for c in classes:
        names[c["guide"]["id"]] = c["guide"].get("displayName")
        for i, w in enumerate(c.get("windows") or []):
            items.append({"id": f"{c['classId']}#{i}", "source": "class", "guideId": c["guide"]["id"], "title": c.get("name"), "dow": w["dow"],
                          "start": w["start"], "end": w["end"], "tzOffsetMin": c.get("tzOffsetMin") or 0, "lockMode": None, "allowlist": None})
    out = []
    for i in range(len(items)):
        for j in range(i + 1, len(items)):
            a, b = items[i], items[j]
            if a["guideId"] == b["guideId"]:
                continue
            o = _overlap(a, b)
            if not o:
                continue

            def view(x):
                return {"id": x["id"], "source": x["source"], "guideId": x["guideId"], "guideName": names.get(x["guideId"]) or "Unknown",
                        "title": x["title"], "dow": x["dow"], "start": x["start"], "end": x["end"], "lockMode": x["lockMode"]}
            out.append({"a": view(a), "b": view(b), "overlap": o,
                        "lockModesDiffer": bool(a["lockMode"] and b["lockMode"] and a["lockMode"] != b["lockMode"]),
                        "allowlistsDiffer": bool(a["allowlist"] and b["allowlist"] and sorted(a["allowlist"]) != sorted(b["allowlist"]))})
    out.sort(key=lambda x: (x["overlap"]["dow"], x["overlap"]["start"]))
    return out


# ---- error translation (client/src/errors.js) -------------------------------------------------------------------

def _upstream(detail=""):
    return FamilyError("upstream_error", detail or "The Supabase project could not be reached or answered with an error.", 502, offline=True)


def _from_raise(message):
    m = str(message or "")
    table = (
        (r"^(not allowed|no_access)", 403, "no_access", "No access"),
        (r"^not found", 404, "not_found", "Not found"),
        (r"^revoked", 409, "revoked", "This link was revoked"),
        (r"^used", 409, "used", "That invite was already used"),
        (r"^not pending", 409, "not_pending", "That request is not pending"),
        (r"^too many open invites", 429, "too_many_invites", "Too many open invites. Wait for them to expire."),
        (r"^device_taken", 409, "device_taken", "That deviceId belongs to another account"),
        (r"^not_linked", 400, "not_linked", "You are not linked to that guide"),
        (r"^too_many_requests", 429, "too_many_requests", "You have 10 requests waiting. Wait for a reply first."),
        (r"^too_many_blocks", 429, "too_many_blocks", "Block limit reached"),
        (r"^already_skipped", 409, "already_skipped", "That block is already skipped for that day"),
        (r"^auth required", 401, "unauthorized", "Missing or invalid token"),
    )
    for pattern, status, code, text in table:
        if re.match(pattern, m):
            return FamilyError(code, text, status)
    if re.match(r"^forbidden", m):
        return FamilyError("forbidden", re.sub(r"^forbidden:\s*", "", m) or "Not allowed", 403)
    if re.match(r"^(bad arguments|bad scopes)", m):
        return _bad("body", "A value is not allowed")
    return _bad("body", m or "The request was not accepted")


def error_from_auth(status, body, purpose, retry_after=None):
    """A GoTrue failure as the dev backend's error vocabulary. `purpose` is 'login' | 'refresh' | 'logout'."""
    b = body if isinstance(body, dict) else {}
    code = b.get("error_code") or (b.get("code") if isinstance(b.get("code"), str) else "") or (b.get("error") if isinstance(b.get("error"), str) else "") or ""
    msg = b.get("msg") or b.get("message") or b.get("error_description") or ""
    if status == 0 or status >= 500:
        return _upstream()
    if status == 429 or "rate_limit" in code:
        return FamilyError("rate_limited", "Too many attempts. Try again later.", 429, retry_after=retry_after or 60, body=b)
    simple = {
        "invalid_credentials": (401, "invalid_credentials", "Email or password is not right"),
        "email_not_confirmed": (401, "email_not_confirmed", "Confirm your email address first: open the link Supabase sent you, then sign in."),
        "refresh_token_already_used": (401, "refresh_reused", "That refresh token was already used. Sign in again."),
        "refresh_token_not_found": (401, "invalid_refresh", "Refresh token is not valid"),
        "user_banned": (401, "unauthorized", "Missing or invalid token"),
        "bad_jwt": (401, "unauthorized", "Missing or invalid token"),
    }
    if code in simple:
        st, c, m = simple[code]
        return FamilyError(c, m, st, body=b)
    if code == "session_expired":
        return FamilyError("refresh_expired" if purpose == "refresh" else "unauthorized", "Refresh token has expired. Sign in again.", 401, body=b)
    if code == "session_not_found":
        return FamilyError("invalid_refresh" if purpose == "refresh" else "unauthorized", "Refresh token is not valid", 401, body=b)
    if code == "validation_failed" and purpose == "refresh":
        return FamilyError("invalid_refresh", "Refresh token is not valid", 401, body=b)
    if status == 401:
        return FamilyError("unauthorized", "Missing or invalid token", 401, body=b)
    if status == 403:
        return FamilyError("forbidden", msg or "Not allowed", 403, body=b)
    if status in (400, 422):
        return _bad("body", msg or "The request was not accepted")
    return _upstream()


def error_from_rest(status, body):
    """A PostgREST / PostgreSQL failure as the dev backend's error vocabulary."""
    b = body if isinstance(body, dict) else {}
    code = b.get("code") if isinstance(b.get("code"), str) else ""
    message = b.get("message") if isinstance(b.get("message"), str) else ""
    if status == 0 or status >= 500:
        return _upstream()
    if status == 401 or code in ("PGRST301", "PGRST302", "PGRST303"):
        return FamilyError("unauthorized", "Missing or invalid token", 401, body=b)
    if code == "42501":
        return FamilyError("no_access", "No access", 403)
    if code == "PGRST116":
        return FamilyError("not_found", "Not found", 404)
    if code in ("PGRST202", "PGRST205", "42883", "42P01", "42703"):
        return FamilyError("schema_out_of_date", f"The database is missing something this client needs ({message}).", 503, offline=True)
    if code in ("22P02", "22007", "22023"):
        return _bad("id", "A value has the wrong format")
    if code == "23505":
        return FamilyError("conflict", "That already exists", 409)
    if code in ("23514", "23502", "23503"):
        return _bad("body", message or "A value is not allowed")
    if code == "P0001":
        return _from_raise(message)
    if status == 403:
        return FamilyError("no_access", "No access", 403)
    if status == 404:
        return FamilyError("not_found", "Not found", 404)
    if status == 409:
        return FamilyError("conflict", message or "Conflict", 409)
    return _upstream()


def code_error(r):
    """The {error} answer of redeem_invite_json -> a FamilyError (400 invalid|expired|used, 429 rate_limited, 400 validation)."""
    messages = {"invalid": "That code is not valid.", "expired": "That code has expired. Ask for a new one.", "used": "That code has already been used."}
    err = r.get("error")
    if err == "rate_limited":
        return FamilyError("rate_limited", "Too many failed attempts. Try again later.", 429, retry_after=r.get("retryAfterSec") or 600)
    if err == "scopes_exceed_invite":
        return _bad("scopes", "scopes cannot exceed what the invite requested")
    if err == "detail_exceeds_invite":
        return _bad("detail", "detail cannot exceed what the invite requested")
    return FamilyError(str(err or "invalid"), messages.get(err, messages["invalid"]), 400, body=r)


def decode_jwt(token):
    """The claims of a JWT (no signature check: the server checks it), or {}."""
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        data = json.loads(base64.urlsafe_b64decode(payload.encode("ascii")).decode("utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _iso_or_none(value):
    ms = parse_iso_ms(value) if isinstance(value, str) else None
    return iso_from_ms(ms) if ms is not None else value


# ---- the transport ----------------------------------------------------------------------------------------------

class SupabaseFamilyTransport(FamilyTransport):
    name = "supabase"

    def __init__(self, base=DEFAULT_URL, api_key=DEFAULT_KEY, token_store=None, timeout=REQUEST_TIMEOUT_SECONDS):
        self._base = DEFAULT_URL
        self._key = api_key or DEFAULT_KEY
        self._timeout = timeout
        self._tokens = token_store if token_store is not None else SupabaseTokenStore()
        self._lock = threading.RLock()
        self._refresh_lock = threading.Lock()
        self._access = None
        self._access_expires_ms = None
        self._user = None
        self._offset_ms = 0
        self.early_refresh_ms = EARLY_REFRESH_MS
        self.refresh_count = 0
        self.device_id = None                       # accepted for interface parity; GoTrue has no device-aware refresh grace
        self.session_lost = None
        try:
            self._base = normalize_base(base)
        except FamilyError:
            self._base = DEFAULT_URL

    # ---- configuration and state ----
    def configure(self, base, api_key=None):
        new = normalize_base(base)
        with self._lock:
            self._base = new
            if api_key:
                self._key = api_key.strip()
        return new

    @property
    def base(self):
        return self._base

    def server_clock_offset_ms(self):
        return self._offset_ms

    def has_saved_login(self):
        return bool(self._access or self._tokens.get())

    def is_signed_in(self):
        with self._lock:
            if self._access:
                return True
        return bool(self._tokens.get()) and self.session_lost is None

    def user(self):
        with self._lock:
            return dict(self._user) if self._user else None

    def _uid(self):
        with self._lock:
            if self._user and self._user.get("id"):
                return self._user["id"]
            token = self._access
        sub = decode_jwt(token or "").get("sub")
        if not isinstance(sub, str) or not _UUID_RE.match(sub):
            raise FamilyError("signed_out", "Not signed in.", 401)
        return sub

    # ---- sign-in ----
    def login(self, email, password):
        if not isinstance(email, str) or not email.strip() or not isinstance(password, str) or not password:
            raise FamilyError("validation", "Email and password are required.", 400)
        reply = self._auth("token?grant_type=password", {"email": email.strip().lower(), "password": password}, "login")
        self._take_session(reply)
        self.session_lost = None
        try:
            self._load_user(reply)
        except FamilyError:
            self.forget()
            raise
        return self.user()

    def logout(self, everywhere=False):
        try:
            if self._access:
                self._auth(f"logout?scope={'global' if everywhere else 'local'}", None, "logout", bearer=self._access, timeout=3.0, expect_body=False)
        except FamilyError:
            pass                                       # a sign-out has to work offline
        self.forget()

    def forget(self):
        with self._lock:
            self._access = None
            self._access_expires_ms = None
            self._user = None
        self._tokens.clear()

    def _take_session(self, reply):
        if not isinstance(reply, dict) or not isinstance(reply.get("access_token"), str) or not reply["access_token"]:
            raise FamilyError("malformed", "The server's sign-in reply was not understood.", offline=True)
        with self._lock:
            self._access = reply["access_token"]
            expires_at = reply.get("expires_at")
            if isinstance(expires_at, (int, float)) and not isinstance(expires_at, bool):
                self._access_expires_ms = int(expires_at * 1000)
            else:
                self._access_expires_ms = self._now_ms() + int(reply.get("expires_in") or 3600) * 1000
            user = reply.get("user")
            if isinstance(user, dict) and isinstance(user.get("id"), str) and self._user is None:
                self._user = {"id": user["id"], "email": str(user.get("email") or ""), "displayName": ""}
        refresh = reply.get("refresh_token")
        if isinstance(refresh, str) and refresh:
            self._tokens.set(refresh)

    def _load_user(self, reply):
        """The user object, like the adapter's loadUser(): the profile row (created from the sign-up metadata)."""
        auth_user = reply.get("user") if isinstance(reply.get("user"), dict) else {}
        profile = self.rpc("my_profile", {}, retry=False)
        if not profile:
            meta = auth_user.get("user_metadata") if isinstance(auth_user.get("user_metadata"), dict) else {}
            fallback = str(meta.get("display_name") or (auth_user.get("email") or "someone").split("@")[0])[:60] or "Someone"
            year = meta.get("birth_year")
            profile = self.rpc("set_my_profile", {"p_display_name": fallback, "p_birth_year": year if isinstance(year, int) else None}, retry=False)
        with self._lock:
            uid = str(auth_user.get("id") or (self._user or {}).get("id") or decode_jwt(self._access or "").get("sub") or "")
            self._user = {"id": uid, "email": str(auth_user.get("email") or (self._user or {}).get("email") or ""),
                          "displayName": str((profile or {}).get("displayName") or "")}

    # ---- refreshing ----
    def ensure_fresh(self):
        with self._lock:
            token, expires = self._access, self._access_expires_ms
        if not token:
            if self._tokens.get() and self.session_lost is None:
                return self.refresh(None)
            return False
        if expires is None or expires - self._now_ms() > self.early_refresh_ms:
            return False
        return self.refresh(token)

    def refresh(self, stale):
        """Swap the refresh token for a new pair (single-flight; see family_rest.FamilyRest.refresh)."""
        with self._refresh_lock:
            with self._lock:
                if stale is not None and self._access is not None and self._access != stale:
                    return True                      # another caller refreshed while we waited
            refresh_token = self._tokens.get()
            if not refresh_token:
                with self._lock:
                    self._access = None
                raise FamilyError("session_lost", "Not signed in.", 401)
            try:
                reply = self._auth("token?grant_type=refresh_token", {"refresh_token": refresh_token}, "refresh")
            except FamilyError as err:
                if err.code in REFUSED_REFRESH_CODES:
                    self.session_lost = err.code
                    self.forget()
                    raise FamilyError("session_lost", err.code, 401) from err
                raise                                # unreachable or a hiccup: keep every token, try again later
            self.refresh_count += 1
            self._take_session(reply)
            return True

    # ---- HTTP ----
    @staticmethod
    def _now_ms():
        return int(time.time() * 1000)

    def _send(self, method, path, body, headers, timeout):
        """One HTTP exchange -> (status, parsed JSON or None, response headers, raw bytes). Network failures raise offline."""
        data = None
        h = {"apikey": self._key, "Accept": "application/json"}
        h.update(headers)
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            h["Content-Type"] = "application/json"
        request = urllib.request.Request(self._base + path, data=data, headers=h, method=method)
        try:
            with urllib.request.urlopen(request, timeout=timeout or self._timeout) as response:
                status, raw, resp_headers = response.status, response.read(), response.headers
        except urllib.error.HTTPError as err:
            status, resp_headers = err.code, err.headers
            try:
                raw = err.read()
            except Exception:
                raw = b""
        except (urllib.error.URLError, OSError, ValueError, TimeoutError) as err:
            raise FamilyError("offline", str(getattr(err, "reason", err)), 0, offline=True) from err
        except Exception as err:
            raise FamilyError("offline", str(err), 0, offline=True) from err
        self._learn_clock(resp_headers)
        parsed = None
        if raw:
            try:
                parsed = json.loads(raw.decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                parsed = None
        return status, parsed, resp_headers, raw

    def _auth(self, path, body, purpose, bearer=None, timeout=None, expect_body=True):
        headers = {"Authorization": f"Bearer {bearer}"} if bearer else {}
        status, parsed, resp_headers, raw = self._send("POST", "/auth/v1/" + path, body, headers, timeout)
        if status >= 400:
            retry_after = None
            try:
                retry_after = max(1, int(float(resp_headers.get("Retry-After"))))
            except (TypeError, ValueError):
                pass
            raise error_from_auth(status, parsed, purpose, retry_after)
        if expect_body and not isinstance(parsed, dict):
            raise FamilyError("malformed", "The server's reply was not understood.", status, offline=True)
        return parsed

    def _rest(self, method, path, body=None, timeout=None, retry=True):
        """A PostgREST call with the user's access token. A 401 renews the token once and retries once."""
        with self._lock:
            token = self._access
        if not token:
            if self._tokens.get() and self.session_lost is None:
                self.refresh(None)
                with self._lock:
                    token = self._access
            if not token:
                raise FamilyError("signed_out", "Not signed in.", 401)
        status, parsed, _headers, raw = self._send(method, path, body, {"Authorization": f"Bearer {token}"}, timeout)
        if (status == 401 or (isinstance(parsed, dict) and parsed.get("code") in ("PGRST301", "PGRST302", "PGRST303"))) and retry:
            self.refresh(token)                      # raises session_lost / offline
            return self._rest(method, path, body, timeout, retry=False)
        if status >= 400:
            raise error_from_rest(status, parsed)
        if parsed is None and raw:
            raise FamilyError("malformed", "The server's reply was not JSON.", status, offline=True)
        return parsed

    def rpc(self, name, args=None, retry=True):
        return self._rest("POST", f"/rest/v1/rpc/{name}", args if args is not None else {}, retry=retry)

    def _learn_clock(self, headers):
        try:
            when = parsedate_to_datetime(headers.get("Date"))
            self._offset_ms = int(when.timestamp() * 1000 + 500) - self._now_ms()
        except Exception:
            pass

    # ---- the calls (see FamilyTransport) ----
    def me(self):
        uid = self._uid()
        profile = self.rpc("my_profile")
        links = self.rpc("my_links")
        query = urllib.parse.quote("device_id,kind,version,enforcing,rules_state,session_active,last_seen", safe=",")
        devices = self._rest("GET", f"/rest/v1/devices_registry?select={query}&user_id=eq.{uid}&revoked=eq.false&order=last_seen.desc.nullslast")
        if not isinstance(links, dict) or not isinstance(devices, list):
            raise FamilyError("malformed", "The server's reply was not understood.", offline=True)
        now_ms = self._now_ms()
        claims = decode_jwt(self._access or "")
        user = self.user() or {}
        as_student = []
        for link in links.get("asStudent") or []:
            link = dict(link)
            expires = parse_iso_ms(link.get("expiresAt")) if link.get("expiresAt") else None
            if link.get("status") == "active" and expires is not None and expires <= now_ms:
                link["expired"] = True
            as_student.append(link)
        return {
            "user": {"id": uid, "email": str(claims.get("email") or user.get("email") or ""),
                     "displayName": str((profile or {}).get("displayName") or user.get("displayName") or ""),
                     "birthYear": (profile or {}).get("birthYear")},
            "asStudent": {"links": as_student, "devices": [
                {"deviceId": d.get("device_id"), "kind": d.get("kind"), "version": d.get("version"), "enforcing": d.get("enforcing"),
                 "rules": d.get("rules_state"), "sessionActive": d.get("session_active"), "lastSeenAt": _iso_or_none(d.get("last_seen"))}
                for d in devices if isinstance(d, dict)]},
            "asGuide": {"students": []},
        }

    def heartbeat(self, payload):
        if not isinstance(payload, dict):
            raise FamilyError("invalid_body", "Request body must be a JSON object", 400)
        device = _text(payload, "deviceId", 1, 64)
        kind = _one_of(payload, "kind", ("desktop", "extension", "web"))
        version = _text(payload, "version", 0, 32, required=False) or ""
        enforcing, session = payload.get("enforcing"), payload.get("sessionActive")
        if not isinstance(enforcing, bool) or not isinstance(session, bool):
            raise _bad("enforcing", "enforcing and sessionActive must be booleans")
        rules = _one_of(payload, "rules", ("ok", "stale", "none"))
        return self.rpc("me_heartbeat", {"p_device": device, "p_kind": kind, "p_version": version, "p_enforcing": enforcing,
                                         "p_rules": rules, "p_session_active": session})

    def post_status(self, payload):
        data, fields = status_data(payload)
        self.rpc("me_push_status", {"p_data": data, "p_fields": fields})
        return {"ok": True}

    def post_summaries(self, items):
        if not isinstance(items, list) or len(items) > 200:
            raise FamilyError("invalid_body", "Request body must be a JSON array of at most 200 items", 400)
        done = [validate_summary(s, i) for i, s in enumerate(items)]
        reply = self.rpc("me_push_summaries", {"p_items": [{"id": o["id"], "data": to_session_history(o)} for o in done], "p_fields": SUMMARY_FIELDS})
        return {"accepted": (reply or {}).get("accepted", len(done))}

    def post_screentime(self, items):
        if not isinstance(items, list) or len(items) > 5000:
            raise FamilyError("invalid_body", "Request body must be a JSON array of at most 5000 items", 400)
        now_ms = self._now_ms() + self._offset_ms
        today = iso_from_ms(now_ms)[:10]
        rows = []
        for i, x in enumerate(items):
            if not isinstance(x, dict):
                raise _bad(f"[{i}]", f"item {i} must be an object")
            day = x.get("day")
            if not isinstance(day, str) or not _DAY_RE.match(day) or parse_iso_ms(day + "T00:00:00Z") is None:
                raise _bad("day", "day must be YYYY-MM-DD")
            day_ms, today_ms = parse_iso_ms(day + "T00:00:00Z"), parse_iso_ms(today + "T00:00:00Z")
            if day_ms > today_ms + 86400000 or day_ms < today_ms - 90 * 86400000:
                raise _bad("day", f"item {i}: day is outside the accepted range")
            host = norm_host(x.get("hostname"))
            if not host:
                raise FamilyError("invalid_hostname", f"item {i}: hostname must be a bare hostname (no scheme, path, port or query)", 400)
            seconds = x.get("seconds")
            if not _is_int(seconds, 1, 86400):
                raise _bad("seconds", "seconds must be an integer 1..86400")
            at = _iso_field(x, "at") or (iso_from_ms(now_ms) if day == today else f"{day}T12:00:00.000Z")
            rows.append({"day": day, "hostname": host, "seconds": seconds, "at": at, "category": category_of(host)})
        reply = self.rpc("me_push_screentime", {"p_rows": rows})
        return {"accepted": (reply or {}).get("accepted", len(rows))}

    def post_violations(self, items):
        """POST /me/violations [{at, hostname}] (not used by family_sync yet; ported for completeness)."""
        if not isinstance(items, list) or len(items) > 500:
            raise FamilyError("invalid_body", "Request body must be a JSON array of at most 500 items", 400)
        rows = []
        for i, x in enumerate(items):
            host = norm_host(x.get("hostname")) if isinstance(x, dict) else None
            if not host:
                raise FamilyError("invalid_hostname", f"item {i}: hostname must be a bare hostname (never a full URL)", 400)
            rows.append({"at": _iso_field(x, "at", True), "hostname": host})
        reply = self.rpc("me_push_violations", {"p_rows": rows})
        return {"accepted": (reply or {}).get("accepted", len(rows))}

    def commands(self):
        reply = self.rpc("my_commands", {"p_since": None})
        if not isinstance(reply, list):
            raise FamilyError("malformed", "The server's reply was not understood.", offline=True)
        return reply

    def ack_command(self, command_id, applied):
        cid = str(command_id)
        if not _UUID_RE.match(cid):
            raise FamilyError("not_found", "No such command", 404)
        try:
            return self.rpc("ack_command", {"p_id": cid, "p_applied": bool(applied)})
        except FamilyError as err:
            if err.code == "no_access":              # RLS cannot tell a stranger's id from an unknown one
                raise FamilyError("not_found", "No such command", 404) from err
            raise

    def rules(self):
        return self.rpc("my_rules")

    def schedule(self):
        reply = self.rpc("my_schedule_json")
        if not isinstance(reply, dict):
            raise FamilyError("malformed", "The server's reply was not understood.", offline=True)
        return {"blocks": reply.get("blocks")}

    def schedule_conflicts(self):
        s = self.rpc("my_schedule_json")
        classes = self.rpc("my_class_memberships")
        if not isinstance(s, dict) or not isinstance(s.get("blocks"), list) or not isinstance(classes, list):
            raise FamilyError("malformed", "The server's reply was not understood.", offline=True)
        try:
            conflicts = compute_conflicts(s["blocks"], classes)
        except (KeyError, TypeError, ValueError) as err:
            raise FamilyError("malformed", "The schedule was not understood.", offline=True) from err
        return {"conflicts": conflicts, "count": len(conflicts)}

    def classes(self):
        return self.rpc("my_class_memberships")

    def post_schedule_skip(self, block_id, reason, day):
        bid = str(block_id).lower()
        if not _UUID_RE.match(bid):
            raise FamilyError("not_found", "No such study block", 404)
        try:
            return self.rpc("add_schedule_skip", {"p_block": bid, "p_reason": str(reason or "")[:200] or "skipped", "p_day": day})
        except FamilyError as err:
            if err.code == "not_found":
                raise FamilyError("not_found", "No such study block", 404) from err
            raise

    def notifications(self, limit=50):
        n = max(1, min(200, int(limit)))
        r = self.rpc("my_notifications", {"p_unread": False, "p_limit": n, "p_off": 0})
        if not isinstance(r, dict):
            raise FamilyError("malformed", "The server's reply was not understood.", offline=True)
        return {"notifications": r.get("items"), "unread": r.get("unread"), "total": r.get("total"),
                "nextCursor": None}

    def mark_notifications_read(self):
        return self.rpc("read_all_notifications")

    def _tail(self, fn, limit):
        n = max(1, min(5000, int(limit)))
        r = self.rpc(fn, {"p_limit": n, "p_end": None})
        if not isinstance(r, dict) or not isinstance(r.get("items"), list):
            raise FamilyError("malformed", "The server's reply was not understood.", offline=True)
        return r["items"]

    def audit(self, limit=200):
        return self._tail("my_audit_page", limit)

    def shared_log(self, limit=200):
        return self._tail("my_shared_log_page", limit)

    def revoke_link(self, link_id):
        lid = str(link_id)
        if not _UUID_RE.match(lid):
            raise FamilyError("not_found", "No such link", 404)
        try:
            return self.rpc("revoke_link_json", {"p_link": lid})
        except FamilyError as err:
            if err.code == "no_access":
                raise FamilyError("not_found", "No such link", 404) from err
            raise

    def redeem_code(self, code):
        r = self.rpc("redeem_invite_json", {"p_code": code if isinstance(code, str) else "", "p_scopes": None, "p_detail": None})
        if isinstance(r, dict) and r.get("error"):
            raise code_error(r)
        return {"link": (r or {}).get("link")}

    # ---- requests ("ask for more time"): not in family_sync yet, ported so a later UI needs no transport change ----
    def create_request(self, kind, guide_id, reason="", payload=None):
        if kind not in ("more_time", "unblock", "help"):
            raise _bad("kind", "kind must be one of more_time|unblock|help")
        guide = str(guide_id).lower()
        if not _UUID_RE.match(guide):
            raise FamilyError("not_linked", "You are not linked to that guide", 400)
        payload = {} if payload is None else payload
        if not isinstance(payload, dict) or len(json.dumps(payload)) > 2000:
            raise _bad("payload", "payload must be a small object")
        return self.rpc("create_request", {"p_kind": kind, "p_guide": guide, "p_payload": payload, "p_reason": str(reason or "")[:500]})

    def requests(self):
        return self.rpc("my_requests")

    def new_request_id(self):
        return str(uuid_mod.uuid4())
