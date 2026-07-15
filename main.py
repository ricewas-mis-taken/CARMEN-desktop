"""Entry point — starts the API server, window-polling loop, and the shared
Tkinter GUI thread as background threads, then runs the tray icon on the
main thread (pystray requirement)."""
import os
import threading

import config
import gui_thread
import tray
import window_tracker
from api_server import run_server

stop_event = threading.Event()


def main():
    config.load_config()

    api_thread = threading.Thread(target=run_server, daemon=True)
    api_thread.start()

    polling_thread = threading.Thread(
        target=window_tracker.run_polling_loop, args=(stop_event,), daemon=True
    )
    polling_thread.start()

    # One shared Tk() root/thread for every popup (lock overlays, the app
    # picker, the timer dialog) — see gui_thread.py for why this can't be a
    # new Tk() per popup.
    gui_thread.start()

    def on_quit():
        stop_event.set()

    tray.run_tray(on_quit)

    os._exit(0)


if __name__ == "__main__":
    main()
