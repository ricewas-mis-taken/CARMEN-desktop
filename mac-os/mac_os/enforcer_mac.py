"""macOS soft/hard lock enforcement actions -- Accessibility-API equivalent
of enforcer.py's win32/DWM implementation.

Every mechanism here requires Accessibility permission (System Settings ->
Privacy & Security -> Accessibility). is_accessibility_trusted() is exposed
for the onboarding flow PORT_SPEC.md's Subsystem 2 calls for (checking on
every launch, blocking session-start or showing a persistent warning until
granted) -- that UI itself is not built yet; wiring it into main.py's
startup and qt_ui is still open work.

Window identity: there is no macOS "hwnd". Every function below that takes
or returns a window handle actually takes/returns a pid -- see
window_tracker_mac.get_active_window's docstring for why that's a safe
stand-in for every current caller.
"""
import threading

import psutil
from AppKit import NSRunningApplication, NSWorkspace, NSApplicationActivateIgnoringOtherApps
from ApplicationServices import (
    AXIsProcessTrustedWithOptions,
    AXUIElementCopyAttributeValue,
    AXUIElementCreateApplication,
    AXUIElementSetAttributeValue,
    kAXMinimizedAttribute,
    kAXWindowsAttribute,
)
from Quartz import (
    CGWindowListCopyWindowInfo,
    kCGNullWindowID,
    kCGWindowListOptionOnScreenOnly,
)

import qt_gui_thread
import qt_ui.enforcer_overlay as enforcer_overlay
import session_manager

_AX_TRUSTED_PROMPT_OPTION = "AXTrustedCheckOptionPrompt"


def is_accessibility_trusted(prompt=False):
    """True if this process currently has Accessibility permission. Pass
    prompt=True to have macOS pop its own system permission dialog the first
    time this is called if not already granted (see
    AXIsProcessTrustedWithOptions's docs) -- the onboarding flow should call
    this with prompt=True once, then re-check with prompt=False on every
    subsequent launch (the user can revoke it later in System Settings, so a
    stale "trusted" assumption must never be cached across launches)."""
    try:
        return bool(AXIsProcessTrustedWithOptions({_AX_TRUSTED_PROMPT_OPTION: prompt}))
    except Exception:
        return False


# Every pid currently hidden via _hide_app(pid, True) -- restore_all_taskbar_previews()
# (called on every session end, see session_manager.py) walks this to undo
# every one, not just whichever single process restore_window_for_process's
# mid-session "Unblock" flow happens to target -- same reasoning as
# enforcer.py's _hidden_hwnds on Windows.
_hidden_pids = set()
_hidden_pids_lock = threading.Lock()


def _minimize_all_windows(pid):
    """Minimizes every window belonging to pid via the Accessibility API.
    Returns True if the call could even attempt it (an AX-trusted process
    exists at that pid with a readable window list), False otherwise -- the
    caller uses this the same way enforcer.py's ShowWindow try/except does,
    to decide whether to also count this as "actually handled" for dedupe
    purposes."""
    try:
        app_ref = AXUIElementCreateApplication(pid)
        err, windows = AXUIElementCopyAttributeValue(app_ref, kAXWindowsAttribute, None)
        if err or not windows:
            return False
        for w in windows:
            AXUIElementSetAttributeValue(w, kAXMinimizedAttribute, True)
        return True
    except Exception:
        return False


def _all_windows_minimized(pid):
    """True if pid has no windows, or every one of its windows already
    reads kAXMinimizedAttribute == True -- the macOS equivalent of
    enforcer.py's win32gui.IsIconic(hwnd) check in sweep_minimize_blocked_windows,
    used the same way: to avoid re-issuing a minimize (and double-counting
    in the returned list) for a window that's already down."""
    try:
        app_ref = AXUIElementCreateApplication(pid)
        err, windows = AXUIElementCopyAttributeValue(app_ref, kAXWindowsAttribute, None)
        if err or not windows:
            return True
        for w in windows:
            err2, is_minimized = AXUIElementCopyAttributeValue(w, kAXMinimizedAttribute, None)
            if err2 or not is_minimized:
                return False
        return True
    except Exception:
        return False


def _hide_app(pid, hide):
    """NSRunningApplication.hide()/unhide() -- removes (or restores) every
    window of pid's app from the screen, Mission Control, and Cmd+Tab's
    window previews. This is macOS's closest-parity replacement for
    Windows' DWMWA_DISALLOW_PEEK/DWMWA_FORCE_ICONIC_REPRESENTATION taskbar
    hover-preview suppression -- and strictly stronger, since it hides
    every window of the app, not just the one that was blocked (see
    PORT_SPEC.md's Subsystem 2 "closest-parity" section). Used in addition
    to (not instead of) the per-window minimize above, so behavior degrades
    gracefully if hide() ever fails for some app."""
    with _hidden_pids_lock:
        if hide:
            _hidden_pids.add(pid)
        else:
            _hidden_pids.discard(pid)
    try:
        app = NSRunningApplication.runningApplicationWithProcessIdentifier_(pid)
        if app is None:
            return
        if hide:
            app.hide()
        else:
            app.unhide()
    except Exception:
        pass


def restore_all_taskbar_previews():
    """Reverts _hide_app for every pid hidden this session -- called on
    every session end (see session_manager.py's end_session() and
    pop_pending_natural_end()), independent of whether any of them were
    individually unblocked first. Same role as enforcer.py's
    restore_all_taskbar_previews() on Windows."""
    with _hidden_pids_lock:
        pids = list(_hidden_pids)
    for pid in pids:
        _hide_app(pid, False)


def get_window_aumi(window_id):
    """No AppUserModelID concept on macOS -- always None. Kept only so
    call sites written against enforcer.py's Windows API don't need a
    separate code path to skip calling it."""
    return None


def list_known_profile_aumis(process_name):
    """Per-Chrome/Edge-profile blocking is Windows-only for now -- see
    window_tracker_mac.list_known_browser_profiles's docstring and
    PORT_SPEC.md's Subsystem 3 findings. Always []."""
    return []


def describe_browser_profile_aumi(process_name, aumi):
    """No per-profile labels to look up on macOS (see list_known_profile_aumis) --
    falls back to the bare process name, same as enforcer.py's own fallback
    branch for an unrecognized aumi."""
    return process_name


def is_blocked_window(process_name, window_id):
    """window_id is a pid (see module docstring). With no AUMI/per-window
    profile signal available on macOS yet, this collapses to the plain
    process-name check -- session_manager.is_blocked() already works
    identically on both platforms with no changes needed, which is exactly
    PORT_SPEC.md's Subsystem 3 fallback: the base case works everywhere,
    only per-profile blocking is deferred."""
    return session_manager.is_blocked(process_name)


def _find_pid_by_process_name(process_name):
    """macOS equivalent of enforcer._find_window_by_process_name -- returns
    a pid rather than a window handle, since activation/restoration both
    happen at the whole-app level here (NSRunningApplication), not a
    specific window."""
    if not process_name:
        return None
    try:
        for proc in psutil.process_iter(["pid", "name"]):
            try:
                if (proc.info.get("name") or "").lower() == process_name.lower():
                    return proc.info["pid"]
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
    except Exception:
        pass
    return None


def _activate(pid):
    try:
        app = NSRunningApplication.runningApplicationWithProcessIdentifier_(pid)
        if app is not None:
            app.activateWithOptions_(NSApplicationActivateIgnoringOtherApps)
    except Exception:
        pass


def _frontmost_app():
    """Returns (pid, process_name) for the current frontmost app, or
    (None, None) on any failure."""
    try:
        active_app = NSWorkspace.sharedWorkspace().frontmostApplication()
        if active_app is None:
            return None, None
        pid = active_app.processIdentifier()
        try:
            process_name = psutil.Process(pid).name()
        except (psutil.NoSuchProcess, psutil.AccessDenied, Exception):
            process_name = None
        return pid, process_name
    except Exception:
        return None, None


def soft_lock_warning(offending_process_name=None, hwnd=None):
    """hwnd (a pid, per this module's convention) is accepted only for
    call-site compatibility with enforcer.soft_lock_warning's signature --
    it's unused here. Windows' soft lock additionally covers the offending
    window's own on-screen rectangle in black for the warning's duration;
    that needs a per-window frame lookup this port doesn't implement yet, so
    only the message overlay itself shows on macOS for now (a real
    functional gap versus Windows soft lock, not just cosmetic -- worth
    closing in a follow-up once AXUIElement window-frame lookup is wired
    up)."""
    status = session_manager.get_status()
    if status.get("source") == "review":
        message = f"Finish {status.get('reviewProblemName') or 'this review'} first"
    else:
        last_ok = status["lastAcceptableProcess"] or "your focus app"
        message = f"You're off track — back to {last_ok}?"
    _show_lock_overlay(message, duration_ms=5000, offending_process_name=offending_process_name)


def hard_lock_redirect(offending_process_name=None):
    """Minimizes every window of the current frontmost app (if it's
    blocked, not exempt) and hides it from Mission Control/Cmd+Tab preview,
    then activates the last-acceptable (non-blocklisted) app.

    Re-checks the frontmost app right before acting, same reasoning as
    enforcer.hard_lock_redirect's own comment: the frontmost app can change
    between the polling tick that detected this violation and this call
    actually running."""
    frontmost_pid, frontmost_process = _frontmost_app()

    if (
        frontmost_pid is not None
        and not session_manager.is_exempt(frontmost_process, frontmost_pid)
        and frontmost_process
        and is_blocked_window(frontmost_process, frontmost_pid)
    ):
        _minimize_all_windows(frontmost_pid)
        _hide_app(frontmost_pid, True)

    last_acceptable = session_manager.get_last_acceptable_process()
    target_pid = _find_pid_by_process_name(last_acceptable) if last_acceptable else None
    if target_pid:
        _activate(target_pid)

    label = offending_process_name or frontmost_process or "that app"
    status = session_manager.get_status()
    if status.get("source") == "review":
        message = f"Finish {status.get('reviewProblemName') or 'this review'} first"
    else:
        back_to = last_acceptable or "your focus app"
        message = f"Redirected from {label} — back to {back_to}."
    _show_lock_overlay(
        message,
        duration_ms=3000,
        offending_process_name=label if label != "that app" else None,
    )


def sweep_minimize_blocked_windows():
    """Minimizes every on-screen window belonging to a blocklisted process --
    not just whichever app happens to be frontmost. Same role as
    enforcer.sweep_minimize_blocked_windows() on Windows: catches a
    blocklisted app that's open in the background without ever becoming the
    frontmost app.

    Returns [(process_name, pid), ...] for every pid actually minimized this
    call (i.e. the ones that weren't already fully minimized) -- pid stands
    in for hwnd here (see module docstring); window_tracker.run_polling_loop
    only ever uses this value as an opaque dedupe-dict key / equality check,
    never a real window handle, so it's a safe drop-in."""
    minimized = []
    seen_pids = set()
    try:
        window_list = CGWindowListCopyWindowInfo(
            kCGWindowListOptionOnScreenOnly, kCGNullWindowID
        ) or []
    except Exception:
        return minimized

    for window in window_list:
        pid = window.get("kCGWindowOwnerPID")
        if pid is None or pid in seen_pids:
            continue
        seen_pids.add(pid)
        try:
            process_name = psutil.Process(pid).name()
        except (psutil.NoSuchProcess, psutil.AccessDenied, Exception):
            continue
        if session_manager.is_exempt(process_name, pid):
            continue
        if not is_blocked_window(process_name, pid):
            continue

        was_all_minimized = _all_windows_minimized(pid)
        # Idempotent, like enforcer.py's own _hide_taskbar_preview -- safe to
        # reapply every tick even if this pid is already hidden.
        _hide_app(pid, True)
        if not was_all_minimized:
            if _minimize_all_windows(pid):
                minimized.append((process_name, pid))

    return minimized


def show_blocked_notice(process_name):
    """Same role as enforcer.show_blocked_notice on Windows: shows the lock
    overlay for a window sweep_minimize_blocked_windows() just caught,
    without redirecting whatever's actually frontmost right now."""
    status = session_manager.get_status()
    if status.get("source") == "review":
        message = f"Finish {status.get('reviewProblemName') or 'this review'} first"
    else:
        message = f"{process_name} is blocked and was minimized."
    _show_lock_overlay(message, duration_ms=5000, offending_process_name=process_name)


def restore_window_for_process(process_name):
    """Un-hides and activates process_name's app, if it's running --
    same role as enforcer.restore_window_for_process on Windows, called
    right after a mid-session unblock."""
    pid = _find_pid_by_process_name(process_name)
    if not pid:
        return
    _activate(pid)
    _hide_app(pid, False)


def _show_lock_overlay(message, duration_ms, offending_process_name=None):
    """Same Qt overlay mechanism as enforcer.py's Windows _show_lock_overlay --
    qt_ui/enforcer_overlay.py has no win32 dependency of its own, so this is
    a thin, deliberately duplicated wrapper rather than a shared import from
    enforcer.py (which is guarded behind a platform check that would make
    importing anything from it on macOS fragile). blackout_rect is always
    None here -- see soft_lock_warning's docstring for why."""
    qt_gui_thread.run_on_gui_thread(
        lambda: enforcer_overlay.build_overlay(
            message, duration_ms, offending_process_name, blackout_rect=None
        )
    )
