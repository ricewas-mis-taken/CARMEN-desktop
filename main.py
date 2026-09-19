"""Entry point — starts the API server, window-polling loop, and pystray's
detached tray thread as background threads, then runs Qt's event loop on
the main thread (Qt requirement: only the thread that constructs
QApplication may create/touch widgets — see qt_gui_thread.py)."""
import os
import subprocess
import sys
import threading
import time

from PySide6.QtWidgets import QApplication, QMessageBox

import api_server
import auto_updater
import autostart
import calendar_scheduler
import calendar_toast
import config
import dev_watcher
import enforcer
import qt_gui_thread
import singleinstance
import sync_scheduler
import tray
import window_tracker
from api_server import run_server

stop_event = threading.Event()
DEV_MODE = "--dev" in sys.argv[1:]

STYLESHEET_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "styles.qss")


def _load_stylesheet(app):
    try:
        with open(STYLESHEET_PATH, "r", encoding="utf-8") as f:
            app.setStyleSheet(f.read())
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

    # QApplication must be constructed here, on what becomes "the Qt main
    # thread" (this thread), before qt_gui_thread.start() and before any
    # background thread exists that could reach into Qt (the polling
    # thread below is the one that matters most — it can trigger
    # enforcer.py's lock overlays at any time once it starts).
    app = QApplication(sys.argv)
    # Tray-resident app: closing the last visible window (the calendar
    # window, a lock overlay auto-closing, etc.) must never exit the
    # process on its own — only "Quit" from the tray menu should.
    app.setQuitOnLastWindowClosed(False)
    _load_stylesheet(app)
    _check_accessibility_trust()

    qt_gui_thread.start()

    if DEV_MODE:
        # Opens the main window automatically on launch/dev-restart, purely
        # a dev-loop convenience so window-geometry changes can be reverified
        # without a manual tray-icon click each time -- never runs outside
        # --dev.
        import qt_ui.main_window as main_window
        qt_gui_thread.run_on_gui_thread(main_window.open_main_window)

    api_thread = threading.Thread(target=run_server, daemon=True)
    api_thread.start()

    calendar_scheduler.start(stop_event)
    sync_scheduler.start(stop_event)

    def on_quit():
        stop_event.set()
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
    auto_updater.start(stop_event, on_quit)

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
        if info["phase"] == "break":
            calendar_toast.show_toast(
                "Break time", f"Cycle {info['cycle']} of {info['totalCycles']} done — take a break."
            )
        else:
            calendar_toast.show_toast(
                "Back to focus", f"Starting cycle {info['cycle']} of {info['totalCycles']}."
            )

    polling_thread = threading.Thread(
        target=window_tracker.run_polling_loop,
        args=(stop_event, on_session_end),
        kwargs={"tray_icon": icon, "on_phase_change": on_phase_change},
        daemon=True,
    )
    polling_thread.start()

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

    exit_code = app.exec()  # main thread blocks here instead of icon.run()

    stop_event.set()
    if auto_updater.restart_was_requested() or dev_watcher.restart_was_requested():
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
        os.execv(sys.executable, [sys.executable] + sys.argv)
    singleinstance.release()
    os._exit(exit_code)


if __name__ == "__main__":
    main()
