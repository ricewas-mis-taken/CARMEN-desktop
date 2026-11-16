"""One place that knows whether new code is waiting, and applies it to the running app without a restart.

Two things can be waiting: commits pushed to the repo (pulled when the button is pressed) and edits already
on disk (from a pull done by hand, or a change made here). Both show as one "Update" button in the window.
Pressing it pulls if needed, then hot_update.swap() puts the new code into the live app.
This module only holds state and runs the git side; the window shows the button."""
import threading

import auto_updater
import code_watcher
from calendar_log import logger

CHECK_SECONDS = 60

_state = {"remote": False, "local": False, "busy": False, "message": ""}
_lock = threading.Lock()
_baseline = {}
watcher_restart = None
relaunch_hook = None                       # main.py sets this to the app's normal shutdown
_relaunch = threading.Event()
_needs_relaunch = threading.Event()


def needs_relaunch():
    """Files that only a fresh start can take in were just updated: the window offers a Relaunch button."""
    return _needs_relaunch.is_set()


def set_needs_relaunch(value=True):
    (_needs_relaunch.set if value else _needs_relaunch.clear)()


def relaunch_was_requested():
    return _relaunch.is_set()


def request_relaunch():
    """Closes Carmen and starts it again at once, with the window back on the same page. A running session is
    kept (it is saved as it runs and comes back with the new copy)."""
    import os
    try:
        import config
        from qt_ui import main_window
        remembered = main_window.remembered()
        config.update_config(lambda cfg: cfg.update(restoreWindow=remembered))
    except Exception:
        logger.exception("update_center: could not remember the window")
    os.environ["CARMEN_RESTARTED"] = "1"
    _relaunch.set()
    if relaunch_hook is not None:
        relaunch_hook()


def available():
    with _lock:
        return _state["remote"] or _state["local"]


def snapshot():
    """Copy of the state for the window: {"available", "busy", "message"}."""
    with _lock:
        return {"available": _state["remote"] or _state["local"], "busy": _state["busy"], "message": _state["message"]}


def set_local(value=True):
    with _lock:
        _state["local"] = value


def set_message(text):
    with _lock:
        _state["message"] = text


def start_baseline():
    global _baseline
    _baseline = code_watcher.file_stamps()


def changed_files():
    """Tracked source files that differ from what this process loaded."""
    now = code_watcher.file_stamps()
    return sorted(n for n in set(now) | set(_baseline) if now.get(n) != _baseline.get(n))


def watch(stop_event):
    """Background: asks the remote every minute whether an update is waiting."""
    def loop():
        while not stop_event.wait(CHECK_SECONDS):
            try:
                found = auto_updater.update_waiting()
                with _lock:
                    _state["remote"] = bool(found)
            except Exception:
                logger.exception("update_center check failed")
    threading.Thread(target=loop, daemon=True, name="update-center").start()


def apply_async(run_on_gui, done):
    """Pulls (when something is waiting on the remote), then swaps the new code in on the GUI thread.
    `done(result)` is called on the GUI thread with hot_update's result dict or {"error": text}."""
    with _lock:
        if _state["busy"]:
            return
        _state["busy"] = True

    def work():
        try:
            if _state["remote"]:
                if not auto_updater.pull_now():
                    raise RuntimeError("the update could not be pulled")
            files = changed_files()

            def swap():
                try:
                    import hot_update
                    result = hot_update.swap(files)
                except Exception as exc:              # a failed swap leaves the running copy as it was
                    logger.exception("hot update failed")
                    result = {"error": str(exc)}
                _finish(result)
                done(result)
            run_on_gui(swap)
        except Exception as exc:
            logger.exception("update failed")
            _finish({"error": str(exc)})
            run_on_gui(lambda: done({"error": str(exc)}))

    threading.Thread(target=work, daemon=True, name="update-apply").start()


def _finish(result):
    with _lock:
        _state["busy"] = False
        if "error" not in result:
            _state["remote"] = False
            _state["local"] = False
    start_baseline()
    if "error" not in result and watcher_restart is not None:
        watcher_restart()                     # the watcher stops after announcing; begin again from the new state
