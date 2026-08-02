"""Soft/hard lock enforcement actions."""
import psutil
import win32con
import win32gui
import win32process

import qt_gui_thread
import qt_ui.enforcer_overlay as enforcer_overlay
import session_manager


def soft_lock_warning(offending_process_name=None):
    status = session_manager.get_status()
    if status.get("source") == "review":
        message = f"Finish {status.get('eventTitle') or 'this review'} first"
    else:
        last_ok = status["lastAcceptableProcess"] or "your focus app"
        message = f"You're off track — back to {last_ok}?"
    _show_lock_overlay(
        message,
        duration_ms=5000,
        offending_process_name=offending_process_name,
    )


def hard_lock_redirect(offending_process_name=None):
    """Minimizes the offending foreground window (unless it's exempt/our own
    process), then brings the last acceptable (non-blocklisted) app's window
    back to the foreground without disturbing its size or snap position.

    Note: a previous version of this deliberately skipped minimizing the
    offending window at all, after it turned out to close a lightweight
    background WPM-tracker app outright instead of minimizing it (some
    fragile/minimal apps mishandle a forced SW_MINIMIZE that way). Minimizing
    is back by explicit request — actually enforcing hard lock means the
    offending window shouldn't still be sitting there — with the understanding
    that this same class of fragile app could in principle hit the same issue
    again. It's wrapped in try/except and skipped entirely for exempt/system
    processes, which is the extent of the safety net here."""
    hwnd = win32gui.GetForegroundWindow()
    hwnd_process = None
    hwnd_pid = None
    try:
        _, hwnd_pid = win32process.GetWindowThreadProcessId(hwnd)
        hwnd_process = psutil.Process(hwnd_pid).name()
    except Exception:
        pass

    # Re-check against the blocklist too, not just is_exempt() — the
    # foreground window can change between the polling tick that detected
    # this violation and this call actually running (e.g. the user already
    # switched to an allowed app in that gap). Minimizing whatever happens
    # to be foreground right now without this check could minimize a
    # window that's no longer the violation at all.
    if (
        hwnd
        and not session_manager.is_exempt(hwnd_process, hwnd_pid)
        and hwnd_process
        and session_manager.is_blocked(hwnd_process)
    ):
        try:
            win32gui.ShowWindow(hwnd, win32con.SW_MINIMIZE)
        except Exception:
            pass

    last_acceptable = session_manager.get_last_acceptable_process()
    target_hwnd = _find_window_by_process_name(last_acceptable) if last_acceptable else None
    if target_hwnd:
        try:
            # Only restore if it's actually minimized — calling SW_RESTORE on
            # a window that's already visible (e.g. snapped to half the
            # screen) can reset it back to its pre-snap size, which is the
            # "other window shrinks" bug this guards against.
            if win32gui.IsIconic(target_hwnd):
                win32gui.ShowWindow(target_hwnd, win32con.SW_RESTORE)
            win32gui.SetForegroundWindow(target_hwnd)
        except Exception:
            pass

    label = offending_process_name or hwnd_process or "that app"
    status = session_manager.get_status()
    if status.get("source") == "review":
        message = f"Finish {status.get('eventTitle') or 'this review'} first"
    else:
        back_to = last_acceptable or "your focus app"
        message = f"Redirected from {label} — back to {back_to}."
    _show_lock_overlay(
        message,
        duration_ms=3000,
        offending_process_name=label if label != "that app" else None,
    )


def sweep_minimize_blocked_windows():
    """Minimizes every visible, non-iconic window belonging to a blocklisted
    process -- not just whichever one happens to be in the foreground on a
    given poll tick. hard_lock_redirect() alone only ever reacts to the
    current foreground window, so a blocklisted app that's open in the
    background (already running before the session started, or reopened in
    the gap between two polls without ever becoming the foreground window)
    would otherwise just sit there, unminimized and usable, until the user
    happened to switch to it directly."""
    def callback(hwnd, _):
        if not win32gui.IsWindowVisible(hwnd) or win32gui.IsIconic(hwnd):
            return
        if not win32gui.GetWindowText(hwnd):
            return
        try:
            _, pid = win32process.GetWindowThreadProcessId(hwnd)
            name = psutil.Process(pid).name()
        except (psutil.NoSuchProcess, psutil.AccessDenied, Exception):
            return
        if session_manager.is_exempt(name, pid):
            return
        if session_manager.is_blocked(name):
            try:
                win32gui.ShowWindow(hwnd, win32con.SW_MINIMIZE)
            except Exception:
                pass

    win32gui.EnumWindows(callback, None)


def _find_window_by_process_name(process_name):
    found = {"hwnd": None}

    def callback(hwnd, _):
        if found["hwnd"] is not None:
            return
        if not win32gui.IsWindowVisible(hwnd):
            return
        if not win32gui.GetWindowText(hwnd):
            return
        try:
            _, pid = win32process.GetWindowThreadProcessId(hwnd)
            name = psutil.Process(pid).name()
        except (psutil.NoSuchProcess, psutil.AccessDenied, Exception):
            return
        if name.lower() == process_name.lower():
            found["hwnd"] = hwnd

    win32gui.EnumWindows(callback, None)
    return found["hwnd"]


def _show_lock_overlay(message, duration_ms, offending_process_name=None):
    """Shows a small always-on-top, borderless popup for duration_ms while a
    progress bar fills, then closes automatically. It repeatedly raises and
    refocuses itself so it's hard to ignore, but deliberately does not take
    a system-wide input grab -- that would freeze every other running app
    (any background exe's window, tray flyouts, etc.), not just the
    offending one.

    Built on the Qt main thread via qt_gui_thread.run_on_gui_thread() rather
    than constructed here directly -- Qt widgets, like the Tk widgets this
    replaced, may only be touched from the thread that owns the
    QApplication (this app's main thread; see main.py), and this function
    runs on window_tracker's polling thread instead.

    Guarded two ways against ever getting stuck open: the overlay's own
    tick-driven close, and a backup timer -- see qt_ui/enforcer_overlay.py.

    offending_process_name, when known, adds an "Unblock" button -- lets
    the user let that exe through for the rest of the session without
    ending hard/soft lock enforcement entirely, same as the "Pick Apps to
    Blocklist" picker's own removal flow, just reachable from the moment of
    redirect itself.
    """
    qt_gui_thread.run_on_gui_thread(
        lambda: enforcer_overlay.build_overlay(message, duration_ms, offending_process_name)
    )
