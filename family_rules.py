"""The rules a linked guide (a parent or an instructor) can set, and what the desktop should do about them.

PURE PYTHON: no Qt, no win32, no network, no files, no clock of its own (every function that needs "now"
takes it as a number of milliseconds since the epoch). That is on purpose -- this is the part that must be
right, so it is the part that is tested exhaustively and runs on any machine.

It mirrors the browser extension's own logic (carmen-guide-extension/src/core: rules.js, matching.js, blocks.js,
schedule.js, commands.js) so the desktop and the browser agree on what a guide's rules mean:

  * effective rules = the student's own allow list + the guide's allow lists, with every guide's deny list
    applied FIRST (a denied site stays blocked even if somebody allowed it);
  * a recurring study block is active when (utc + tzOffsetMin) has weekday == dow and start <= HH:MM < end;
    if several are active the lock is hard when any one is hard and the allow lists are joined;
  * a guide's command (start_session, end_session, push_link, set_rules, message) is applied at most once,
    is dropped if it has expired, and always ends in a note the student can read. Nothing a guide does is silent.

FAIL-SAFE, ON PURPOSE: everything that arrives from the server goes through a sanitize_* function that returns
None for anything that is not exactly the expected shape. The caller then keeps the copy it already had. So a
broken, truncated or hostile reply can never empty a rules list (that would read as "allow everything") and can
never turn a hard lock into a soft one. Losing the connection is the same: the last good copy keeps being enforced.
Do not "fix" this by treating a bad reply as an empty one.
"""
import math
import re
from datetime import date

# ---------------------------------------------------------------------------------------------------------
# small shared helpers
# ---------------------------------------------------------------------------------------------------------

MAX_ENTRIES = 2000          # most entries any one list may hold (the desktop's own lists are capped the same way)
MAX_ENTRY_LENGTH = 253      # a hostname can't be longer than this
MAX_NAME_LENGTH = 60
MAX_TEXT_LENGTH = 300
MAX_TZ_OFFSET_MIN = 14 * 60  # the real world's range is -12:00..+14:00; the backend allows +-840
MAX_SESSION_MINUTES = 1440
_EPOCH_ORDINAL = 719163     # date(1970, 1, 1).toordinal()
_MS_MAX = 8_000_000_000_000_000   # anything bigger is not a real timestamp

LOCK_MODES = ("soft", "hard")
COMMAND_TYPES = ("start_session", "end_session", "push_link", "set_rules", "message")

_TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
_DAY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
# Same pattern the backend uses for a hostname it will accept and store.
HOST_RE = re.compile(r"^(?=.{1,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z][a-z0-9-]{1,62}$")
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f-\x9f​-‏‪-‮⁦-⁩]")


def _is_int(value):
    return isinstance(value, int) and not isinstance(value, bool)


def _is_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def clean_text(value, limit=MAX_TEXT_LENGTH):
    """Text that came from somebody else (a guide's name, a note), made safe to show: no control or
    direction-changing characters, no line breaks, trimmed, capped. Never raises; non-text becomes ''."""
    if not isinstance(value, str):
        return ""
    return re.sub(r" {2,}", " ", _CONTROL_RE.sub(" ", value)).strip()[:limit].strip()


def parse_iso_ms(value):
    """An ISO-8601 timestamp (as the backend writes them: 2026-11-23T10:00:00.000Z) as milliseconds since the
    epoch, or None when it is not one. A timestamp with no zone is read as UTC."""
    if not isinstance(value, str) or not value or len(value) > 40:
        return None
    from datetime import datetime, timezone
    text = value.strip()
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    ms = int(round(parsed.timestamp() * 1000))
    return ms if abs(ms) < _MS_MAX else None


def iso_from_ms(ms):
    """Milliseconds since the epoch as 2026-11-23T10:00:00.000Z."""
    from datetime import datetime, timezone
    return datetime.fromtimestamp(ms / 1000.0, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.") + f"{int(ms) % 1000:03d}Z"


def utc_day(ms):
    return _local_parts(ms, 0)["day"]


def _uniq(items):
    seen, out = set(), []
    for item in items:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out


# ---------------------------------------------------------------------------------------------------------
# hostnames and URL matching (the same semantics as the extension's matching.js)
# ---------------------------------------------------------------------------------------------------------

def shareable_host(host):
    """A hostname the backend will accept (lower case, no 'www.', dotted, alphabetic TLD), or None. IP addresses
    and 'localhost' are not representable, so they are never uploaded."""
    if not isinstance(host, str):
        return None
    h = host.strip().lower()
    if h.startswith("www."):
        h = h[4:]
    return h if HOST_RE.match(h) else None


_DOMAIN_EQUIVALENTS = (("gmail.com", "mail.google.com"),)


def _equivalent_hostnames(domain):
    for group in _DOMAIN_EQUIVALENTS:
        if domain in group:
            return group
    return (domain,)


def _canonical_host(host):
    """The host the way a URL parser spells it (punycode for non-ASCII names, no trailing dot)."""
    host = host.strip().strip(".").lower()
    try:
        return host.encode("idna").decode("ascii")
    except UnicodeError:
        return host


def _split_url(url):
    """(hostname, pathname) of a URL or bare host, or (None, None). Hostname is lower case without a port or a
    trailing dot; pathname is lower case."""
    from urllib.parse import urlsplit
    if not isinstance(url, str) or not url.strip():
        return None, None
    text = url.strip()
    if "://" not in text:
        text = "http://" + text
    try:
        parts = urlsplit(text)
        host = parts.hostname
    except ValueError:
        return None, None
    if not host:
        return None, None
    return _canonical_host(host), (parts.path or "/").lower()


def is_whitelisted(url, entries):
    """True if `url` (a URL or bare host) is covered by any entry. An entry matches its own host and every
    subdomain; an entry with a path matches at a path boundary. An empty url counts as allowed (nothing to
    judge); an empty or non-list `entries` allows nothing."""
    if not url:
        return True
    if not isinstance(entries, (list, tuple, set)) or not entries:
        return False
    host, path = _split_url(url)
    if host is None:
        return False
    for entry in entries:
        text = str(entry if entry is not None else "").strip().lower()
        if not text:
            continue
        text = re.sub(r"^https?://", "", text)
        text = re.sub(r"^\*?\.", "", text)
        text = re.sub(r"[?#].*$", "", text)
        text = re.sub(r"^([^/?#]*?):\d+(?=[/?#]|$)", r"\1", text)
        slash = text.find("/")
        if slash == -1:
            domain = _canonical_host(text)
            if any(host == d or host.endswith("." + d) for d in _equivalent_hostnames(domain)):
                return True
            continue
        domain = _canonical_host(text[:slash])
        entry_path = text[slash:]
        if not any(host == d or host.endswith("." + d) for d in _equivalent_hostnames(domain)):
            continue
        boundary = entry_path if entry_path.endswith("/") else entry_path + "/"
        if path == entry_path or path.startswith(boundary):
            return True
    return False


def evaluate(url, eff):
    """Deny first, then allow. `eff` = {"allow": [...], "deny": [...], "checkAllow": bool}.
    checkAllow False means only the deny list is enforced. Returns {"allowed": bool, "why": str}."""
    if not url:
        return {"allowed": True, "why": "empty"}
    eff = eff if isinstance(eff, dict) else {}
    deny = eff.get("deny") if isinstance(eff.get("deny"), list) else []
    if deny and is_whitelisted(url, deny):
        return {"allowed": False, "why": "deny"}
    if eff.get("checkAllow") is False:
        return {"allowed": True, "why": "unchecked"}
    if is_whitelisted(url, eff.get("allow")):
        return {"allowed": True, "why": "allow"}
    return {"allowed": False, "why": "not_allowed"}


# ---------------------------------------------------------------------------------------------------------
# rules: GET /me/rules -> {"allow": [...], "deny": [...], "byGuide": [{guideId, name, allow, deny, note}]}
# ---------------------------------------------------------------------------------------------------------

EMPTY_RULES = {"allow": [], "deny": [], "byGuide": [], "version": ""}


def _host_list(value, limit=MAX_ENTRIES):
    """A clean copy of a list of strings, or None if it is not exactly that."""
    if not isinstance(value, list) or len(value) > limit:
        return None
    out = []
    for item in value:
        if not isinstance(item, str) or not item.strip() or len(item) > MAX_ENTRY_LENGTH + 40:
            return None
        out.append(item.strip())
    return out


def rules_version(rules):
    import json
    by = [[g.get("guideId"), g.get("allow"), g.get("deny"), g.get("note") or ""] for g in rules.get("byGuide", [])]
    return json.dumps([rules.get("allow"), rules.get("deny"), by], separators=(",", ":"), sort_keys=False)


def sanitize_rules(remote):
    """The rules document, cleaned -- or None if it is not exactly the expected shape (the caller keeps the copy
    it has). Never returns a partial or an emptied list for a malformed reply."""
    if not isinstance(remote, dict):
        return None
    allow = _host_list(remote.get("allow"))
    deny = _host_list(remote.get("deny"))
    if allow is None or deny is None:
        return None
    by_guide = []
    raw_by = remote.get("byGuide", [])
    if raw_by is None:
        raw_by = []
    if not isinstance(raw_by, list) or len(raw_by) > 200:
        return None
    for g in raw_by:
        if not isinstance(g, dict):
            return None
        g_allow, g_deny = _host_list(g.get("allow", [])), _host_list(g.get("deny", []))
        if g_allow is None or g_deny is None:
            return None
        guide_id = g.get("guideId")
        if not isinstance(guide_id, str) or not guide_id:
            return None
        by_guide.append({"guideId": guide_id, "name": clean_text(g.get("name"), MAX_NAME_LENGTH) or "Your guide",
                         "allow": g_allow, "deny": g_deny, "note": clean_text(g.get("note"))})
    rules = {"allow": _uniq(allow), "deny": _uniq(deny), "byGuide": by_guide}
    rules["version"] = rules_version(rules)
    return rules


def _covered_by(entry, deny):
    """True if allowing `entry` would let in something a deny entry blocks: the entry is the denied host or sits
    under it (allow docs.example.com, deny example.com -> the allow is moot)."""
    host = _canonical_host(re.sub(r"[/?#].*$", "", re.sub(r"^\*?\.", "", re.sub(r"^https?://", "", str(entry).strip().lower()))))
    return bool(host) and is_whitelisted(host, deny)


def merge_rules(student_allow=(), guide_allow=(), guide_deny=(), grant_hosts=(), pushed_hosts=()):
    """The student's own list unioned with the guides' allow lists (and anything a guide granted or pushed).
    Deny is every guide's deny list; it is applied first when a site is judged (see evaluate)."""
    allow = _uniq([*student_allow, *guide_allow, *grant_hosts, *pushed_hosts])
    return {"allow": allow, "deny": _uniq(list(guide_deny))}


def enforceable_allowlist(allow, deny):
    """The allow list as the desktop's own session can hold it: an allow list has no way to say "except this", so
    every allow entry that a deny entry already covers is dropped (deny first). What an allow list cannot express
    -- a denied subdomain of an allowed site -- is still enforced where the deny list itself is read (see
    evaluate and the /family/rules route the browser extension polls)."""
    deny = list(deny or [])
    return [a for a in _uniq(list(allow or [])) if not (deny and _covered_by(a, deny))]


def effective_for_session(student_allow, rules, extra_allow=()):
    """What a session run on a guide's behalf enforces: its own allow list + the guides' allow lists, with the
    guides' deny list applied first. Returns {"allow", "deny", "enforceableAllow"}."""
    rules = rules if isinstance(rules, dict) else EMPTY_RULES
    eff = merge_rules(student_allow=student_allow, guide_allow=rules.get("allow") or [],
                      guide_deny=rules.get("deny") or [], pushed_hosts=extra_allow)
    eff["enforceableAllow"] = enforceable_allowlist(eff["allow"], eff["deny"])
    return eff


def rules_state(has_cache, connected_recently):
    """What the heartbeat tells a guide about this device's copy of the rules (same words as the extension):
    "ok" heard from the backend recently, "stale" enforcing a cached copy, "none" nothing cached at all."""
    if not has_cache:
        return "ok" if connected_recently else "none"
    return "ok" if connected_recently else "stale"


# ---------------------------------------------------------------------------------------------------------
# time windows (study blocks and class windows). local = utc + tzOffsetMin, dow 0 = Sunday, start inclusive,
# end exclusive. Same arithmetic as the backend's timewin.js and the extension's blocks.js / schedule.js.
# ---------------------------------------------------------------------------------------------------------

def _minutes_of(hhmm):
    return int(hhmm[:2]) * 60 + int(hhmm[3:])


def _local_parts(ms, tz_offset_min=0):
    """{"dow", "min", "day", "dayIndex"} of an instant seen from a zone `tz_offset_min` minutes east of UTC.
    Integer arithmetic only (no datetime), so any offset and any date works, and a day that differs from the UTC
    day (late evening west of Greenwich, early morning east of it) is handled by the floor division."""
    total_minutes = int(ms // 60000) + int(tz_offset_min or 0)
    day_index, minute = divmod(total_minutes, 1440)
    dow = (day_index + 4) % 7            # 1970-01-01 was a Thursday; Sunday is 0
    try:
        day = date.fromordinal(_EPOCH_ORDINAL + day_index).isoformat()
    except (ValueError, OverflowError):
        day = ""
    return {"dow": dow, "min": minute, "day": day, "dayIndex": day_index}


def local_parts(ms, tz_offset_min=0):
    return _local_parts(ms, tz_offset_min)


def _tz_ok(value):
    return value is None or (_is_int(value) and -MAX_TZ_OFFSET_MIN <= value <= MAX_TZ_OFFSET_MIN)


def _sanitize_block(b):
    if not isinstance(b, dict):
        return None
    block_id = b.get("id")
    if not isinstance(block_id, str) or not block_id or len(block_id) > 80:
        return None
    dow, start, end, tz = b.get("dow"), b.get("start"), b.get("end"), b.get("tzOffsetMin")
    if not _is_int(dow) or not 0 <= dow <= 6:
        return None
    if not (isinstance(start, str) and _TIME_RE.match(start) and isinstance(end, str) and _TIME_RE.match(end)):
        return None
    if _minutes_of(start) >= _minutes_of(end):      # blocks never cross midnight (the backend refuses them too)
        return None
    if not _tz_ok(tz):
        return None
    if b.get("lockMode") not in LOCK_MODES:
        return None
    allow = _host_list(b.get("allowlist", []), limit=500)
    if allow is None:
        return None
    guide_id = b.get("guideId")
    return {
        "id": block_id, "title": clean_text(b.get("title"), MAX_NAME_LENGTH),
        "dow": dow, "start": start, "end": end, "tzOffsetMin": tz or 0,
        "lockMode": b["lockMode"], "allowlist": allow,
        "guideId": guide_id if isinstance(guide_id, str) else None,
        "guideName": clean_text(b.get("guideName"), MAX_NAME_LENGTH),
    }


def sanitize_blocks(payload):
    """GET /me/schedule -> a clean list of blocks, or None if ANY block is malformed (a half-understood schedule
    could drop a hard lock; the caller keeps the copy it has instead)."""
    if not isinstance(payload, dict) or not isinstance(payload.get("blocks"), list) or len(payload["blocks"]) > 400:
        return None
    out = []
    for raw in payload["blocks"]:
        block = _sanitize_block(raw)
        if block is None:
            return None
        out.append(block)
    return out


def _skipped(skips):
    out = set()
    for s in skips or []:
        if isinstance(s, dict) and isinstance(s.get("blockId"), str) and isinstance(s.get("day"), str):
            out.add((s["blockId"], s["day"]))
    return out


def clean_blocks(blocks):
    """A list of blocks with every malformed one dropped (for evaluating: a block that cannot be understood is
    never active). Use sanitize_blocks to judge a whole reply from the server -- that one refuses the lot."""
    return [b for b in (_sanitize_block(x) for x in blocks or []) if b is not None]


def evaluate_blocks(blocks, now_ms, skips=(), _clean=False):
    """The study blocks active at `now_ms`, or None. Result:
        {"blocks": [...], "lockMode": "hard" if any active block is hard else "soft",
         "allow": [union of allow lists], "endsAtMs": the latest end among them, "by": [names], "title": str}
    A block the student skipped for today (a (blockId, day) pair in `skips`) is not active. A malformed block
    never crashes this and is never active."""
    if not _is_number(now_ms):
        return None
    skipped = _skipped(skips)
    active = []
    for b in (blocks or []) if _clean else clean_blocks(blocks):
        p = _local_parts(now_ms, b["tzOffsetMin"])
        if b["dow"] != p["dow"] or p["min"] < _minutes_of(b["start"]) or p["min"] >= _minutes_of(b["end"]):
            continue
        if (b["id"], p["day"]) in skipped:
            continue
        minute_start = int(now_ms // 60000) * 60000
        active.append({**b, "day": p["day"], "endsAtMs": minute_start + (_minutes_of(b["end"]) - p["min"]) * 60000})
    if not active:
        return None
    names = _uniq([b["guideName"] or "your own schedule" for b in active])
    return {
        "blocks": active,
        "lockMode": "hard" if any(b["lockMode"] == "hard" for b in active) else "soft",
        "allow": _uniq([h for b in active for h in b["allowlist"]]),
        "endsAtMs": max(b["endsAtMs"] for b in active),
        "by": names,
        "title": ", ".join(t for t in _uniq([b["title"] for b in active]) if t),
    }


def coverage_end_ms(blocks, now_ms, skips=(), horizon_minutes=1440):
    """How long study-block cover runs without a gap, from now: the first minute (up to `horizon_minutes`
    ahead) at which no block is active any more. A session started for a block runs exactly that long, so two
    back-to-back blocks are one session, not two."""
    if not _is_number(now_ms):
        return None
    start = int(now_ms // 60000) * 60000
    t = start
    blocks = clean_blocks(blocks)
    for _ in range(horizon_minutes):
        if evaluate_blocks(blocks, t, skips, _clean=True) is None:
            return t if t > start else None
        t += 60000
    return t


def next_block_start_ms(blocks, now_ms, skips=(), horizon_minutes=8 * 1440):
    """When the next study block begins (a skipped one does not count), or None within the horizon."""
    if not _is_number(now_ms):
        return None
    t = int(now_ms // 60000) * 60000
    blocks = clean_blocks(blocks)
    was_active = evaluate_blocks(blocks, t, skips, _clean=True) is not None
    for _ in range(horizon_minutes):
        t += 60000
        active = evaluate_blocks(blocks, t, skips, _clean=True) is not None
        if active and not was_active:
            return t
        was_active = active
    return None


def sanitize_classes(payload):
    """GET /me/classes -> a clean list, or None if malformed. Class windows are only used to tell the student a
    class is on and to expose a deny-only soft overlay; they never start a session by themselves."""
    if not isinstance(payload, list) or len(payload) > 200:
        return None
    out = []
    for c in payload:
        if not isinstance(c, dict) or not isinstance(c.get("classId"), str) or not _tz_ok(c.get("tzOffsetMin")):
            return None
        windows = c.get("windows", [])
        if not isinstance(windows, list) or len(windows) > 50:
            return None
        clean_windows = []
        for w in windows:
            if not (isinstance(w, dict) and _is_int(w.get("dow")) and 0 <= w["dow"] <= 6
                    and isinstance(w.get("start"), str) and _TIME_RE.match(w["start"])
                    and isinstance(w.get("end"), str) and _TIME_RE.match(w["end"])
                    and _minutes_of(w["start"]) < _minutes_of(w["end"])):
                return None
            clean_windows.append({"dow": w["dow"], "start": w["start"], "end": w["end"]})
        ends_on = c.get("endsOn")
        if ends_on is not None and not (isinstance(ends_on, str) and _DAY_RE.match(ends_on)):
            return None
        guide = c.get("guide") if isinstance(c.get("guide"), dict) else {}
        active = c.get("activeSession")
        if active is not None and not (isinstance(active, dict) and isinstance(active.get("id"), str)):
            return None
        out.append({
            "classId": c["classId"], "name": clean_text(c.get("name"), 80) or "Class",
            "endsOn": ends_on, "windows": clean_windows, "tzOffsetMin": c.get("tzOffsetMin") or 0,
            "guide": {"id": guide.get("id") if isinstance(guide.get("id"), str) else None,
                      "displayName": clean_text(guide.get("displayName"), MAX_NAME_LENGTH) or "Your instructor"},
            "activeSession": ({"id": active["id"]} if active else None),
        })
    return out


def active_class_window(classes, now_ms):
    """The class window `now_ms` falls in, or None: {"cls", "window", "startsAtMs", "endsAtMs"}."""
    if not _is_number(now_ms):
        return None
    for cls in classes or []:
        if not isinstance(cls, dict):
            continue
        p = _local_parts(now_ms, cls.get("tzOffsetMin") or 0)
        ends_on = cls.get("endsOn")
        if ends_on and p["day"] > ends_on:
            continue
        for w in cls.get("windows") or []:
            if w.get("dow") != p["dow"]:
                continue
            s, e = _minutes_of(w["start"]), _minutes_of(w["end"])
            if s <= p["min"] < e:
                into_minute = int(now_ms) % 60000
                return {"cls": cls, "window": w,
                        "startsAtMs": int(now_ms) - ((p["min"] - s) * 60000 + into_minute),
                        "endsAtMs": int(now_ms) + ((e - p["min"]) * 60000 - into_minute)}
    return None


def sanitize_conflicts(payload):
    """GET /me/schedule-conflicts -> [{a, b, overlap}], or None if malformed. Only what the student is told."""
    if not isinstance(payload, dict) or not isinstance(payload.get("conflicts"), list) or len(payload["conflicts"]) > 200:
        return None
    out = []
    for c in payload["conflicts"]:
        if not isinstance(c, dict) or not isinstance(c.get("a"), dict) or not isinstance(c.get("b"), dict):
            return None
        a, b, o = c["a"], c["b"], c.get("overlap")
        if not isinstance(a.get("id"), str) or not isinstance(b.get("id"), str) or not isinstance(o, dict):
            return None
        if not (_is_int(o.get("dow")) and 0 <= o["dow"] <= 6 and isinstance(o.get("start"), str) and isinstance(o.get("end"), str)):
            return None
        out.append({"key": "|".join(sorted([a["id"], b["id"]])),
                    "aName": clean_text(a.get("guideName"), MAX_NAME_LENGTH) or "A guide",
                    "bName": clean_text(b.get("guideName"), MAX_NAME_LENGTH) or "A guide",
                    "dow": o["dow"], "start": clean_text(o["start"], 5), "end": clean_text(o["end"], 5)})
    return out


# ---------------------------------------------------------------------------------------------------------
# links: GET /me -> asStudent.links
# ---------------------------------------------------------------------------------------------------------

LINK_SCOPES = ("status", "sessions", "tasks", "screentime", "integrity", "score", "review")


def sanitize_links(payload):
    """GET /me -> {"user", "links"} (the student-side links), or None if malformed."""
    if not isinstance(payload, dict) or not isinstance(payload.get("user"), dict):
        return None
    as_student = payload.get("asStudent")
    if not isinstance(as_student, dict) or not isinstance(as_student.get("links"), list) or len(as_student["links"]) > 400:
        return None
    user = payload["user"]
    if not isinstance(user.get("id"), str):
        return None
    links = []
    for item in as_student["links"]:
        if not isinstance(item, dict) or not isinstance(item.get("id"), str) or not isinstance(item.get("guideId"), str):
            return None
        scopes = item.get("scopes")
        if not isinstance(scopes, list) or not all(isinstance(s, str) for s in scopes):
            return None
        guide = item.get("guide") if isinstance(item.get("guide"), dict) else {}
        links.append({
            "id": item["id"], "guideId": item["guideId"],
            "guideName": clean_text(guide.get("displayName"), MAX_NAME_LENGTH) or "Someone",
            "relation": item.get("relation") if item.get("relation") in ("parent", "instructor") else "parent",
            "classId": item.get("classId") if isinstance(item.get("classId"), str) else None,
            "status": "active" if item.get("status") == "active" else "revoked",
            "scopes": [s for s in scopes if s in LINK_SCOPES],
            "detail": "detail" if item.get("detail") == "detail" else "summary",
            "canAssign": bool(item.get("canAssign")), "canSetRules": bool(item.get("canSetRules")),
            "createdAt": item.get("createdAt") if isinstance(item.get("createdAt"), str) else None,
            "expiresAt": item.get("expiresAt") if isinstance(item.get("expiresAt"), str) else None,
        })
    return {"user": {"id": user["id"], "displayName": clean_text(user.get("displayName"), MAX_NAME_LENGTH),
                     "email": clean_text(user.get("email"), 120)}, "links": links}


def is_active_link(link, now_ms):
    if not isinstance(link, dict) or link.get("status") != "active":
        return False
    expires = parse_iso_ms(link.get("expiresAt")) if link.get("expiresAt") else None
    return expires is None or expires > now_ms


def share_flags(links, now_ms):
    """What at least one active link may see, so only that is ever uploaded (no link: nothing but the heartbeat)."""
    active = [l for l in links or [] if is_active_link(l, now_ms)]

    def has(scope):
        return any(scope in l.get("scopes", []) for l in active)

    return {"any": bool(active), "status": has("status"), "sessions": has("sessions") or has("integrity"),
            "screentime": has("screentime"), "tasks": has("tasks"),
            "detail": any(l.get("detail") == "detail" for l in active)}


SCOPE_WORDS = {
    "status": "whether you are in a session right now, and the session's lock, length and violation count",
    "sessions": "a summary of each finished session (when, how long, lock, violations, pauses)",
    "tasks": "your task names and goals",
    "screentime": "how long you spent on each website, by category (website names only)",
    "integrity": "how many pauses, unblocks and early endings you had",
    "score": "your Carmen Score and grade (every guide always sees this)",
    "review": "how many review topics are due",
}


def describe_link(link):
    """Plain-English lines for what one guide can see and do, from the link itself."""
    lines = [SCOPE_WORDS[s] for s in LINK_SCOPES if s in link.get("scopes", []) and s in SCOPE_WORDS]
    if "score" not in link.get("scopes", []):
        lines.append(SCOPE_WORDS["score"])
    if link.get("detail") == "detail":
        lines.append("the reasons you gave and the names of sites involved, not only counts")
    do = []
    if link.get("canSetRules"):
        do.append("set site rules, start and end sessions, and add study blocks")
    if link.get("canAssign"):
        do.append("give you assignments")
    return {"sees": lines, "can": do}


# ---------------------------------------------------------------------------------------------------------
# commands: GET /me/commands -> [{id, type, payload, authorId, authorName, expiresAt, createdAt}]
# ---------------------------------------------------------------------------------------------------------

def _sanitize_command(c):
    if not isinstance(c, dict):
        return None
    cid, ctype = c.get("id"), c.get("type")
    if not isinstance(cid, str) or not cid or len(cid) > 80 or ctype not in COMMAND_TYPES:
        return None
    payload = c.get("payload")
    if payload is None:
        payload = {}
    if not isinstance(payload, dict):
        return None
    created, expires = parse_iso_ms(c.get("createdAt")), parse_iso_ms(c.get("expiresAt"))
    return {"id": cid, "type": ctype, "payload": payload, "createdAtMs": created, "expiresAtMs": expires,
            "authorId": c.get("authorId") if isinstance(c.get("authorId"), str) else "",
            "authorName": clean_text(c.get("authorName"), MAX_NAME_LENGTH) or "Your guide"}


def sanitize_commands(payload):
    """GET /me/commands -> (clean commands in the order they were made, ids that could not be understood), or
    None if the reply is not a list at all (nothing is applied from a reply that is not a list). A command that
    is itself malformed is reported by id so it can be declined; it is never half-applied."""
    if not isinstance(payload, list) or len(payload) > 500:
        return None
    good, bad = [], []
    for raw in payload:
        cmd = _sanitize_command(raw)
        if cmd is not None:
            good.append(cmd)
        elif isinstance(raw, dict) and isinstance(raw.get("id"), str) and raw["id"]:
            bad.append(raw["id"])
    good.sort(key=lambda c: (c["createdAtMs"] if c["createdAtMs"] is not None else 0))
    return good, bad


def attribution(guide):
    """The words shown next to something a guide set: 'Set by Ms. Okafor'."""
    if not isinstance(guide, dict):
        return ""
    if guide.get("own"):
        return "Your own study block"
    name = clean_text(guide.get("name"), MAX_NAME_LENGTH) or "your guide"
    return f"Set by {name}"


def _guide_dict(cmd, kind="command", **extra):
    return {"kind": kind, "id": cmd.get("authorId") or "", "name": cmd.get("authorName") or "Your guide",
            "commandId": cmd.get("id"), **extra}


def _fmt_clock(ms, tz_offset_min):
    p = _local_parts(ms, tz_offset_min)
    return f"{p['min'] // 60:02d}:{p['min'] % 60:02d}"


def _command_session(cmd, now_ms, rules, extra_allow=()):
    """Validate a start_session command and work out the session it asks for. -> (ok, why_or_plan)"""
    p = cmd["payload"]
    allow = p.get("allowlist")
    if not isinstance(allow, list) or len(allow) > 500 or not all(isinstance(x, str) for x in allow):
        return False, "malformed"
    allow = _uniq([x.strip() for x in allow if x.strip()])
    minutes = p.get("minutes")
    if not _is_number(minutes) or not 1 <= minutes <= MAX_SESSION_MINUTES:
        return False, "malformed"
    if p.get("lockMode") not in LOCK_MODES:
        return False, "malformed"
    started = cmd["createdAtMs"] if cmd["createdAtMs"] is not None else now_ms
    ends = started + int(minutes * 60000)
    if ends <= now_ms:
        return False, "expired"
    eff = effective_for_session(allow, rules, extra_allow)
    return True, {"lockMode": p["lockMode"], "plannedMin": minutes, "startedAtMs": started, "endsAtMs": ends,
                  "allow": eff["enforceableAllow"], "deny": eff["deny"], "guideAllow": allow,
                  "classSessionId": p.get("classSessionId") if isinstance(p.get("classSessionId"), str) else None,
                  "className": clean_text(p.get("className"), 80)}


def decide_command(cmd, now_ms, local, rules=None):
    """What to do about one command. `local` = {"session": {...}} (see decide()). Returns a dict:
        {"action": "start_session" | "end_session" | "notice" | "ignore",
         "commandId": id, "applied": bool,        # what to tell the backend when acknowledging
         "why": str, ...details}
    "applied" False means the guide is told it was declined, and the student gets a quiet note saying why."""
    cid = cmd["id"]
    expires = cmd.get("expiresAtMs")
    if expires is not None and expires <= now_ms:
        return {"action": "ignore", "commandId": cid, "applied": False, "why": "expired"}
    session = (local or {}).get("session") or {}
    guide_session = session.get("guide") if session.get("isActive") else None
    name = cmd["authorName"]

    if cmd["type"] == "start_session":
        ok, plan = _command_session(cmd, now_ms, rules or EMPTY_RULES)
        if not ok:
            return {"action": "ignore", "commandId": cid, "applied": False, "why": plan}
        class_session = plan["classSessionId"]
        if guide_session and guide_session.get("commandId") == cid:
            return {"action": "ignore", "commandId": cid, "applied": True, "why": "already_running"}
        if class_session and guide_session and guide_session.get("classSessionId") == class_session:
            return {"action": "ignore", "commandId": cid, "applied": True, "why": "already_running"}
        guide = _guide_dict(cmd, classSessionId=class_session, className=plan["className"] or None,
                            plannedMin=plan["plannedMin"], baseAllow=plan["guideAllow"])
        what = f'the class session "{plan["className"]}"' if plan["className"] else "a session"
        sites = len(plan["guideAllow"])
        text = (f"{name} started {what}: {plan['lockMode']} lock, {plan['plannedMin']:g} min, "
                f"{sites} allowed site{'' if sites == 1 else 's'}. You can end it early; {name} will see that.")
        return {"action": "start_session", "commandId": cid, "applied": True, "why": "ok", "guide": guide,
                "lockMode": plan["lockMode"], "allow": plan["allow"], "deny": plan["deny"],
                "minutes": max(1 / 60, (plan["endsAtMs"] - now_ms) / 60000.0), "plannedMin": plan["plannedMin"],
                "startedAtMs": plan["startedAtMs"], "endsAtMs": plan["endsAtMs"],
                "replaces": bool(session.get("isActive")),
                "notice": {"kind": "session", "by": name, "text": text}}

    if cmd["type"] == "end_session":
        if not session.get("isActive") or not guide_session:
            return {"action": "ignore", "commandId": cid, "applied": False, "why": "no_guide_session"}
        wanted = cmd["payload"].get("classSessionId")
        if isinstance(wanted, str) and guide_session.get("classSessionId") and wanted != guide_session["classSessionId"]:
            return {"action": "ignore", "commandId": cid, "applied": False, "why": "different_session"}
        if guide_session.get("kind") == "block":
            return {"action": "ignore", "commandId": cid, "applied": False, "why": "no_guide_session"}
        return {"action": "end_session", "commandId": cid, "applied": True, "why": "ok", "endType": "natural",
                "reason": f"Ended by {name}",
                "notice": {"kind": "session", "by": name, "text": f"{name} ended the session. You are free to browse."}}

    if cmd["type"] == "push_link":
        url = cmd["payload"].get("url")
        if not isinstance(url, str) or len(url) > 2000 or not re.match(r"^https?://", url, re.I):
            return {"action": "ignore", "commandId": cid, "applied": False, "why": "malformed"}
        host, _ = _split_url(url)
        if not host:
            return {"action": "ignore", "commandId": cid, "applied": False, "why": "malformed"}
        note = clean_text(cmd["payload"].get("note"), 200)
        return {"action": "notice", "commandId": cid, "applied": True, "why": "ok",
                "notice": {"kind": "link_push", "by": name, "url": url,
                           "text": f"{name} sent you a link to {host}{': ' + note if note else ''}"}}

    if cmd["type"] == "set_rules":
        p = cmd["payload"]
        allow, deny = _host_list(p.get("allow", []), 500), _host_list(p.get("deny", []), 500)
        if allow is None or deny is None:
            return {"action": "ignore", "commandId": cid, "applied": False, "why": "malformed"}
        parts = [f"{len(allow)} allowed"] if allow else []
        parts += [f"{len(deny)} blocked"] if deny else []
        note = clean_text(p.get("note"), 200)
        return {"action": "notice", "commandId": cid, "applied": True, "why": "ok", "refreshRules": True,
                "notice": {"kind": "rules", "by": name,
                           "text": f"{name} updated your rules{' (' + ', '.join(parts) + ')' if parts else ''}"
                                   f"{': ' + note if note else ''}. They apply during sessions."}}

    # message
    p = cmd["payload"]
    text = clean_text(p.get("text") or p.get("note"), MAX_TEXT_LENGTH)
    kind = p.get("kind")
    if kind == "grant":
        minutes = p.get("minutes")
        what = clean_text(str(p.get("requestKind") or "").replace("_", " "), 30)
        body = f"{name} approved your {what} request" + (f": {minutes:g} min" if _is_number(minutes) else "")
        body += f'. "{text}"' if text else ""
        return {"action": "notice", "commandId": cid, "applied": True, "why": "ok", "notice": {"kind": "grant", "by": name, "text": body}}
    if kind == "declined":
        return {"action": "notice", "commandId": cid, "applied": True, "why": "ok",
                "notice": {"kind": "grant", "by": name, "text": f"{name} said not this time" + (f': "{text}"' if text else "") + "."}}
    if kind == "help_reply":
        return {"action": "notice", "commandId": cid, "applied": True, "why": "ok",
                "notice": {"kind": "message", "by": name, "text": f"{name} replied to your help request" + (f': "{text}"' if text else "")}}
    if not text:
        return {"action": "ignore", "commandId": cid, "applied": False, "why": "malformed"}
    return {"action": "notice", "commandId": cid, "applied": True, "why": "ok", "notice": {"kind": "message", "by": name, "text": f"{name}: {text}"}}


# ---------------------------------------------------------------------------------------------------------
# the one question: what should the desktop do now?
# ---------------------------------------------------------------------------------------------------------

def _simulate(session, step):
    """The local session as it will be once `step` has been carried out, so a batch of commands decides in order."""
    session = dict(session or {})
    if step["action"] == "start_session":
        session.update(isActive=True, isPaused=False, isBreak=False, guide=step["guide"], lockMode=step["lockMode"],
                       domainWhitelist=list(step["allow"]), sessionId=None)
    elif step["action"] == "end_session":
        session.update(isActive=False, guide=None)
    return session


def _block_steps(now_ms, snap, session, rules):
    """Study-block steps: start a session when a block begins, keep it in step with the blocks, end it when
    cover ends. Only runs when nothing the student or a guide started is already running."""
    blocks, skips = snap.get("blocks") or [], snap.get("skips") or []
    cover = evaluate_blocks(blocks, now_ms, skips)
    guide = session.get("guide") if session.get("isActive") else None
    steps = []
    if session.get("isActive"):
        if not guide or guide.get("kind") != "block":
            return steps                                   # their own session, or a guide's command: leave it
        if cover is None:
            steps.append({"action": "end_session", "commandId": None, "applied": True, "why": "block_over",
                          "endType": "natural", "reason": None,
                          "notice": {"kind": "schedule", "by": guide.get("name"), "text": "Your study block is over. You are free to browse."}})
            return steps
        eff = effective_for_session(cover["allow"], rules, snap.get("pushedHosts") or ())
        if (set(guide.get("blockIds") or []) != {b["id"] for b in cover["blocks"]}
                or session.get("lockMode") != cover["lockMode"]
                or sorted(session.get("domainWhitelist") or []) != sorted(eff["enforceableAllow"])):
            steps.append({"action": "update_session", "commandId": None, "applied": True, "why": "blocks_changed",
                          "lockMode": cover["lockMode"], "allow": eff["enforceableAllow"], "deny": eff["deny"],
                          "guide": {**guide, "blockIds": [b["id"] for b in cover["blocks"]]}})
        return steps
    if cover is None:
        return steps
    end_ms = coverage_end_ms(blocks, now_ms, skips) or cover["endsAtMs"]
    eff = effective_for_session(cover["allow"], rules)
    own_only = cover["by"] == ["your own schedule"]
    first = cover["blocks"][0]
    guide = {"kind": "block", "id": first.get("guideId") or "", "name": " and ".join(cover["by"]),
             "own": own_only, "commandId": None, "blockIds": [b["id"] for b in cover["blocks"]],
             "classSessionId": None, "className": None}
    when = f"until {_fmt_clock(end_ms, first['tzOffsetMin'])}"
    steps.append({"action": "start_session", "commandId": None, "applied": True, "why": "block_started", "guide": guide,
                  "lockMode": cover["lockMode"], "allow": eff["enforceableAllow"], "deny": eff["deny"],
                  "minutes": max(1 / 60, (end_ms - now_ms) / 60000.0), "plannedMin": round((end_ms - now_ms) / 60000.0),
                  "startedAtMs": now_ms, "endsAtMs": end_ms, "replaces": False, "day": first["day"],
                  "notice": {"kind": "schedule", "by": guide["name"],
                             "text": f"{'Your study block' if own_only else guide['name'] + chr(39) + 's study block'} "
                                     f"has started: {cover['lockMode']} lock {when}. You can end it early; "
                                     f"{'it will be noted' if own_only else guide['name'] + ' will see that'}."}})
    return steps


def decide(now_ms, snap):
    """Everything the desktop should do right now, in order, as a list of steps (see decide_command for the shape;
    "update_session" and "skip_block" are added here). Pure: `snap` holds everything it needs.

        snap = {
          "commands": [clean commands from sanitize_commands],   # newest state of GET /me/commands, may be empty
          "processed": {command ids already handled},            # a repeat is acknowledged again, never applied twice
          "bad": [ids of commands that could not be understood],  # declined
          "rules": cached rules (sanitize_rules) or None,
          "blocks": cached study blocks, "skips": [{"blockId", "day"}],
          "activeGuideIds": set of guide ids with an active link, or None when the links are not known
                            (offline, never fetched): None never ends anything,
          "session": {"isActive", "isPaused", "isBreak", "lockMode", "domainWhitelist",
                      "guide": {...}|None},                      # the local session
          "tracked": {"kind": "block", "blockIds": [...], "day": str, "endsAtMs": int, "sessionId": str}|None,
        }

    Never fails open: a malformed snapshot piece is treated as 'nothing known' for that piece and the rest still
    runs; an unknown connection state never ends a session."""
    snap = snap if isinstance(snap, dict) else {}
    steps = []
    rules = snap.get("rules") if isinstance(snap.get("rules"), dict) else EMPTY_RULES
    def _list(key):
        value = snap.get(key)
        return value if isinstance(value, (list, tuple, set)) else []

    session = dict(snap["session"]) if isinstance(snap.get("session"), dict) else {}
    processed = {p for p in _list("processed") if isinstance(p, str)}
    snap = {**snap, "commands": [c for c in _list("commands") if isinstance(c, dict)],
            "bad": [b for b in _list("bad") if isinstance(b, str)],
            "blocks": list(_list("blocks")), "skips": list(_list("skips")),
            "pushedHosts": [h for h in _list("pushedHosts") if isinstance(h, str)],
            "activeGuideIds": set(snap["activeGuideIds"]) if isinstance(snap.get("activeGuideIds"), (list, tuple, set)) else None}

    seen = set()
    for cmd in snap.get("commands") or []:
        cid = cmd.get("id") if isinstance(cmd, dict) else None
        if not cid:
            continue
        if cid in seen:                                    # the same command twice in one reply
            continue
        seen.add(cid)
        if cid in processed:                               # done earlier; its acknowledgement may have been lost
            steps.append({"action": "ignore", "commandId": cid, "applied": True, "why": "duplicate", "reack": True})
            continue
        step = decide_command(cmd, now_ms, {"session": session}, rules)
        steps.append(step)
        session = _simulate(session, step)
    for cid in snap.get("bad") or []:
        if cid not in seen and cid not in processed:
            seen.add(cid)
            steps.append({"action": "ignore", "commandId": cid, "applied": False, "why": "malformed"})

    # a guide's session ends when nobody can vouch for the link any more
    guide = session.get("guide") if session.get("isActive") else None
    active_guides = snap.get("activeGuideIds")
    if guide and guide.get("kind") == "command" and active_guides is not None and guide.get("id") not in active_guides:
        step = {"action": "end_session", "commandId": None, "applied": True, "why": "link_ended", "endType": "manual",
                "reason": "The link ended",
                "notice": {"kind": "session", "by": guide.get("name"),
                           "text": f"{guide.get('name') or 'Your guide'}'s session ended because the link ended. Nothing of theirs applies any more."}}
        steps.append(step)
        session = _simulate(session, step)

    # the guides' rules changed while their session runs (or a pushed link was opened): keep the session in step.
    # Only the allow list of a session a guide started is touched, and deny is applied first.
    guide = session.get("guide") if session.get("isActive") else None
    if guide and guide.get("kind") == "command" and isinstance(guide.get("baseAllow"), list):
        eff = effective_for_session(guide["baseAllow"], rules, snap.get("pushedHosts") or ())
        if sorted(session.get("domainWhitelist") or []) != sorted(eff["enforceableAllow"]):
            steps.append({"action": "update_session", "commandId": None, "applied": True, "why": "rules_changed",
                          "lockMode": session.get("lockMode"), "allow": eff["enforceableAllow"], "deny": eff["deny"],
                          "guide": guide})

    # a block session the student ended early is not started again today
    tracked = snap.get("tracked")
    started_now = any(s["action"] == "start_session" for s in steps)
    if isinstance(tracked, dict) and tracked.get("kind") == "block" and not started_now:
        same = session.get("isActive") and session.get("sessionId") == tracked.get("sessionId")
        ends = tracked.get("endsAtMs")
        if not same and _is_number(ends) and now_ms < ends - 5000:
            steps.append({"action": "skip_block", "commandId": None, "applied": True, "why": "ended_early",
                          "blockIds": list(tracked.get("blockIds") or []), "day": tracked.get("day"),
                          "notice": {"kind": "schedule", "by": None,
                                     "text": "You ended your study block early. It will not start again today, and the person who set it can see that."}})
            snap = {**snap, "skips": [*(snap.get("skips") or []),
                                      *[{"blockId": b, "day": tracked.get("day")} for b in tracked.get("blockIds") or []]]}

    steps.extend(_block_steps(now_ms, snap, session, rules))
    return steps
