"""Active window detection and the polling loop that drives enforcement."""
import time

import psutil
import win32gui
import win32process

import enforcer
import session_manager

POLL_INTERVAL_SECONDS = 1.5


def get_active_window():
    hwnd = win32gui.GetForegroundWindow()
    title = win32gui.GetWindowText(hwnd)
    process_name = None
    pid = None
    try:
        _, pid = win32process.GetWindowThreadProcessId(hwnd)
        process_name = psutil.Process(pid).name()
    except (psutil.NoSuchProcess, psutil.AccessDenied, Exception):
        process_name = None
    return {"title": title, "process_name": process_name, "pid": pid}


def list_running_apps():
    """Enumerates visible top-level windows and returns one entry per unique
    process name (first window title found for it), for the app picker."""
    apps = {}

    def callback(hwnd, _):
        if not win32gui.IsWindowVisible(hwnd):
            return
        title = win32gui.GetWindowText(hwnd)
        if not title:
            return
        try:
            _, pid = win32process.GetWindowThreadProcessId(hwnd)
            process_name = psutil.Process(pid).name()
        except (psutil.NoSuchProcess, psutil.AccessDenied, Exception):
            return
        key = process_name.lower()
        if key not in apps:
            apps[key] = {"process_name": process_name, "window_title": title}

    win32gui.EnumWindows(callback, None)
    return list(apps.values())


def run_polling_loop(stop_event, on_session_end=None):
    """Runs until stop_event is set. Intended to be launched in its own thread.

    on_session_end(summary), if given, is called once whenever a session's
    timer runs out naturally (as opposed to being ended manually via the tray
    menu or POST /session/end, which notify their own caller directly). This
    loop calls get_status() every tick specifically so that self-finalization
    happens here even if nothing else (browser extension, lock overlay) is
    polling /status — otherwise a session could expire with no notification
    ever firing.
    """
    last_flagged_process = None

    while not stop_event.is_set():
        try:
            status = session_manager.get_status()

            pending = session_manager.pop_pending_natural_end()
            if pending is not None and on_session_end is not None:
                try:
                    on_session_end(pending)
                except Exception:
                    pass

            if status["isActive"]:
                window = get_active_window()
                process_name = window["process_name"]
                pid = window["pid"]

                if session_manager.is_exempt(process_name, pid):
                    # Core shell/system processes (taskbar, alt-tab, wifi/time
                    # flyouts) and our own tray/popup windows are never
                    # violations — don't touch dedupe state either way.
                    pass
                elif process_name and session_manager.is_whitelisted(process_name):
                    session_manager.record_acceptable(process_name)
                    last_flagged_process = None
                elif process_name:
                    if process_name != last_flagged_process:
                        last_flagged_process = process_name
                        session_manager.record_violation(process_name)
                        lock_mode = session_manager.get_lock_mode()
                        if lock_mode == "hard":
                            enforcer.hard_lock_redirect(process_name)
                            # hard_lock_redirect() forces focus back to the
                            # whitelisted app right here, so the dedupe check
                            # above must not keep treating this process as
                            # "already handled" — if the user alt-tabs straight
                            # back to it before the next tick observes the
                            # whitelisted app (which is what normally resets
                            # this via record_acceptable), the reopened app
                            # would otherwise compare equal and get skipped,
                            # silently defeating hard lock.
                            last_flagged_process = None
                        else:
                            enforcer.soft_lock_warning()
            else:
                last_flagged_process = None
        except Exception:
            pass

        stop_event.wait(POLL_INTERVAL_SECONDS)
