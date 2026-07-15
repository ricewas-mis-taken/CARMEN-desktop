"""Session state machine — mirrors the browser extension's session model.

State is kept in memory and persisted to session_state.json after every
mutation so an in-progress session survives a crash/restart.
"""
import json
import os
import threading
from datetime import datetime, timedelta

import session_history

STATE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "session_state.json")

_lock = threading.Lock()

_state = {
    "isActive": False,
    "startTime": None,
    "endTime": None,
    "lockMode": "soft",
    "processWhitelist": [],
    "domainWhitelist": [],
    "violationCount": 0,
    "violationLog": [],
    "lastAcceptableProcess": None,
}

# Index into violationLog of the most recent still-unresolved violation of
# each kind ("process" / "domain"), so record_acceptable()/
# resolve_domain_violation() can compute how long that violation lasted.
# Not persisted — deliberately transient, same trade-off as the rest of this
# app's crash-recovery story: losing an open violation's resolution on a
# restart mid-session is an acceptable simplification.
_open_violation_index = {"process": None, "domain": None}

# Set by get_status() when it notices a session's timer ran out and
# self-finalizes it — the window-polling loop (which calls get_status() every
# tick regardless of whether anything else is polling /status) drains this to
# fire a "session complete" tray notification exactly once. Manual ends (tray
# "End Session", POST /session/end) notify their own caller directly and never
# touch this.
_pending_natural_end = {"value": None}

# Core Windows shell / system processes that are never treated as violations,
# regardless of the session whitelist. Without this, enforcement fights the
# taskbar, alt-tab, wifi/time flyouts, and the shell itself — and minimizing
# explorer.exe specifically has been observed to crash it and disturb the
# size of unrelated snapped windows.
ALWAYS_ALLOWED_PROCESSES = {
    "explorer.exe",
    "shellexperiencehost.exe",
    "searchhost.exe",
    "searchapp.exe",
    "startmenuexperiencehost.exe",
    "applicationframehost.exe",
    "textinputhost.exe",
    "lockapp.exe",
    "dwm.exe",
    "sihost.exe",
    "widgets.exe",
    "widgetboard.exe",
    "systemsettings.exe",
    "systemsettingsbroker.exe",
    "control.exe",
    "peopleexperiencehost.exe",
    "shellhost.exe",
    "taskmgr.exe",
    "windowsterminal.exe",
    "wt.exe",
    "openconsole.exe",
    # Vendor/GPU utilities — checking GPU stats or an overlay isn't a
    # meaningful "distraction" to guard against.
    "nvidia app.exe",
    "nvidiaapp.exe",
    "nvcplui.exe",
    "nvcontainer.exe",
    "nvidia share.exe",
    "nvsphelper64.exe",
    "geforceexperience.exe",
    "gpuview.exe",
    # Git for Windows' own bundled launchers (Git Bash / Git CMD / Git GUI
    # Start Menu shortcuts) — dev tooling, not a focus-breaker.
    "git-bash.exe",
    "git-cmd.exe",
    "git-gui.exe",
    "gitk.exe",
    # Built-in Windows utility apps and personal tooling — exempted by
    # explicit request, same as the git-bash entries above. PowerShell is
    # included here (rather than just "Developer PowerShell for VS" alone)
    # because that shortcut just launches the regular powershell.exe/pwsh.exe
    # binary with startup arguments; there's no separate "dev powershell" exe
    # to match on.
    "time.exe",  # Clock / Alarms & Clock
    "calculatorapp.exe",  # Calculator
    "notepad.exe",  # Notepad (classic and the newer Store version share this name)
    "snippingtool.exe",  # Snipping Tool
    "screensketch.exe",  # Snip & Sketch (older name for the same app)
    "powershell.exe",
    "pwsh.exe",
    # Own background app — has no visible window most of the time, so it
    # never shows up in the installed-apps picker (no Start Menu shortcut,
    # no MSIX package) for is_exempt() to be checked against in the first
    # place there; exempting it here is what actually stops it from getting
    # minimized/redirected by hard-lock enforcement on the rare occasion its
    # window does come to the foreground.
    "typesenselogger.exe",
    # Power Automate Desktop — process name guessed (not verified on this
    # machine); if it's still getting flagged, check Task Manager for its
    # actual process name and add it here instead/also.
    "pad.console.host.exe",
    "microsoft.flow.rpa.desktop.exe",
}


def is_exempt(process_name, pid=None):
    """True for our own process (tray/popups) or core shell/system processes
    that must always remain usable — alt-tab, taskbar, wifi/time flyouts,
    the tray icon itself — no matter what the session whitelist says."""
    if pid is not None and pid == os.getpid():
        return True
    if process_name and process_name.lower() in ALWAYS_ALLOWED_PROCESSES:
        return True
    return False


def _save():
    # Write to a temp file and rename over the real one — _save() runs on
    # every violation and status tick, so a plain in-place write leaves a
    # wide window where killing the process mid-write truncates the file.
    # os.replace() is atomic on Windows, so a crash mid-save can never leave
    # session_state.json partially written.
    tmp_path = STATE_PATH + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(_state, f, indent=2)
    os.replace(tmp_path, STATE_PATH)


def _load():
    global _state
    if not os.path.exists(STATE_PATH):
        return
    try:
        with open(STATE_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        # A corrupt/truncated file (e.g. from a crash mid-write before the
        # atomic-save fix above) must not crash the whole app at import
        # time — fall back to the in-memory defaults instead.
        return
    _state.update(data)


_load()


def start_session(duration_minutes, lock_mode, process_whitelist, domain_whitelist):
    with _lock:
        now = datetime.now()
        end_time = now + timedelta(minutes=duration_minutes)
        _state["isActive"] = True
        _state["startTime"] = now.isoformat()
        _state["endTime"] = end_time.isoformat()
        _state["lockMode"] = lock_mode
        _state["processWhitelist"] = list(process_whitelist)
        _state["domainWhitelist"] = list(domain_whitelist)
        _state["violationCount"] = 0
        _state["violationLog"] = []
        _state["lastAcceptableProcess"] = None
        _open_violation_index["process"] = None
        _open_violation_index["domain"] = None
        _save()
    return get_status()


def end_session():
    """Ends the session and returns its final status, including the full
    violationLog accumulated during the session — this is the one place a
    caller (e.g. the tray's "End Session") can see what happened before the
    counters reset for the next session. Also files the completed session
    into session_history.json (see _finalize_to_history_locked)."""
    with _lock:
        result = _finalize_to_history_locked(datetime.now())
    return result


def _finalize_to_history_locked(now):
    """Records the current session into session_history.json and resets
    in-memory state for the next one. Must be called with _lock held.
    Shared by end_session() and get_status()'s natural-expiry path, so a
    session that just runs out the clock ends up in history exactly the same
    as one ended manually — otherwise most sessions would never get logged.

    A no-op (well, still resets state, but skips the history write) when
    there was no actual session running — e.g. the tray's "End Session" or
    POST /session/end called with nothing active. Without this guard, every
    redundant end call would file a phantom history entry with no start
    time."""
    was_active = _state["isActive"] or _state["startTime"] is not None

    summary_count = _state["violationCount"]
    summary_log = list(_state["violationLog"])
    lock_mode = _state["lockMode"]
    process_whitelist = list(_state["processWhitelist"])
    domain_whitelist = list(_state["domainWhitelist"])
    start_time = _state["startTime"]

    if was_active:
        session_history.append_entry(
            {
                "startTime": start_time,
                "endTime": now.isoformat(),
                "lockMode": lock_mode,
                "processWhitelist": process_whitelist,
                "domainWhitelist": domain_whitelist,
                "violationCount": summary_count,
                "violationLog": summary_log,
            }
        )

    _state["isActive"] = False
    _state["startTime"] = None
    _state["endTime"] = None
    _state["violationCount"] = 0
    _state["violationLog"] = []
    _state["lastAcceptableProcess"] = None
    _open_violation_index["process"] = None
    _open_violation_index["domain"] = None
    _save()

    return {
        "isActive": False,
        "secondsRemaining": 0,
        "lockMode": lock_mode,
        "processWhitelist": process_whitelist,
        "domainWhitelist": domain_whitelist,
        "violationCount": summary_count,
        "lastAcceptableProcess": None,
        "violationLog": summary_log,
    }


def get_status():
    with _lock:
        seconds_remaining = 0
        if _state["isActive"] and _state["endTime"]:
            end_time = datetime.fromisoformat(_state["endTime"])
            seconds_remaining = max(0, int((end_time - datetime.now()).total_seconds()))
            if seconds_remaining == 0:
                _pending_natural_end["value"] = _finalize_to_history_locked(end_time)
        return {
            "isActive": _state["isActive"],
            "secondsRemaining": seconds_remaining,
            "lockMode": _state["lockMode"],
            "processWhitelist": list(_state["processWhitelist"]),
            "domainWhitelist": list(_state["domainWhitelist"]),
            "violationCount": _state["violationCount"],
            "violationLog": list(_state["violationLog"]),
            "lastAcceptableProcess": _state["lastAcceptableProcess"],
        }


def pop_pending_natural_end():
    """Returns (and clears) the summary queued by get_status() the last time
    it self-finalized an expired session, or None if no natural end is
    pending. Meant to be polled once per window_tracker tick."""
    with _lock:
        summary = _pending_natural_end["value"]
        _pending_natural_end["value"] = None
        return summary


def is_whitelisted(process_name):
    """Checks process_name against processWhitelist — this is what the
    desktop app's own window-polling loop uses. The browser extension is
    expected to read domainWhitelist from GET /status itself and apply its
    own tab-matching logic; this module doesn't interpret domains."""
    if not process_name:
        return False
    with _lock:
        whitelist_lower = [p.lower() for p in _state["processWhitelist"]]
        return process_name.lower() in whitelist_lower


def _resolve_open_violation_locked(kind, now):
    """Closes out the most recent unresolved violation of the given kind
    ("process" / "domain"), filling in resolvedAt/durationSeconds. Must be
    called with _lock held. A no-op if there's nothing open — record_violation
    calls this before opening its own new entry, so switching straight from
    one distraction to another (bad app A -> bad app B, with no whitelisted
    app in between) still closes A's entry: its duration is "how long you
    stayed on that one" rather than "time until back on track", which is the
    only sensible definition once B has already started."""
    index = _open_violation_index.get(kind)
    if index is None:
        return
    _open_violation_index[kind] = None
    if index >= len(_state["violationLog"]):
        return
    entry = _state["violationLog"][index]
    if entry.get("resolvedAt") is not None:
        return
    start = datetime.fromisoformat(entry["timestamp"])
    entry["resolvedAt"] = now.isoformat()
    entry["durationSeconds"] = max(0, int((now - start).total_seconds()))


def record_acceptable(process_name):
    """Called when the foreground app is on processWhitelist — resolves any
    open *process* violation (switching to an allowed app is what "back on
    track" means for this kind; it says nothing about the browser's active
    tab, which is tracked/resolved independently via resolve_domain_violation)."""
    with _lock:
        now = datetime.now()
        _resolve_open_violation_locked("process", now)
        _state["lastAcceptableProcess"] = process_name
        _save()


def record_violation(process_name):
    with _lock:
        now = datetime.now()
        _resolve_open_violation_locked("process", now)
        _state["violationCount"] += 1
        _state["violationLog"].append(
            {
                "kind": "process",
                "process": process_name,
                "timestamp": now.isoformat(),
                "lockMode": _state["lockMode"],
                "resolvedAt": None,
                "durationSeconds": None,
            }
        )
        _open_violation_index["process"] = len(_state["violationLog"]) - 1
        _save()
        return _state["violationCount"]


def record_domain_violation(url):
    """Same violationCount/violationLog the process-based record_violation()
    uses, for a violation reported by the browser extension (an off-whitelist
    domain) instead of this app's own window-polling loop."""
    with _lock:
        now = datetime.now()
        _resolve_open_violation_locked("domain", now)
        _state["violationCount"] += 1
        _state["violationLog"].append(
            {
                "kind": "domain",
                "url": url,
                "timestamp": now.isoformat(),
                "lockMode": _state["lockMode"],
                "resolvedAt": None,
                "durationSeconds": None,
            }
        )
        _open_violation_index["domain"] = len(_state["violationLog"]) - 1
        _save()
        return _state["violationCount"]


def resolve_domain_violation():
    """Called when the browser extension reports the active tab is back on
    an allowed domain — closes out any open domain violation the same way
    record_acceptable() does for process violations. Nothing to resolve is
    not an error (e.g. the extension pinging this with no prior violation)."""
    with _lock:
        _resolve_open_violation_locked("domain", datetime.now())
        _save()


def get_lock_mode():
    with _lock:
        return _state["lockMode"]


def get_last_acceptable_process():
    with _lock:
        return _state["lastAcceptableProcess"]


def is_active():
    with _lock:
        return _state["isActive"]
