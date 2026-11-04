"""Applies new code to the running app by itself, so nobody has to quit and relaunch it.

Whatever changes the source files while Carmen is open (a pull, a commit, an edit) is noticed here. Once the
files have stopped changing, the new code is checked in a throwaway process (can it be imported at all?),
and only then, at a quiet moment, the app restarts into it, reopening on the page that was showing.

A bad change never replaces a working app: if the check fails the running copy carries on and the problem is
logged. A quiet moment is when no session or review is running and either the window is closed or nobody
has touched the keyboard or mouse for a few seconds, so it never cuts into work."""
import hashlib
import os
import subprocess
import sys
import tempfile
import threading
import time

from calendar_log import logger

REPO_ROOT = os.path.dirname(os.path.abspath(__file__))
CHECK_SECONDS = 15
SETTLE_SECONDS = 20          # the files must have stopped changing this long (an edit in progress is not ready)
IDLE_SECONDS = 20            # ...and nobody using the app this long, unless its window is closed
SMOKE_MODULES = ("session_manager", "review_store", "score_store", "tasks_store", "api_server",
                 "qt_ui.main_window", "qt_ui.tour")
_CREATE_NO_WINDOW = subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0

_restart_requested = threading.Event()
_last_problem = None
_generation = [0]


def restart_was_requested():
    return _restart_requested.is_set()


def _tracked_sources():
    result = subprocess.run(["git", "ls-files", "--", "*.py", "*.qss"], cwd=REPO_ROOT, capture_output=True,
                            text=True, timeout=30, creationflags=_CREATE_NO_WINDOW)
    return [p for p in result.stdout.splitlines() if p] if result.returncode == 0 else []


def file_stamps():
    """{tracked file: (size, modified time)}"""
    stamps = {}
    for name in _tracked_sources():
        try:
            stat = os.stat(os.path.join(REPO_ROOT, name))
            stamps[name] = (stat.st_size, stat.st_mtime_ns)
        except OSError:
            stamps[name] = None
    return stamps


def clear_restart_request():
    """The watcher now only announces new code; nothing should restart when the app is later quit."""
    _restart_requested.clear()


def fingerprint():
    """One value for the state of every tracked source file (name, size, modified time)."""
    digest = hashlib.sha1()
    for name in sorted(_tracked_sources()):
        try:
            stat = os.stat(os.path.join(REPO_ROOT, name))
        except OSError:
            digest.update(f"{name}:gone".encode())
            continue
        digest.update(f"{name}:{stat.st_size}:{stat.st_mtime_ns}".encode())
    return digest.hexdigest()


def new_code_imports():
    """True when the current files import cleanly in a separate process that uses its own empty data folder."""
    code = ("import importlib, sys\n"
            + "".join(f"importlib.import_module({m!r})\n" for m in SMOKE_MODULES))
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen", CARMEN_PRIVATE_DIR=tempfile.mkdtemp())
    try:
        result = subprocess.run([sys.executable, "-c", code], cwd=REPO_ROOT, env=env, capture_output=True, text=True,
                                timeout=90, creationflags=_CREATE_NO_WINDOW)
    except (OSError, subprocess.TimeoutExpired) as exc:
        _log_once(f"the new code could not be checked: {exc}")
        return False
    if result.returncode != 0:
        _log_once("the new code does not import, staying on the running copy: " + result.stderr.strip()[-400:])
        return False
    return True


def _log_once(message):
    global _last_problem
    if message != _last_problem:
        _last_problem = message
        logger.warning("code_watcher: %s", message)


def safe_to_restart(busy, window_open, idle_seconds):
    """A quiet moment. `busy()` says a session, review or first attempt is running; `window_open()` whether
    the main window is showing; `idle_seconds()` seconds since the last key or mouse move (None if unknown)."""
    if busy():
        return False
    if not window_open():
        return True
    idle = idle_seconds()
    return idle is not None and idle >= IDLE_SECONDS


def _loop(stop_event, on_ready, busy, window_open, idle_seconds, generation=None):
    baseline = fingerprint()
    seen, changed_at, checked, verified = baseline, None, None, None
    while not stop_event.wait(CHECK_SECONDS):
        if generation is not None and generation != _generation[0]:
            return                                      # a newer watcher took over
        try:
            current = fingerprint()
            if current == baseline:
                seen, changed_at, checked, verified = current, None, None, None
                continue
            if current != seen:                         # still being edited: start the wait again
                seen, changed_at, checked, verified = current, time.monotonic(), None, None
                continue
            if time.monotonic() - (changed_at or time.monotonic()) < SETTLE_SECONDS:
                continue
            if checked != current:
                checked = current
                if not new_code_imports():
                    continue                            # looked at once; looked at again only if it changes
                verified = current
                logger.info("code_watcher: new code checked, restarting at the next quiet moment")
            if verified == current and safe_to_restart(busy, window_open, idle_seconds):
                _restart_requested.set()
                on_ready()
                return
        except Exception:
            logger.exception("code_watcher check failed")


def start(stop_event, on_ready, busy, window_open, idle_seconds):
    _generation[0] += 1
    thread = threading.Thread(target=_loop, args=(stop_event, on_ready, busy, window_open, idle_seconds, _generation[0]),
                              daemon=True, name="code-watcher")
    thread.start()
    return thread
