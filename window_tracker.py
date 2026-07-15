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


def run_polling_loop(stop_event):
    """Runs until stop_event is set. Intended to be launched in its own thread."""
    last_flagged_process = None

    while not stop_event.is_set():
        try:
            if session_manager.is_active():
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
                        else:
                            enforcer.soft_lock_warning()
            else:
                last_flagged_process = None
        except Exception:
            pass

        stop_event.wait(POLL_INTERVAL_SECONDS)
