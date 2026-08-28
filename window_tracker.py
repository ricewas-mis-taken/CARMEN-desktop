"""Active window detection and the polling loop that drives enforcement."""
import time

import psutil
import win32gui
import win32process

import enforcer
import session_manager

POLL_INTERVAL_SECONDS = 1.5

# Some apps (observed with Discord) don't actually leave the foreground when
# hard_lock_redirect() minimizes them -- a stray popup/overlay window belonging
# to the same process regrabs focus almost immediately, or the redirect's
# SetForegroundWindow race loses to the app re-asserting itself. Since the
# hard-lock branch below resets last_flagged_process to None right after
# redirecting (so a genuine re-open by the user still counts as a fresh
# violation), a process that never actually leaves foreground was retriggering
# hard_lock_redirect() on *every* poll tick -- each call re-issuing
# SW_MINIMIZE/SetForegroundWindow (visible as the app's window flashing) and
# spawning another lock overlay (visible as several piling up on screen) every
# 1.5s for as long as it stayed stuck. This cooldown limits how often the same
# offending process can be redirected, without touching how often violations
# are recorded.
HARD_REDIRECT_COOLDOWN_SECONDS = POLL_INTERVAL_SECONDS * 3
# Minimum gap between logging two violations for the same process -- prevents
# rapid-fire entries when a background app briefly steals focus repeatedly.
VIOLATION_COOLDOWN_SECONDS = 5


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
    return {"title": title, "process_name": process_name, "pid": pid, "hwnd": hwnd}


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


def list_browser_profile_windows():
    """Enumerates visible top-level windows belonging to a multi-profile
    browser (session_manager.MULTI_PROFILE_BROWSER_PROCESSES) and returns
    one entry per unique AUMI -- one per currently-open profile window --
    for the app picker's "block just one browser profile" section. Only
    profiles with a window open *right now* can be offered, since a
    profile's AUMI isn't known until Windows actually reads it off a real
    window (see enforcer.get_window_aumi)."""
    seen = {}

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
        if process_name.lower() not in session_manager.MULTI_PROFILE_BROWSER_PROCESSES:
            return
        aumi = enforcer.get_window_aumi(hwnd)
        if not aumi or aumi in seen:
            return
        seen[aumi] = {
            "process_name": process_name,
            "aumi": aumi,
            "label": enforcer.describe_browser_profile_aumi(process_name, aumi),
            "window_title": title,
        }

    win32gui.EnumWindows(callback, None)
    return list(seen.values())


def run_polling_loop(stop_event, on_session_end=None, tray_icon=None):
    """Runs until stop_event is set. Intended to be launched in its own thread.

    on_session_end(summary), if given, is called once whenever a session's
    timer runs out naturally (as opposed to being ended manually via the tray
    menu or POST /session/end, which notify their own caller directly). This
    loop calls get_status() every tick specifically so that self-finalization
    happens here even if nothing else (browser extension, lock overlay) is
    polling /status — otherwise a session could expire with no notification
    ever firing.

    tray_icon, if given, gets update_menu() called whenever isActive/isPaused
    changes — pystray's win32 backend only rebuilds its popup menu (and so
    only re-evaluates each MenuItem's `visible` callable) when told to, not
    automatically on every right-click. Without this, the tray's Pause/Resume
    and End Session (Nuclear) items — which are only meant to show up while a
    session is running — would keep showing whatever visibility they had the
    last time update_menu() happened to run, regardless of session state
    changes made elsewhere (the API, a session ending naturally, etc).
    """
    last_flagged_process = None
    last_menu_state = None
    last_hard_redirect = {"process": None, "hwnd": None, "time": 0.0}
    last_violation_time = {}  # process_name -> time.time() of last logged violation
    last_sweep_notice = {}  # hwnd -> time.time() of last overlay shown for that window

    while not stop_event.is_set():
        try:
            status = session_manager.get_status()

            if tray_icon is not None:
                menu_state = (status["isActive"], status["isPaused"])
                if menu_state != last_menu_state:
                    last_menu_state = menu_state
                    try:
                        tray_icon.update_menu()
                    except Exception:
                        pass

            pending = session_manager.pop_pending_natural_end()
            if pending is not None and on_session_end is not None:
                try:
                    on_session_end(pending)
                except Exception:
                    pass

            if status["isActive"] and not status["isPaused"]:
                window = get_active_window()
                process_name = window["process_name"]
                pid = window["pid"]
                hwnd = window["hwnd"]

                if session_manager.is_exempt(process_name, pid):
                    # Core shell/system processes (taskbar, alt-tab, wifi/time
                    # flyouts) and our own tray/popup windows are never
                    # violations — don't touch dedupe state either way.
                    pass
                elif process_name and enforcer.is_blocked_window(process_name, hwnd):
                    if process_name != last_flagged_process:
                        last_flagged_process = process_name
                        now = time.time()
                        if now - last_violation_time.get(process_name, 0) >= VIOLATION_COOLDOWN_SECONDS:
                            last_violation_time[process_name] = now
                            session_manager.record_violation(process_name)
                        lock_mode = session_manager.get_lock_mode()
                        if lock_mode == "hard":
                            now = time.time()
                            # Keyed on hwnd, not just process name -- a
                            # process name match alone can't tell "this exact
                            # window never actually left the foreground"
                            # (the Discord-popup case this cooldown exists
                            # for) apart from "the user closed it and
                            # reopened a brand new window of the same app"
                            # (a fresh violation that deserves an immediate
                            # redirect, not a wait for the old window's
                            # cooldown to expire).
                            recently_redirected = (
                                last_hard_redirect["process"] == process_name
                                and last_hard_redirect["hwnd"] == hwnd
                                and (now - last_hard_redirect["time"]) < HARD_REDIRECT_COOLDOWN_SECONDS
                            )
                            if not recently_redirected:
                                enforcer.hard_lock_redirect(process_name)
                                last_hard_redirect["process"] = process_name
                                last_hard_redirect["hwnd"] = hwnd
                                last_hard_redirect["time"] = now
                            # hard_lock_redirect() forces focus back to the
                            # last acceptable (non-blocklisted) app right
                            # here, so the dedupe check above must not keep
                            # treating this process as "already handled" — if
                            # the user alt-tabs straight back to it before the
                            # next tick observes an acceptable app (which is
                            # what normally resets this via
                            # record_acceptable), the reopened app would
                            # otherwise compare equal and get skipped,
                            # silently defeating hard lock. The cooldown above
                            # (not this reset) is what stops a stuck-in-
                            # foreground app from spamming redirects/overlays.
                            last_flagged_process = None
                        else:
                            enforcer.soft_lock_warning(process_name, hwnd)
                elif process_name:
                    session_manager.record_acceptable(process_name)
                    last_flagged_process = None

                # Everything above only ever looks at whichever window is
                # currently in the foreground on this tick. That misses two
                # cases entirely: a blocklisted app already open (but not
                # focused) when the session started, and the same app
                # reopened right after being minimized -- if it doesn't
                # happen to be the foreground window at the exact moment of
                # a poll tick, it just sits there open and usable. Sweeping
                # every visible window each tick in hard-lock mode closes
                # that gap independently of focus.
                if session_manager.get_lock_mode() == "hard":
                    swept = enforcer.sweep_minimize_blocked_windows()
                    for swept_process, swept_hwnd in swept:
                        now = time.time()
                        # Deliberately two *independent* cooldowns, not one
                        # shared check -- this used to gate both the
                        # violation log AND the notice on the same
                        # last_violation_time entry, which is wrong: the
                        # foreground branch above can log a violation for
                        # this exact process moments earlier (e.g. right
                        # before hard_lock_redirect() itself gets
                        # cooldown-suppressed for still being the same
                        # window), which then also silently blocked the
                        # notice here for the next several seconds even
                        # though the window kept getting re-minimized on
                        # every tick in that gap -- exactly "the app won't
                        # even appear, but there's no message at all".
                        if now - last_violation_time.get(swept_process, 0) >= VIOLATION_COOLDOWN_SECONDS:
                            last_violation_time[swept_process] = now
                            session_manager.record_violation(swept_process)
                        # Keyed on hwnd (like HARD_REDIRECT_COOLDOWN_SECONDS
                        # above) so a genuinely different window -- the user
                        # closed and relaunched the app, or this is simply
                        # the first time this window's been caught -- always
                        # gets an immediate notice, while a window that
                        # keeps re-appearing in `swept` every single tick
                        # (the stuck-popup case, e.g. Discord) doesn't spam
                        # a fresh overlay every 1.5s forever.
                        if now - last_sweep_notice.get(swept_hwnd, 0) >= HARD_REDIRECT_COOLDOWN_SECONDS:
                            last_sweep_notice[swept_hwnd] = now
                            enforcer.show_blocked_notice(swept_process)
            else:
                # Reset dedupe state when paused or idle so the first process
                # seen after resuming is always evaluated fresh.
                last_flagged_process = None
        except Exception:
            pass

        stop_event.wait(POLL_INTERVAL_SECONDS)
