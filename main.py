"""Entry point — starts the API server, window-polling loop, and the shared
Tkinter GUI thread as background threads, then runs the tray icon on the
main thread (pystray requirement)."""
import os
import threading

import calendar_scheduler
import calendar_toast
import config
import gui_thread
import tray
import window_tracker
from api_server import run_server

stop_event = threading.Event()


def main():
    config.load_config()
    calendar_toast.set_app_id()

    api_thread = threading.Thread(target=run_server, daemon=True)
    api_thread.start()

    calendar_scheduler.start(stop_event)

    def on_quit():
        stop_event.set()

    # Built before the polling thread starts so the polling loop can notify
    # through it when a session's timer runs out naturally (see
    # window_tracker.run_polling_loop's on_session_end).
    icon = tray.build_tray_icon(on_quit)

    def on_session_end(summary):
        icon.notify(tray.format_end_summary(summary), title="Focus session complete")

    polling_thread = threading.Thread(
        target=window_tracker.run_polling_loop,
        args=(stop_event, on_session_end),
        kwargs={"tray_icon": icon},
        daemon=True,
    )
    polling_thread.start()

    # One shared Tk() root/thread for every popup (lock overlays, the app
    # picker, the timer dialog) — see gui_thread.py for why this can't be a
    # new Tk() per popup.
    gui_thread.start()

    icon.run()

    os._exit(0)


if __name__ == "__main__":
    main()
