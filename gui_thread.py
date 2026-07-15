"""Single shared Tkinter GUI thread.

Tkinter is not thread-safe. The previous design created a brand new Tk()
root in its own OS thread for every popup — enforcer.py's lock overlays and
picker_gui.py's picker/timer dialogs each did this independently. Having two
Tk() roots alive in two different threads at the same time (e.g. a soft-lock
warning firing while the whitelist picker is open) triggers a fatal Tcl
error — "Tcl_AsyncDelete: async handler deleted by the wrong thread" — that
crashes the entire process, confirmed by reproduction, not theoretical.

This module runs exactly ONE persistent, hidden Tk() root on one dedicated
thread for the whole app's lifetime. Every popup (lock overlays, the app
picker, the timer dialog) is a Toplevel() built on that same root, and every
window-creation request is marshaled onto that thread via a queue so
Tkinter is only ever touched from the thread that owns it.
"""
import queue
import threading
import tkinter as tk

_task_queue = queue.Queue()
_root = None


def start():
    """Starts the GUI thread. Call once, from main.py."""
    threading.Thread(target=_run, daemon=True).start()


def run_on_gui_thread(build_fn):
    """Schedules build_fn(root) to run on the shared GUI thread. build_fn
    should create and manage its own Toplevel(root) window — never a new
    Tk()."""
    _task_queue.put(build_fn)


def _run():
    global _root
    _root = tk.Tk()
    _root.withdraw()  # hidden — every real window is a Toplevel on this root

    def poll_queue():
        while True:
            try:
                build_fn = _task_queue.get_nowait()
            except queue.Empty:
                break
            try:
                build_fn(_root)
            except Exception:
                pass
        _root.after(50, poll_queue)

    poll_queue()
    _root.mainloop()
