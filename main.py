"""Entry point — starts the API server, window-polling loop, and pystray's
detached tray thread as background threads, then runs Qt's event loop on
the main thread (Qt requirement: only the thread that constructs
QApplication may create/touch widgets — see qt_gui_thread.py)."""
import os
import subprocess
import sys
import threading
import theme
import time

from PySide6.QtWidgets import QApplication, QMessageBox

from calendar_log import logger

try:                                    # a hard crash of the interpreter or Qt leaves its stack here
    import faulthandler
    import paths as _paths
    _crash_file = open(_paths.private_path("crash.log"), "a", buffering=1)
    faulthandler.enable(_crash_file, all_threads=True)
except Exception:
    pass

import api_server
import auto_updater
from qt_ui import ui_kit
import autostart
import desktop_menu
import calendar_scheduler
import calendar_toast
import config
import code_watcher
import dev_watcher
import enforcer
import qt_gui_thread
import screentime_store
import singleinstance
import sync_live
import sync_realtime
import sync_scheduler
import taskbar_quiet
import tray
import ui_watchdog
import window_tracker
from api_server import run_server

stop_event = threading.Event()
DEV_MODE = "--dev" in sys.argv[1:]
# Started by the desktop right-click "QuickNote" when the app was not running yet.
OPEN_QUICKNOTE = "--quicknote" in sys.argv[1:]
AUTOSTARTED = "--autostart" in sys.argv[1:]

STYLESHEET_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "styles.qss")


def _load_stylesheet(app):
    try:
        with open(STYLESHEET_PATH, "r", encoding="utf-8") as f:
            app.setStyleSheet(ui_kit.with_check_icon(theme.apply_to_stylesheet(f.read(), theme.startup_colours())))
    except OSError:
        pass  # missing/unreadable stylesheet must never block the app from starting


def _check_accessibility_trust():
    """macOS-only: every hard/soft lock mechanism in mac_os/enforcer_mac.py
    needs Accessibility permission (System Settings -> Privacy & Security ->
    Accessibility) -- there's no way around this, Apple gates all
    cross-application window control behind it. Per PORT_SPEC.md's Subsystem
    2, this must be an explicit, visible onboarding step re-checked on every
    launch (the user can revoke it later in System Settings), not a silent
    background check -- a session that *thinks* it's enforcing but silently
    can't touch any window would be worse than not having the feature.

    This shows a persistent warning rather than blocking session-start
    outright (the stricter of the two options PORT_SPEC.md allows) -- fully
    gating "Start Focus Session" on trust status is real, separate follow-up
    work (touching qt_ui/focus_tab.py's start button, not just main.py's
    startup path) that hasn't been done yet; flagged in PORT_SPEC.md."""
    if sys.platform != "darwin":
        return
    # AXIsProcessTrustedWithOptions's prompt option makes macOS pop its own
    # system permission dialog the first time this runs if not already
    # granted -- see enforcer_mac.is_accessibility_trusted's docstring.
    if enforcer.is_accessibility_trusted(prompt=True):
        return
    box = QMessageBox()
    box.setIcon(QMessageBox.Warning)
    box.setWindowTitle("Carmen Focus needs Accessibility access")
    box.setText(
        "Carmen Focus can't enforce focus sessions on macOS without "
        "Accessibility permission. Until it's granted, hard/soft lock will "
        "silently do nothing."
    )
    open_settings_button = box.addButton("Open System Settings", QMessageBox.ActionRole)
    box.addButton("Later", QMessageBox.RejectRole)
    box.exec()
    if box.clickedButton() is open_settings_button:
        try:
            subprocess.run(
                ["open", "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility"],
                check=False,
            )
        except Exception:
            pass


def main():
    singleinstance.acquire()
    config.load_config()
    calendar_toast.set_app_id()
    autostart.ensure_autostart_registered()
    desktop_menu.ensure_registered()

    # QApplication must be constructed here, on what becomes "the Qt main
    # thread" (this thread), before qt_gui_thread.start() and before any
    # background thread exists that could reach into Qt (the polling
    # thread below is the one that matters most — it can trigger
    # enforcer.py's lock overlays at any time once it starts).
    app = QApplication(sys.argv)
    # Background threads (sync, saving, the API) share the interpreter lock with the interface. The default
    # lets one of them hold it for 5 ms at a time, which is felt under a pen; 0.5 ms hands it back sooner.
    sys.setswitchinterval(0.0005)
    import gc
    gc.set_threshold(10000, 20, 20)     # collect far less often: a big Scribe board holds ~200k objects
    import qt_ui.popup_fit as popup_fit
    popup_fit.install(app)                      # every popup opens a sensible size, on screen
    from PySide6.QtGui import QIcon
    app.setWindowIcon(QIcon(os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "carmen_icon.ico")))  # windows and the taskbar
    # Tray-resident app: closing the last visible window (the calendar
    # window, a lock overlay auto-closing, etc.) must never exit the
    # process on its own — only "Quit" from the tray menu should.
    app.setQuitOnLastWindowClosed(False)
    ui_kit.force_light_scheme()
    _load_stylesheet(app)
    _check_accessibility_trust()

    qt_gui_thread.start()
    ui_watchdog.start()

    if OPEN_QUICKNOTE:
        import qt_ui.quicknote as quicknote
        qt_gui_thread.run_on_gui_thread(quicknote.open_quicknote)

    if os.environ.pop("CARMEN_RESTARTED", None):
        # The app restarted itself into new code: the window comes back where it was.
        import qt_ui.main_window as main_window
        qt_gui_thread.run_on_gui_thread(main_window.reopen_after_restart)

    elif AUTOSTARTED:
        if config.load_config().get("openOnStartup", True):
            import qt_ui.main_window as main_window
            qt_gui_thread.run_on_gui_thread(main_window.open_main_window)

    if DEV_MODE:
        # Opens the main window automatically on launch/dev-restart, purely
        # a dev-loop convenience so window-geometry changes can be reverified
        # without a manual tray-icon click each time -- never runs outside
        # --dev.
        import qt_ui.main_window as main_window
        qt_gui_thread.run_on_gui_thread(main_window.open_main_window)

    # A crash or kill mid-session can leave the taskbar badges/flashing
    # switched off; put them back before anything decides what is wanted now.
    taskbar_quiet.restore_all()

    api_thread = threading.Thread(target=run_server, daemon=True)
    api_thread.start()

    # The calendar's reminders and auto-start-at-event-time are parked with the
    # Calendar tab (see archive/README.md): with no way to see or edit events,
    # a leftover one would still start a session out of nowhere.
    # calendar_scheduler.start(stop_event)
    sync_scheduler.start(stop_event)
    sync_live.start(stop_event)
    sync_realtime.start(stop_event)

    def _refresh_open_window(tables):
        import qt_ui.main_window as main_window
        qt_gui_thread.run_on_gui_thread(lambda: main_window.refresh_after_pull(tables))

    import sync_client
    sync_client.add_pulled_listener(_refresh_open_window)

    def on_quit():
        stop_event.set()
        # The periodic debounce inside screentime_store.add_app_seconds()
        # only flushes to disk every ~10s -- without an explicit flush here,
        # up to that much of the day's tally could be lost on a clean quit.
        screentime_store.flush()
        taskbar_quiet.restore_all()
        # Must go through qt_gui_thread's queued marshal, not a direct
        # QApplication.instance().quit() — on_quit() itself runs on
        # pystray's callback thread (see on_quit_clicked in tray.py), and a
        # direct cross-thread .quit() call was confirmed (via a throwaway
        # spike) to just hang the process forever instead of exiting.
        qt_gui_thread.quit_app()

    # Built before the polling thread starts so the polling loop can notify
    # through it when a session's timer runs out naturally (see
    # window_tracker.run_polling_loop's on_session_end).
    icon = tray.build_tray_icon(on_quit)

    def on_external_quit():
        # Lets a future instance's singleinstance.acquire() ask this process
        # to shut down cleanly over loopback HTTP (POST /internal/quit)
        # instead of hard-killing it -- see singleinstance.py for why that
        # distinction matters. Needs icon.stop() alongside on_quit(), same
        # as on_quit_clicked in tray.py and on_dev_restart below -- on_quit()
        # alone stops the Qt loop but never calls Shell_NotifyIcon(NIM_DELETE),
        # so without this the tray icon outlives the process as a ghost icon
        # exactly like a hard kill would.
        on_quit()
        icon.stop()

    api_server.register_quit_callback(on_external_quit)

    # Reuses on_quit's exact shutdown path -- the only difference between a
    # user-initiated Quit and an auto-update restart is what main() does
    # after app.exec() returns (os._exit vs. os.execv into the freshly
    # pulled code; see below).
    # Updates no longer restart the app: a button in the window applies them to the running copy.
    import update_center
    update_center.start_baseline()
    update_center.relaunch_hook = lambda: on_quit()
    update_center.watch(stop_event)

    def _busy():
        """A session, a review or a first attempt is running: not a moment to restart."""
        import review_store
        import session_manager
        return bool(session_manager.get_status().get("isActive") or review_store.get_active_review()
                    or review_store.get_first_attempt())

    def _window_open():
        import qt_ui.main_window as main_window
        return main_window.remembered()["open"]

    def on_code_changed():
        """New code on disk and it imports cleanly: offer it as an update. Nothing restarts; the window's
        Update button applies it to the running app, and the watcher starts again from the new state."""
        code_watcher.clear_restart_request()
        update_center.set_local(True)

    code_watcher.start(stop_event, on_code_changed, lambda: False, lambda: True, lambda: 10 ** 6)
    update_center.watcher_restart = lambda: code_watcher.start(stop_event, on_code_changed, lambda: False,
                                                               lambda: True, lambda: 10 ** 6)

    if DEV_MODE:
        def on_dev_restart():
            # Same shutdown path as a normal Quit/auto-update restart, plus
            # an explicit icon.stop() (normally left to on_quit_clicked in
            # tray.py) -- without it the shell notify icon isn't removed
            # before the process image is replaced by os.execv below, and
            # Explorer leaves a ghost icon behind until moused over.
            on_quit()
            icon.stop()

        dev_watcher.start(on_dev_restart)

    def on_session_end(summary):
        # A real Windows toast (calendar_toast, same mechanism calendar_scheduler
        # uses for reminders) rather than icon.notify() -- pystray's tray balloon
        # is easy to miss/suppress and doesn't land in Action Center, which is
        # why natural session ends were going unnoticed.
        calendar_toast.show_toast("Focus session complete", tray.format_end_summary(summary))

    def on_phase_change(info):
        # Fired for every pomodoro focus<->break flip that isn't the final
        # one (that goes through on_session_end instead, same toast as any
        # other session end) -- see window_tracker.run_polling_loop's
        # on_phase_change docstring.
        logger.info("pomodoro phase change -> %s (cycle %s of %s); showing its toast",
                    info["phase"], info["cycle"], info["totalCycles"])
        if info["phase"] == "break":
            calendar_toast.show_toast(
                "Break time", f"Cycle {info['cycle']} of {info['totalCycles']} done — take a break."
            )
        else:
            calendar_toast.show_toast(
                "Back to focus", f"Starting cycle {info['cycle']} of {info['totalCycles']}."
            )

    def on_pause_label(info):
        # A "decide later" pause just ended: ask what it was, on the GUI thread.
        def ask():
            from qt_ui import pause_label
            pause_label.ask(info["timestamp"], info["seconds"])
        qt_gui_thread.run_on_gui_thread(ask)

    polling_thread = threading.Thread(
        target=window_tracker.run_polling_loop,
        args=(stop_event, on_session_end),
        kwargs={"tray_icon": icon, "on_phase_change": on_phase_change, "on_pause_label": on_pause_label},
        daemon=True,
    )
    polling_thread.start()

    import discord_presence
    import score_store
    discord_presence.start(stop_event)
    score_store.start(stop_event)

    # Instant re-minimize on click (hard lock only) -- a WinEvent hook, not
    # part of window_tracker's poll loop, so it needs a real Windows message
    # pump on its own thread; see enforcer.run_instant_reminimize_watcher.
    # macOS has no message-pump equivalent of this mechanism -- its hard-lock
    # path (mac_os/enforcer_mac.py) relies on the Accessibility API's own
    # per-window minimize instead, so this thread is Windows-only.
    if sys.platform != "darwin":
        reminimize_thread = threading.Thread(
            target=enforcer.run_instant_reminimize_watcher,
            args=(stop_event,),
            daemon=True,
        )
        reminimize_thread.start()

    # pystray runs detached on its own background thread instead of
    # blocking this one — confirmed via a throwaway spike that pystray's
    # win32 backend doesn't require the main thread the way some other
    # backends (e.g. macOS's Cocoa) would. This frees the main thread for
    # Qt's event loop below.
    icon.run_detached()

    if sys.platform == "darwin":
        # Live clock + hover card on the menu-bar dot (mac-os/mac_os/menubar_timer_mac.py).
        try:
            _mac_os_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "mac-os")
            if _mac_os_dir not in sys.path:
                sys.path.insert(0, _mac_os_dir)
            from mac_os import menubar_timer_mac
            _menubar_timer = menubar_timer_mac.install(icon)
        except Exception:
            logger.exception("menubar timer unavailable")

    exit_code = app.exec()  # main thread blocks here instead of icon.run()

    stop_event.set()
    if (auto_updater.restart_was_requested() or dev_watcher.restart_was_requested()
            or code_watcher.restart_was_requested() or update_center.relaunch_was_requested()):
        # Re-exec in place (same PID, fresh interpreter) rather than
        # spawning a new process and exiting this one -- avoids a race
        # where the child tries to rebind api_server's port before this
        # process has actually released it. The lock file already holds
        # this PID, so singleinstance needs no update across the re-exec.
        #
        # A short pause first: app.exec() returning means Qt's loop is
        # done and the tray icon's Quit callback has run, but the daemon
        # threads (Flask's listener socket in particular) are still
        # unwinding on the OS's own schedule -- this gives the port a
        # moment to actually free up before the fresh interpreter tries to
        # rebind it.
        time.sleep(0.3)
        if sys.platform == "win32":
            # os.execv on Windows does not replace the process: it starts a child and the
            # parent exits at once, and the child could lose the race for the lock or the
            # port, leaving nothing running ("Carmen quit by itself after an update").
            # Release the lock, start the new copy detached, and only then leave.
            import subprocess
            singleinstance.release()
            subprocess.Popen(
                [sys.executable] + sys.argv, close_fds=True, cwd=os.getcwd(),
                creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP,
            )
            os._exit(0)
        os.execv(sys.executable, [sys.executable] + sys.argv)
    singleinstance.release()
    os._exit(exit_code)


if __name__ == "__main__":
    main()
