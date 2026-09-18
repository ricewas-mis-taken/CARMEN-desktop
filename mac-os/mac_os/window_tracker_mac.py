"""macOS active-window detection and window enumeration -- replaces
window_tracker.py's win32gui/win32process calls. See enforcer_mac.py for the
actual enforcement (minimize/hide/activate) logic that consumes these.

window_tracker.py's own run_polling_loop() is genuinely platform-agnostic --
it only ever calls get_active_window() and enforcer.* -- so it stays defined
exactly once in window_tracker.py, unconditionally, rather than being
duplicated here. Only the window-lookup primitives below are swapped in via
window_tracker.py's dispatch shim.
"""
import psutil
from AppKit import NSWorkspace
from Quartz import (
    CGWindowListCopyWindowInfo,
    kCGNullWindowID,
    kCGWindowListOptionOnScreenOnly,
)

# Same values as window_tracker.py's Windows constants -- run_polling_loop
# uses whichever module bound these names (see window_tracker.py's dispatch
# shim), so they must stay in sync if the poll cadence/cooldowns ever change.
POLL_INTERVAL_SECONDS = 1.5
HARD_REDIRECT_COOLDOWN_SECONDS = POLL_INTERVAL_SECONDS * 3
VIOLATION_COOLDOWN_SECONDS = 5


def get_active_window():
    """Equivalent of window_tracker.get_active_window(). There's no "hwnd"
    concept on macOS -- every call site that threads a window handle through
    (enforcer.is_blocked_window, sweep_minimize_blocked_windows, the
    hard-redirect cooldown dict in run_polling_loop, etc.) only ever uses it
    as an opaque equality-comparable/dict-key value, never a real handle, so
    the frontmost app's pid is a safe stand-in everywhere "hwnd" is used.
    (The one feature that genuinely needs true per-window identity --
    per-Chrome/Edge-profile blocking via AUMI -- has no macOS equivalent yet;
    see PORT_SPEC.md's Subsystem 3 findings. It stays Windows-only.)"""
    title = None
    process_name = None
    pid = None
    try:
        active_app = NSWorkspace.sharedWorkspace().frontmostApplication()
        if active_app is not None:
            pid = active_app.processIdentifier()
            try:
                process_name = psutil.Process(pid).name()
            except (psutil.NoSuchProcess, psutil.AccessDenied, Exception):
                process_name = None
            try:
                window_list = CGWindowListCopyWindowInfo(
                    kCGWindowListOptionOnScreenOnly, kCGNullWindowID
                ) or []
                for w in window_list:
                    if w.get("kCGWindowOwnerPID") == pid and w.get("kCGWindowLayer") == 0:
                        title = w.get("kCGWindowName") or title
                        break
            except Exception:
                pass
    except Exception:
        pass
    return {"title": title, "process_name": process_name, "pid": pid, "hwnd": pid}


def list_running_apps():
    """Equivalent of window_tracker.list_running_apps() -- one entry per
    unique process name (first window title found for it), for the app
    picker. Returns [] if window enumeration itself fails (e.g. Screen
    Recording permission not yet granted -- CGWindowListCopyWindowInfo can
    return window owner names without it, but this mirrors the Windows
    version's own "never crash the picker" defensiveness either way)."""
    apps = {}
    try:
        window_list = CGWindowListCopyWindowInfo(
            kCGWindowListOptionOnScreenOnly, kCGNullWindowID
        ) or []
    except Exception:
        return []

    for w in window_list:
        title = w.get("kCGWindowName")
        pid = w.get("kCGWindowOwnerPID")
        if not title or pid is None:
            continue
        try:
            process_name = psutil.Process(pid).name()
        except (psutil.NoSuchProcess, psutil.AccessDenied, Exception):
            continue
        key = process_name.lower()
        if key not in apps:
            apps[key] = {"process_name": process_name, "window_title": title}

    return list(apps.values())


def list_browser_profile_windows():
    """Per-Chrome/Edge-profile window identification (AUMI on Windows) has
    no confirmed macOS equivalent yet -- the PORT_SPEC.md Subsystem 3
    research spike needs a real Mac to run and hasn't happened. Per that
    section's own documented fallback, per-profile blocking stays a
    Windows-only feature for now; the base "block the whole browser" case
    (session_manager.is_blocked(process_name)) already works identically on
    both platforms with no changes needed. Always returns []."""
    return []


def list_known_browser_profiles():
    """Same Subsystem 3 gap as list_browser_profile_windows() -- always []
    until that spike is run on a real Mac and this is implemented for real."""
    return []
