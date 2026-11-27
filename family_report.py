"""What the desktop tells the family backend about the student, built in one place so it is easy to audit.

PURE PYTHON (no Qt, no win32, no network). The rules it enforces:

  * hostnames only: a violation host, a screen-time host, an unblock host is a bare hostname. No URL, path,
    query, page title or page content ever enters anything built here. Anything that is not a hostname the backend
    accepts (an IP address, "localhost", an app name) is dropped, not sent;
  * nothing is built for a guide who cannot see it: every builder takes the share flags that
    family_rules.share_flags derives from the student's active links. No link, no upload but the heartbeat;
  * free text (the reason a session was ended, the plan given for an unblock) is for detail-level links only;
  * history that happened BEFORE a guide could see it is never sent later: marks start at the moment of consent
    (see the watermark and screen-time marks in family_sync);
  * task names and review problems are only shared with a link that has the "tasks" scope.
"""
import hashlib
from datetime import datetime, timezone

from family_rules import clean_text, iso_from_ms, shareable_host

# pause reasons that cost nothing (mirror of pause_budget.FREE; kept here so this module stays import-light)
FREE_PAUSE_REASONS = {"break", "long_break", "interrupted", "stuck", "rest", "aside"}


def _parse_local(iso):
    """A history timestamp (naive local time, as session_manager writes it) as an aware datetime, or None."""
    if not isinstance(iso, str) or not iso:
        return None
    try:
        parsed = datetime.fromisoformat(iso)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        try:
            parsed = parsed.astimezone()          # a naive time is this computer's local time
        except (OverflowError, OSError, ValueError):
            parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def to_utc_iso(iso):
    """A local history timestamp as 2026-11-23T10:00:00.000Z, or None."""
    parsed = _parse_local(iso)
    if parsed is None:
        return None
    return iso_from_ms(int(parsed.timestamp() * 1000))


def summary_id(entry):
    """The id a finished session is filed under: its sessionId, or (an entry from before sessions had ids) the same
    fingerprint session_history.entry_id uses, so two reports of the same session never differ."""
    session_id = entry.get("sessionId")
    if isinstance(session_id, str) and session_id:
        return session_id[:64]
    raw = "|".join(str(entry.get(k)) for k in ("startTime", "endTime", "source", "eventId", "eventTitle", "reviewProblemName"))
    return ("legacy-" + hashlib.sha1(raw.encode("utf-8")).hexdigest()[:24])[:64]


def _markers(log):
    return sorted((e for e in (log or []) if isinstance(e, dict) and e.get("kind") in ("pause", "resume")
                   and isinstance(e.get("timestamp"), str)), key=lambda e: e["timestamp"])


def pause_stats(log, end_iso):
    """{"free": n, "checking": n, "minutes": total paused minutes, "freeMin", "checkingMin", "pausedSeconds"} from the
    pause/resume markers in a session's log. A pause still open at the end runs to the end."""
    free = checking = 0
    free_s = check_s = 0
    open_at, open_reason = None, None
    end = _parse_local(end_iso)

    def close(at):
        nonlocal free, checking, free_s, check_s, open_at
        seconds = max(0, int((at - open_at).total_seconds()))
        if open_reason in FREE_PAUSE_REASONS:
            free += 1
            free_s += seconds
        else:
            checking += 1
            check_s += seconds
        open_at = None

    for marker in _markers(log):
        at = _parse_local(marker["timestamp"])
        if at is None:
            continue
        if marker["kind"] == "pause" and open_at is None:
            open_at, open_reason = at, marker.get("pauseReason") or "unspecified"
        elif marker["kind"] == "resume" and open_at is not None:
            close(at)
    if open_at is not None and end is not None and end > open_at:
        close(end)
    return {"free": free, "checking": checking, "minutes": round((free_s + check_s) / 60),
            "freeMin": round(free_s / 60), "checkingMin": round(check_s / 60), "pausedSeconds": free_s + check_s}


def focused_minutes(entry):
    """Wall-clock minutes between start and end, minus the time paused."""
    start, end = _parse_local(entry.get("startTime")), _parse_local(entry.get("endTime"))
    if start is None or end is None or end <= start:
        return 0
    paused = pause_stats(entry.get("violationLog"), entry.get("endTime"))["pausedSeconds"]
    return max(0, min(1440, round(((end - start).total_seconds() - paused) / 60)))


def _end_type(entry):
    kind = entry.get("endType")
    if kind == "nuclear":
        return "nuclear"
    if kind == "natural":
        return "completed"
    return "ended_early"


def task_label(entry, share_tasks):
    """The name shown to a guide: a class session's class name always (the guide named it), a task name only when a
    link has the tasks scope, a review only as the word 'Review' (never the problem or subject)."""
    guide = entry.get("guide") if isinstance(entry.get("guide"), dict) else None
    if guide and guide.get("className"):
        return clean_text(guide["className"], 80)
    if not share_tasks:
        return ""
    if entry.get("source") == "review" or entry.get("reviewProblemName"):
        return "Review"
    return clean_text(entry.get("eventTitle"), 80)


def summary_from_history(entry, flags):
    """One finished session as the backend's summary shape, redacted to what `flags` allow. None when the entry is
    not a reportable session (no times, a review timed beside another session, an unreadable shape)."""
    if not isinstance(entry, dict) or entry.get("beside"):
        return None
    started, ended = to_utc_iso(entry.get("startTime")), to_utc_iso(entry.get("endTime"))
    if not started or not ended or ended < started:
        return None
    lock = entry.get("lockMode") if entry.get("lockMode") in ("soft", "hard") else "soft"
    stats = pause_stats(entry.get("violationLog"), entry.get("endTime"))
    span_min = max(0, min(1440, round(((_parse_local(entry["endTime"]) - _parse_local(entry["startTime"])).total_seconds()) / 60)))
    guide = entry.get("guide") if isinstance(entry.get("guide"), dict) else None
    planned = span_min
    if guide and isinstance(guide.get("plannedMin"), (int, float)) and not isinstance(guide.get("plannedMin"), bool):
        planned = max(0, min(1440, round(guide["plannedMin"])))
    additions = entry.get("domainWhitelistAdditions") if isinstance(entry.get("domainWhitelistAdditions"), list) else []
    exceptions = entry.get("processBlocklistExceptions") if isinstance(entry.get("processBlocklistExceptions"), list) else []
    out = {
        "id": summary_id(entry), "startedAt": started, "endedAt": ended,
        "plannedMin": planned, "focusedMin": min(focused_minutes(entry), 1440),
        "taskName": task_label(entry, flags.get("tasks", False)),
        "lockMode": lock, "endType": _end_type(entry),
        "violations": max(0, min(100000, int(entry.get("violationCount") or 0))),
        "pauses": {"free": min(1000, stats["free"]), "checking": min(1000, stats["checking"]), "minutes": min(1440, stats["minutes"])},
        "pauseMin": {"free": min(1440, stats["freeMin"]), "checking": min(1440, stats["checkingMin"])},
        "source": "desktop",
        "unblocks": min(1000, len(additions) + len(exceptions)),
    }
    class_session = guide.get("classSessionId") if guide else None
    if isinstance(class_session, str) and class_session:
        out["classSessionId"] = class_session[:64]
    if flags.get("detail"):
        reason = clean_text(entry.get("reason"), 500)
        if reason:
            out["reason"] = reason
        hosts = []
        for item in entry.get("violationLog") or []:
            if isinstance(item, dict) and item.get("kind") == "domain":
                host = shareable_host(_host_from_url(item.get("url")))
                if host and host not in hosts:
                    hosts.append(host)
        if hosts:
            out["violationHosts"] = hosts[:100]
        plans = []
        for a in additions:
            if not isinstance(a, dict) or not a.get("plan"):
                continue
            host = shareable_host(a.get("domain"))
            if host:
                minutes = a.get("minutes")
                plans.append({"host": host, "reason": clean_text(a.get("reason"), 300), "plan": clean_text(a.get("plan"), 300),
                              "minutes": int(minutes) if isinstance(minutes, (int, float)) and not isinstance(minutes, bool) and 0 <= minutes <= 240 else 0})
        if plans:
            out["unblockPlans"] = plans[:20]
    return out


def _host_from_url(url):
    from urllib.parse import urlsplit
    if not isinstance(url, str) or not url:
        return None
    try:
        return urlsplit(url if "://" in url else "http://" + url).hostname
    except ValueError:
        return None


def status_payload(session, now_ms, flags):
    """The live status for POST /me/status, or None when no guide may see it. `session` is the desktop's
    session_manager.get_status() (or anything with the same keys)."""
    if not flags.get("status"):
        return None
    if not isinstance(session, dict) or not session.get("isActive"):
        return {"state": "idle"}
    paused = bool(session.get("isPaused"))
    state = "paused" if paused else ("break" if session.get("isBreak") else "live")
    ends_ms = None
    if isinstance(session.get("secondsRemaining"), (int, float)):
        ends_ms = now_ms + int(session["secondsRemaining"]) * 1000
    started = to_utc_iso(session.get("startTime"))
    out = {"state": state, "lockMode": session.get("lockMode") if session.get("lockMode") in ("soft", "hard") else "soft",
           "violations": max(0, min(100000, int(session.get("violationCount") or 0)))}
    if started:
        out["startedAt"] = started
    if ends_ms is not None:
        out["endsAt"] = iso_from_ms(ends_ms)
    stats = pause_stats(session.get("violationLog"), None)
    open_extra = 0
    log = _markers(session.get("violationLog"))
    if log and log[-1]["kind"] == "pause":
        at = _parse_local(log[-1]["timestamp"])
        if at is not None:
            open_extra = max(0, int(now_ms / 1000 - at.timestamp()))
    out["pausedMin"] = max(0, min(1440, round((stats["pausedSeconds"] + open_extra) / 60)))
    label = task_label({"source": session.get("source"), "eventTitle": session.get("eventTitle"),
                        "reviewProblemName": session.get("reviewProblemName"), "guide": session.get("guide")},
                       flags.get("tasks", False))
    if label:
        out["taskName"] = label
    return out


def screentime_rows(domain_seconds, sent, local_day, now_ms):
    """New hostname-seconds since the last pass. `domain_seconds` = {host: total seconds today on this computer},
    `sent` = {"<day>|<host>": seconds already counted}. Returns (rows, new_sent): rows are
    [{"day", "hostname", "seconds", "at"}] for POST /me/screentime, `at` being the minute the time was credited to
    (an instructor only sees samples inside a class window, so the stamp has to be honest). Hosts the backend
    cannot take (IP addresses, localhost, bare words) are counted as seen but never listed."""
    from family_rules import utc_day
    rows, new_sent = [], dict(sent or {})
    minute = int(now_ms // 60000) * 60000
    for host, total in (domain_seconds or {}).items():
        if not isinstance(total, (int, float)) or isinstance(total, bool) or total != total:
            continue
        key = f"{local_day}|{host}"
        delta = int(total) - int(new_sent.get(key, 0))
        if delta <= 0:
            continue
        new_sent[key] = int(total)
        clean = shareable_host(host)
        if clean:
            rows.append({"day": utc_day(minute), "hostname": clean, "seconds": min(86400, delta), "at": iso_from_ms(minute)})
    return rows, new_sent


def merge_screentime(queue, rows):
    """Adds rows to the upload queue, merging seconds of the same host in the same minute."""
    out = list(queue or [])
    index = {(r["hostname"], r["at"]): r for r in out}
    for row in rows:
        hit = index.get((row["hostname"], row["at"]))
        if hit:
            hit["seconds"] = min(86400, hit["seconds"] + row["seconds"])
        else:
            copy = dict(row)
            out.append(copy)
            index[(copy["hostname"], copy["at"])] = copy
    return out[-2000:]
