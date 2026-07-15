"""Soft/hard lock enforcement actions."""
import time
import tkinter as tk

import psutil
import win32con
import win32gui
import win32process

import gui_thread
import session_manager


def soft_lock_warning():
    status = session_manager.get_status()
    last_ok = status["lastAcceptableProcess"] or "your focus app"
    _show_lock_overlay(f"You're off track — back to {last_ok}?", duration_ms=5000)


def hard_lock_redirect(offending_process_name=None):
    """Minimizes the offending foreground window (unless it's exempt/our own
    process), then brings the last whitelisted app's window back to the
    foreground without disturbing its size or snap position.

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

    # Re-check against the whitelist too, not just is_exempt() — the
    # foreground window can change between the polling tick that detected
    # this violation and this call actually running (e.g. the user already
    # switched to an allowed app in that gap). Minimizing whatever happens
    # to be foreground right now without this check could minimize a
    # window that's no longer the violation at all.
    if (
        hwnd
        and not session_manager.is_exempt(hwnd_process, hwnd_pid)
        and not (hwnd_process and session_manager.is_whitelisted(hwnd_process))
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
    back_to = last_acceptable or "your focus app"
    _show_lock_overlay(
        f"Redirected from {label} — back to {back_to}.",
        duration_ms=3000,
    )


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


def _show_lock_overlay(message, duration_ms):
    """Shows a small always-on-top, borderless popup for duration_ms while a
    green progress bar fills, then closes automatically. It repeatedly lifts
    and refocuses itself so it's hard to ignore, but deliberately does not
    take a system-wide input grab — that would freeze every other running
    app (any background exe's window, tray flyouts, etc.), not just the
    offending one.

    Built as a Toplevel on the single shared GUI-thread root (gui_thread.py)
    rather than its own Tk() in its own thread — two Tk() roots alive in two
    different threads at once (e.g. this overlay firing while the whitelist
    picker is open) crashes the whole process with a fatal Tcl error, so all
    popups share one root/thread instead.

    Guarded two ways against ever getting stuck open: the normal
    tick-driven close, and a backup `.after()` timer.
    """
    gui_thread.run_on_gui_thread(lambda root: _build_overlay(root, message, duration_ms))


def _build_overlay(root, message, duration_ms):
    width, height = 380, 150
    bar_width = width - 40

    win = tk.Toplevel(root)
    win.title("Carmen Focus")
    win.overrideredirect(True)
    win.attributes("-topmost", True)
    win.configure(bg="#1e1e1e")

    screen_width = win.winfo_screenwidth()
    screen_height = win.winfo_screenheight()
    x = (screen_width - width) // 2
    y = (screen_height - height) // 2
    win.geometry(f"{width}x{height}+{x}+{y}")

    tk.Label(
        win, text=message, font=("Segoe UI", 11), wraplength=340,
        bg="#1e1e1e", fg="white", justify="center",
    ).pack(pady=(18, 6))

    time_label = tk.Label(win, font=("Segoe UI", 9), bg="#1e1e1e", fg="#aaaaaa")
    time_label.pack()

    bar_bg = tk.Frame(win, bg="#3a3a3a", height=8, width=bar_width)
    bar_bg.pack(pady=(14, 0))
    bar_bg.pack_propagate(False)
    bar_fill = tk.Frame(bar_bg, bg="#2ecc71", height=8, width=0)
    bar_fill.place(x=0, y=0, relheight=1, width=0)

    state = {"closed": False}

    def close():
        if state["closed"]:
            return
        state["closed"] = True
        try:
            win.destroy()
        except Exception:
            pass

    # Hard safety net independent of the tick loop below — guarantees the
    # popup closes even if something in tick() raises.
    win.after(duration_ms + 1000, close)

    start = time.time()

    def tick():
        if state["closed"]:
            return
        elapsed_ms = (time.time() - start) * 1000
        fraction = min(1.0, elapsed_ms / duration_ms)
        bar_fill.place(width=int(bar_width * fraction))

        status = session_manager.get_status()
        minutes, seconds = divmod(status["secondsRemaining"], 60)
        time_label.config(text=f"Time remaining: {minutes}m {seconds}s")

        # Keep this popup on top of the screen without grabbing system-wide
        # input — a global grab would freeze every other running app (any
        # background exe's window, tray flyouts, etc.), not just the
        # offending one. Repeatedly lifting/re-focusing just this window
        # keeps it hard to ignore while leaving everything else responsive.
        try:
            win.lift()
            win.focus_force()
        except Exception:
            pass

        if fraction >= 1.0:
            close()
            return
        win.after(50, tick)

    tick()
