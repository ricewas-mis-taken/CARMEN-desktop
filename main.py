"""Entry point — starts the API server, window-polling loop, and pystray's
detached tray thread as background threads, then runs Qt's event loop on
the main thread (Qt requirement: only the thread that constructs
QApplication may create/touch widgets — see qt_gui_thread.py)."""
import os
import sys
import threading

from PySide6.QtWidgets import QApplication

import calendar_scheduler
import calendar_toast
import config
import qt_gui_thread
import tray
import window_tracker
from api_server import run_server

stop_event = threading.Event()

STYLESHEET_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "styles.qss")


def _load_stylesheet(app):
    try:
        with open(STYLESHEET_PATH, "r", encoding="utf-8") as f:
            app.setStyleSheet(f.read())
    except OSError:
        pass  # missing/unreadable stylesheet must never block the app from starting


def main():
    config.load_config()
    calendar_toast.set_app_id()

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

    qt_gui_thread.start()

    api_thread = threading.Thread(target=run_server, daemon=True)
    api_thread.start()

    calendar_scheduler.start(stop_event)

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

    def on_session_end(summary):
        # A real Windows toast (calendar_toast, same mechanism calendar_scheduler
        # uses for reminders) rather than icon.notify() -- pystray's tray balloon
        # is easy to miss/suppress and doesn't land in Action Center, which is
        # why natural session ends were going unnoticed.
        calendar_toast.show_toast("Focus session complete", tray.format_end_summary(summary))

    polling_thread = threading.Thread(
        target=window_tracker.run_polling_loop,
        args=(stop_event, on_session_end),
        kwargs={"tray_icon": icon},
        daemon=True,
    )
    polling_thread.start()

    # pystray runs detached on its own background thread instead of
    # blocking this one — confirmed via a throwaway spike that pystray's
    # win32 backend doesn't require the main thread the way some other
    # backends (e.g. macOS's Cocoa) would. This frees the main thread for
    # Qt's event loop below.
    icon.run_detached()

    exit_code = app.exec()  # main thread blocks here instead of icon.run()

    stop_event.set()
    os._exit(exit_code)


if __name__ == "__main__":
    main()
