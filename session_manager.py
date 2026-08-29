"""Session state machine — mirrors the browser extension's session model.

State is kept in memory and persisted to session_state.json after every
mutation so an in-progress session survives a crash/restart.
"""
import json
import math
import os
import threading
from datetime import datetime, timedelta

import session_history

STATE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "private", "session_state.json")

_lock = threading.Lock()

_state = {
    "isActive": False,
    "startTime": None,
    "endTime": None,
    "lockMode": "soft",
    # Apps are a block list (checked = blocked, everything else allowed);
    # domains stay an allow list (checked = allowed, everything else
    # blocked) -- a deliberate mixed model, not an oversight. See is_blocked()
    # for the process side; the browser extension applies domainWhitelist
    # itself for the domain side (this module doesn't interpret domains).
    "processBlocklist": [],
    "domainWhitelist": [],
    "violationCount": 0,
    "violationLog": [],
    "lastAcceptableProcess": None,
    "domainWhitelistAdditions": [],
    "processBlocklistExceptions": [],
    "isPaused": False,
    "pausedAt": None,
    "frozenSecondsRemaining": None,
    # Where this session's block/allow lists and lock_mode came from:
    # "manual" (popup or picker_gui, using config.json's saved lists) or
    # "calendar-event" (a per-session override tagged with the event it came
    # from). Purely descriptive — enforcement itself doesn't branch on this,
    # it just reads domainWhitelist/lockMode as usual. Exists so the
    # extension popup can show *why* the active lists don't match what's
    # saved locally, instead of leaving the user thinking their manual
    # picks broke.
    "source": "manual",
    "eventId": None,
    "eventTitle": None,
    # Only populated for source="review" (a review timer started against a
    # task-linked topic, see qt_ui/review_tab.py's _begin_review) -- lets the
    # Tasks/Focus tabs show which problem/subject is actually being
    # reviewed right now instead of just the generic eventTitle string.
    "reviewProblemName": None,
    "reviewSubjectName": None,
    # review_store's problem id (an int, unlike the above two display
    # strings) -- the ONLY way a review session recovered after an app
    # restart (see _TopicView._resume_if_active in qt_ui/review_tab.py) can
    # still log a real review_sessions row on Finish. review_store's own
    # start_review()/finish_review() token lives in an in-memory dict that
    # deliberately does NOT survive a restart, but this whole _state dict
    # does (persisted to session_state.json) -- so this is what lets a
    # paused-then-PC-shutdown-then-resumed review still get recorded instead
    # of silently vanishing on Finish.
    "reviewProblemId": None,
    # True for a "Until I burnout" task session (tasks_store.BURNOUT_MINUTES
    # duration, see qt_ui/tasks_tab.py's _start_burnout) -- a first-class,
    # persisted flag rather than something each UI surface has to guess at
    # from duration_minutes or track in its own ephemeral widget state. The
    # latter used to be exactly how the Tasks tab did it (a local
    # self._active_is_burnout, set only by the widget instance that actually
    # clicked Start), which meant any OTHER surface observing the same
    # already-running burnout session -- the Focus tab, the tray tooltip, or
    # even a freshly-rebuilt Tasks tab card after switching tabs away and
    # back -- had no way to know it was a burnout session at all, and showed
    # a countdown from the full 8-hour ceiling instead of "Until burnout"
    # with elapsed time underneath.
    "isBurnout": False,
    # AppUserModelIDs (see enforcer.get_window_aumi) of Chrome/Edge profile
    # windows to block for just that one profile -- unlike processBlocklist,
    # which blocks chrome.exe/msedge.exe outright (every profile equally).
    # Needed because every profile of the same browser runs as ONE OS
    # process (a second `chrome.exe --profile-directory=X` launch hands off
    # to the existing process over IPC and exits), so plain process-name
    # blocking can't tell one profile's windows apart from another's -- see
    # is_blocked_browser_profile() and enforcer.is_blocked_window().
    "blockedBrowserProfiles": [],
}

# Chromium-based browsers where every open profile shares one OS process --
# is_blocked_browser_profile() applies to windows from these, keyed by the
# window's own AppUserModelID rather than its process name/pid.
MULTI_PROFILE_BROWSER_PROCESSES = {"chrome.exe", "msedge.exe"}

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
# regardless of the session blocklist. Without this, enforcement fights the
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
    "openwith.exe",  # "How do you want to open this file?" system dialog
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
    the tray icon itself — no matter what the session blocklist says."""
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
    # private/ (gitignored, holds every real data file) won't exist yet on
    # a fresh clone.
    os.makedirs(os.path.dirname(STATE_PATH), exist_ok=True)
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


def start_session(
    duration_minutes,
    lock_mode,
    process_blocklist,
    domain_whitelist,
    source="manual",
    event_id=None,
    event_title=None,
    review_problem_name=None,
    review_subject_name=None,
    review_problem_id=None,
    is_burnout=False,
    blocked_browser_profiles=None,
):
    # api_server.py's /session/start already validates this for the
    # network-facing path, but start_session() is also called in-process
    # (picker_dialogs.py, review_tab.py, tasks_tab.py, calendar_scheduler.py)
    # without going through that validation -- guard here too, at the
    # actual point the crash would happen (timedelta can't represent NaN
    # -- ValueError -- or Infinity/an enormous value -- OverflowError),
    # rather than relying on every caller to have already checked.
    if (
        not isinstance(duration_minutes, (int, float))
        or isinstance(duration_minutes, bool)
        or not math.isfinite(duration_minutes)
        or duration_minutes <= 0
    ):
        raise ValueError(f"duration_minutes must be a finite positive number, got {duration_minutes!r}")

    with _lock:
        now = datetime.now()
        if _state["isActive"]:
            # A session is already running (e.g. a calendar event fires
            # mid-way through a manual session, or two events overlap) —
            # the new session still wins outright rather than trying to
            # stack/queue lock types or reconcile the block/allow lists, but
            # the one being replaced must not just vanish. Finalize it to
            # history first, same as a normal end, so its violation
            # count/log and whichever lists it was actually running under
            # aren't silently discarded.
            _finalize_to_history_locked(
                now,
                end_type="superseded",
                reason=f"Overwritten by a new {source} session starting.",
            )
        end_time = now + timedelta(minutes=duration_minutes)
        _state["isActive"] = True
        _state["startTime"] = now.isoformat()
        _state["endTime"] = end_time.isoformat()
        _state["lockMode"] = lock_mode
        _state["processBlocklist"] = list(process_blocklist)
        _state["domainWhitelist"] = list(domain_whitelist)
        _state["violationCount"] = 0
        _state["violationLog"] = []
        _state["lastAcceptableProcess"] = None
        _state["domainWhitelistAdditions"] = []
        _state["processBlocklistExceptions"] = []
        _state["isPaused"] = False
        _state["pausedAt"] = None
        _state["frozenSecondsRemaining"] = None
        # Defaulted explicitly (rather than left over from whatever the
        # previous session was) so a plain manual start can never inherit a
        # stale "calendar-event" tag from before.
        _state["source"] = source
        _state["eventId"] = event_id
        _state["eventTitle"] = event_title
        _state["reviewProblemName"] = review_problem_name
        _state["reviewSubjectName"] = review_subject_name
        _state["reviewProblemId"] = review_problem_id
        _state["isBurnout"] = is_burnout
        _state["blockedBrowserProfiles"] = list(blocked_browser_profiles or [])
        _open_violation_index["process"] = None
        _open_violation_index["domain"] = None
        _save()
    return get_status()


def update_blocklist(process_blocklist, domain_whitelist):
    """Replace the active session's block/allow lists in-place, effective
    immediately."""
    with _lock:
        if not _state["isActive"]:
            return
        _state["processBlocklist"] = list(process_blocklist)
        _state["domainWhitelist"] = list(domain_whitelist)
        _save()


def end_session(end_type="manual", reason=None):
    """Ends the session and returns its final status, including the full
    violationLog accumulated during the session — this is the one place a
    caller (e.g. the tray's "End Session") can see what happened before the
    counters reset for the next session. Also files the completed session
    into session_history.json (see _finalize_to_history_locked).

    end_type distinguishes how the session ended in history/UI: "manual" for
    a plain POST /session/end (e.g. from the browser extension), "nuclear"
    for the tray's "End Session (Nuclear)" button specifically, or "natural"
    for a session that just ran out its own clock (see get_status()). reason
    is the free-text explanation collected from the tray's nuclear-end
    dialog — None for every other end_type."""
    with _lock:
        result = _finalize_to_history_locked(datetime.now(), end_type=end_type, reason=reason)
    import daily_summary_store
    daily_summary_store.flush_through_yesterday()
    # Local import, same reasoning as daily_summary_store above -- enforcer
    # imports this module, so importing it back at module level here would
    # be circular. Reverts every taskbar-hover-preview suppression hard lock
    # applied this session (see enforcer._hide_taskbar_preview) -- without
    # this, a window it hid from Alt+Tab/taskbar preview stayed hidden
    # forever once the session ended, since that only otherwise got
    # reverted by the mid-session "Unblock" flow (restore_window_for_process).
    import enforcer
    enforcer.restore_all_taskbar_previews()
    return result


def _finalize_to_history_locked(now, end_type="natural", reason=None):
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
    process_blocklist = list(_state["processBlocklist"])
    domain_whitelist = list(_state["domainWhitelist"])
    domain_whitelist_additions = list(_state["domainWhitelistAdditions"])
    process_blocklist_exceptions = list(_state["processBlocklistExceptions"])
    start_time = _state["startTime"]
    source = _state["source"]
    event_id = _state["eventId"]
    event_title = _state["eventTitle"]
    review_problem_name = _state["reviewProblemName"]
    review_subject_name = _state["reviewSubjectName"]

    if was_active:
        session_history.append_entry(
            {
                "startTime": start_time,
                "endTime": now.isoformat(),
                "endType": end_type,
                "reason": reason,
                "lockMode": lock_mode,
                "processBlocklist": process_blocklist,
                "domainWhitelist": domain_whitelist,
                "violationCount": summary_count,
                "violationLog": summary_log,
                "domainWhitelistAdditions": domain_whitelist_additions,
                "processBlocklistExceptions": process_blocklist_exceptions,
                "source": source,
                "eventId": event_id,
                "eventTitle": event_title,
                # get_status() has always returned these live (see
                # _get_status_locked below) -- they were just never carried
                # into the history entry itself, so a review session's
                # history/log entry showed no more than "manual", with no
                # way to tell which task/subject/problem it was.
                "reviewProblemName": review_problem_name,
                "reviewSubjectName": review_subject_name,
            }
        )

    _state["isActive"] = False
    _state["startTime"] = None
    _state["endTime"] = None
    _state["violationCount"] = 0
    _state["violationLog"] = []
    _state["lastAcceptableProcess"] = None
    _state["domainWhitelistAdditions"] = []
    _state["processBlocklistExceptions"] = []
    _state["isPaused"] = False
    _state["pausedAt"] = None
    _state["frozenSecondsRemaining"] = None
    # Reset back to "manual" here (not just at the next start_session()) so a
    # GET /status polled in the gap between this session ending and the next
    # one starting never reports a stale calendar-event tag for a session
    # that's no longer running.
    _state["source"] = "manual"
    _state["eventId"] = None
    _state["eventTitle"] = None
    _state["reviewProblemName"] = None
    _state["reviewSubjectName"] = None
    _state["reviewProblemId"] = None
    _state["isBurnout"] = False
    _state["blockedBrowserProfiles"] = []
    _open_violation_index["process"] = None
    _open_violation_index["domain"] = None
    _save()

    return {
        "isActive": False,
        "secondsRemaining": 0,
        "endType": end_type,
        "reason": reason,
        "lockMode": lock_mode,
        "processBlocklist": process_blocklist,
        "domainWhitelist": domain_whitelist,
        "violationCount": summary_count,
        "lastAcceptableProcess": None,
        "violationLog": summary_log,
        "domainWhitelistAdditions": domain_whitelist_additions,
        "processBlocklistExceptions": process_blocklist_exceptions,
        "source": source,
        "eventId": event_id,
        "eventTitle": event_title,
    }


def get_status():
    with _lock:
        return _get_status_locked()


def _get_status_locked():
    """Body of get_status(), for callers (pause_session()/resume_session())
    that already hold _lock — _lock isn't reentrant, so get_status() itself
    can't be called from inside another with _lock: block."""
    seconds_remaining = 0
    if _state["isActive"] and _state["isPaused"]:
        # Timer is frozen — return the exact value it was frozen at instead
        # of recomputing from endTime, and never self-finalize a "natural
        # end" while paused (the deadline math below is the only thing that
        # can fire that, and it's skipped entirely here).
        seconds_remaining = _state["frozenSecondsRemaining"] or 0
    elif _state["isActive"] and _state["endTime"]:
        end_time = datetime.fromisoformat(_state["endTime"])
        seconds_remaining = max(0, int((end_time - datetime.now()).total_seconds()))
        if seconds_remaining == 0:
            _pending_natural_end["value"] = _finalize_to_history_locked(end_time, end_type="natural")
    return {
        "isActive": _state["isActive"],
        "isPaused": _state["isPaused"],
        "startTime": _state["startTime"],
        "secondsRemaining": seconds_remaining,
        "lockMode": _state["lockMode"],
        "processBlocklist": list(_state["processBlocklist"]),
        "domainWhitelist": list(_state["domainWhitelist"]),
        "violationCount": _state["violationCount"],
        "violationLog": list(_state["violationLog"]),
        "lastAcceptableProcess": _state["lastAcceptableProcess"],
        "domainWhitelistAdditions": list(_state["domainWhitelistAdditions"]),
        "processBlocklistExceptions": list(_state["processBlocklistExceptions"]),
        "source": _state["source"],
        "eventId": _state["eventId"],
        "eventTitle": _state["eventTitle"],
        "reviewProblemName": _state["reviewProblemName"],
        "reviewSubjectName": _state["reviewSubjectName"],
        "reviewProblemId": _state["reviewProblemId"],
        "isBurnout": _state["isBurnout"],
        "blockedBrowserProfiles": list(_state["blockedBrowserProfiles"]),
    }


def pause_session():
    """Freezes the countdown only — isActive, lockMode, blocklists, and
    violation tracking are all untouched, so lock enforcement keeps working
    exactly as before while paused. Idempotent: no active session, or a
    session that's already paused, just returns the current status unchanged.

    The frozen secondsRemaining is computed once here and stored, rather than
    just remembering pausedAt, so get_status() can return the exact same
    number on every poll without redoing "now vs endTime" math that would
    have to account for the pause itself."""
    with _lock:
        if not _state["isActive"] or _state["isPaused"]:
            return _get_status_locked()

        now = datetime.now()
        end_time = datetime.fromisoformat(_state["endTime"])
        seconds_remaining = max(0, int((end_time - now).total_seconds()))

        if seconds_remaining == 0:
            # The timer already ran out -- this call raced the natural-expiry
            # check in _get_status_locked() (only that path self-finalizes,
            # and it's skipped entirely once isPaused is True). Finalizing
            # here instead of pausing avoids leaving the session stuck
            # forever as "active + paused + 0s remaining": nothing would ever
            # un-stick it, since finalization never runs while paused.
            _pending_natural_end["value"] = _finalize_to_history_locked(end_time, end_type="natural")
            return _get_status_locked()

        _state["isPaused"] = True
        _state["pausedAt"] = now.isoformat()
        _state["frozenSecondsRemaining"] = seconds_remaining
        _state["violationLog"].append({"kind": "pause", "timestamp": now.isoformat()})
        _save()
        return _get_status_locked()


def resume_session():
    """Resumes the countdown from exactly the secondsRemaining it was frozen
    at — shifts endTime forward by however long the pause lasted, rather than
    recomputing from the original start time, so the pause duration never
    counts against the timer. Idempotent: no active session, or a session
    that isn't paused, just returns the current status unchanged."""
    with _lock:
        if not _state["isActive"] or not _state["isPaused"]:
            return _get_status_locked()

        now = datetime.now()
        frozen_remaining = _state["frozenSecondsRemaining"] or 0
        _state["endTime"] = (now + timedelta(seconds=frozen_remaining)).isoformat()
        _state["isPaused"] = False
        _state["pausedAt"] = None
        _state["frozenSecondsRemaining"] = None
        _state["violationLog"].append({"kind": "resume", "timestamp": now.isoformat()})
        _save()
        return _get_status_locked()


def pop_pending_natural_end():
    """Returns (and clears) the summary queued by get_status() the last time
    it self-finalized an expired session, or None if no natural end is
    pending. Meant to be polled once per window_tracker tick."""
    with _lock:
        summary = _pending_natural_end["value"]
        _pending_natural_end["value"] = None
    if summary is not None:
        import daily_summary_store
        daily_summary_store.flush_through_yesterday()
        import enforcer
        enforcer.restore_all_taskbar_previews()
    return summary


def is_blocked(process_name):
    """Checks process_name against processBlocklist — this is what the
    desktop app's own window-polling loop uses. The browser extension is
    expected to read domainWhitelist from GET /status itself and apply its
    own tab-matching logic; this module doesn't interpret domains."""
    if not process_name:
        return False
    with _lock:
        blocklist_lower = [p.lower() for p in _state["processBlocklist"]]
        return process_name.lower() in blocklist_lower


def is_blocked_browser_profile(aumi):
    """Checks a Chrome/Edge window's AppUserModelID (see
    enforcer.get_window_aumi) against blockedBrowserProfiles -- the
    per-profile counterpart to is_blocked()'s per-process-name check. Exact
    match, not case-folded: AUMIs are opaque identifiers Chrome assigns
    itself, not user-typed process names, so there's no reason to expect
    (or paper over) a casing mismatch here."""
    if not aumi:
        return False
    with _lock:
        return aumi in _state["blockedBrowserProfiles"]


def _resolve_open_violation_locked(kind, now):
    """Closes out the most recent unresolved violation of the given kind
    ("process" / "domain"), filling in resolvedAt/durationSeconds. Must be
    called with _lock held. A no-op if there's nothing open — record_violation
    calls this before opening its own new entry, so switching straight from
    one distraction to another (blocklisted app A -> blocklisted app B, with
    no acceptable app in between) still closes A's entry: its duration is
    "how long you stayed on that one" rather than "time until back on
    track", which is the only sensible definition once B has already
    started."""
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
    """Called when the foreground app is NOT on processBlocklist — resolves
    any open *process* violation (switching to an allowed app is what "back
    on track" means for this kind; it says nothing about the browser's active
    tab, which is tracked/resolved independently via resolve_domain_violation)."""
    with _lock:
        now = datetime.now()
        had_open_violation = _open_violation_index["process"] is not None
        _resolve_open_violation_locked("process", now)
        changed = had_open_violation or _state["lastAcceptableProcess"] != process_name
        _state["lastAcceptableProcess"] = process_name
        # This is called on every poll tick (every 1.5s) for as long as the
        # user stays on an allowed app — without this guard, a whole session
        # spent on-task would rewrite session_state.json to disk nonstop for
        # no reason, since nothing here actually changed after the first tick.
        if changed:
            _save()


def record_violation(process_name):
    with _lock:
        if not _state["isActive"]:
            # The window-polling loop only calls this after seeing
            # isActive=True on the very same status snapshot, but this is
            # still reachable with a stale/in-flight call racing a session
            # end — recording a violation against already-reset state would
            # inflate violationCount/violationLog for the idle period until
            # the next session's start_session() call happens to wipe it.
            return _state["violationCount"]
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
    uses, for a violation reported by the browser extension (a domain not on
    domainWhitelist) instead of this app's own window-polling loop."""
    with _lock:
        if not _state["isActive"]:
            # Reachable when the browser extension's POST /violation lands
            # just after a session ends (network latency racing the end) —
            # see record_violation()'s matching guard for why this must not
            # apply against already-reset state.
            return _state["violationCount"]
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


def add_domain_to_whitelist(domain, reason):
    """Adds domain to domainWhitelist mid-session (e.g. via POST
    /whitelist/domains/add) and logs why in domainWhitelistAdditions — the
    audit trail of every site let in outside the original allow list picked
    at session start. Takes effect immediately: the browser extension
    re-reads domainWhitelist from GET /status on every tab check, so there's
    no need to restart the session for the unblock to apply.

    Skips the append (but still logs the addition) if domain is already on
    the allow list, case-insensitive — same "don't grow the list with
    duplicates" behavior as the rest of this module's list handling.

    Returns (None, None) if no session is active. This is checked here,
    inside the same lock as the actual write, rather than leaving it to
    callers to check is_active() beforehand — a session can end (naturally,
    nuclear, or via another request) in the gap between a caller's own
    is_active() check and this call actually running (e.g. while a user is
    still typing a reason in a dialog). Without this atomic check, that race
    would silently apply the addition to already-reset state: since
    domainWhitelist itself isn't cleared until the next start_session(),
    the addition (and its audit-log entry) would end up misattributed to
    whatever session starts next instead of the one the user meant it for."""
    with _lock:
        if not _state["isActive"]:
            return None, None
        now = datetime.now()
        existing_lower = {d.lower() for d in _state["domainWhitelist"]}
        if domain.lower() not in existing_lower:
            _state["domainWhitelist"].append(domain)

        addition = {
            "domain": domain,
            "reason": reason,
            "timestamp": now.isoformat(),
        }
        _state["domainWhitelistAdditions"].append(addition)
        _save()
        return list(_state["domainWhitelist"]), addition


def remove_process_from_blocklist(process_name, reason):
    """Removes process_name from processBlocklist mid-session (e.g. via the
    lock overlay's "Unblock" button on a violation, or the "Pick Apps to
    Blocklist" picker's own remove flow, opened while a session is already
    running) and logs why in processBlocklistExceptions — the audit trail of
    every app let through despite being on the original blocklist picked at
    session start. Takes effect immediately: is_blocked() and the
    window-polling loop both read processBlocklist straight off this same
    in-memory state, so there's no need to restart the session for the
    unblock to apply.

    Skips the actual removal (but still logs the exception) if process_name
    isn't on the blocklist to begin with, case-insensitive.

    Returns (None, None) if no session is active — see add_domain_to_whitelist()
    for why this is checked atomically inside the lock rather than by callers
    beforehand."""
    with _lock:
        if not _state["isActive"]:
            return None, None
        now = datetime.now()
        existing_lower = {p.lower() for p in _state["processBlocklist"]}
        if process_name.lower() in existing_lower:
            _state["processBlocklist"] = [
                p for p in _state["processBlocklist"] if p.lower() != process_name.lower()
            ]

        exception_entry = {
            "process": process_name,
            "reason": reason,
            "timestamp": now.isoformat(),
        }
        _state["processBlocklistExceptions"].append(exception_entry)
        _save()
        return list(_state["processBlocklist"]), exception_entry


def get_lock_mode():
    with _lock:
        return _state["lockMode"]


def get_last_acceptable_process():
    with _lock:
        return _state["lastAcceptableProcess"]


def is_active():
    with _lock:
        return _state["isActive"]
