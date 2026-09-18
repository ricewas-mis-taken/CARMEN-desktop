"""Single-instance guard: only one Carmen Focus process should ever hold the
tray icon / Flask port / window-tracker polling loop at once. Launching a
second copy (a stale process from a crashed shutdown, or a user double
clicking the shortcut again) previously left two tray icons and a port
conflict -- this checks a PID lock file on startup and shuts down whatever
is still holding it before continuing.

Graceful first, hard kill only as a last resort: on Windows,
psutil.Process.terminate() is TerminateProcess() -- an immediate hard kill
with no chance for the target to run its own cleanup (no SIGTERM-equivalent
exists there). So a stale instance is asked to shut itself down cleanly over
its own loopback API first (POST /internal/quit -- see
api_server.register_quit_callback(), wired to the same on_quit path as the
tray's Quit button, which removes the tray icon and lets the Qt/Flask
threads unwind normally). Only if it doesn't exit within a few seconds does
this fall back to a hard TerminateProcess, which *can* leave a ghost tray
icon behind -- logged loudly when that happens, since it's the exception,
not the norm.
"""
import json
import os
import sys
import time
import urllib.error
import urllib.request

import psutil

import api_server
from calendar_log import logger

if sys.platform == "darwin":
    # No LOCALAPPDATA on macOS -- ~/Library/Application Support/CARMEN is
    # the idiomatic per-user data location there, rather than falling back
    # to a bare ~/CARMEN (which worked fine functionally, since this whole
    # module is otherwise platform-agnostic, but isn't where a macOS app is
    # expected to keep this kind of file).
    LOCK_DIR = os.path.join(os.path.expanduser("~"), "Library", "Application Support", "CARMEN")
else:
    LOCK_DIR = os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "CARMEN")
LOCK_PATH = os.path.join(LOCK_DIR, "carmen.lock")

_QUIT_URL = f"http://127.0.0.1:{api_server.API_PORT}/internal/quit"

_GRACEFUL_HTTP_TIMEOUT_SECONDS = 2
_GRACEFUL_EXIT_TIMEOUT_SECONDS = 5
_HARD_TERMINATE_TIMEOUT_SECONDS = 5


def _read_pid():
    try:
        with open(LOCK_PATH, "r", encoding="utf-8") as f:
            return int(f.read().strip())
    except (OSError, ValueError):
        return None


def _is_running_python_process(pid):
    if pid is None or pid == os.getpid():
        return False
    try:
        proc = psutil.Process(pid)
        return "python" in proc.name().lower()
    except psutil.NoSuchProcess:
        return False


def _request_graceful_quit():
    """Best-effort POST to the stale instance's own /internal/quit. Returns
    True only if the request was actually delivered -- a connection refused
    (server not up yet, or already gone) means there's nothing to wait on."""
    try:
        req = urllib.request.Request(_QUIT_URL, data=b"{}", method="POST",
                                      headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=_GRACEFUL_HTTP_TIMEOUT_SECONDS) as resp:
            json.loads(resp.read())
        return True
    except (urllib.error.URLError, OSError, ValueError):
        return False


def _wait_for_exit(pid, timeout_seconds):
    try:
        psutil.Process(pid).wait(timeout=timeout_seconds)
        return True
    except psutil.NoSuchProcess:
        return True
    except psutil.TimeoutExpired:
        return False


def acquire():
    """Call once at startup, before anything else stands up a tray icon or
    binds the Flask port. If a previous instance is still alive, shuts it
    down (gracefully if at all possible) before writing our own PID."""
    os.makedirs(LOCK_DIR, exist_ok=True)

    stale_pid = _read_pid()
    if _is_running_python_process(stale_pid):
        logger.warning("singleinstance: found running stale instance pid=%s", stale_pid)

        if _request_graceful_quit() and _wait_for_exit(stale_pid, _GRACEFUL_EXIT_TIMEOUT_SECONDS):
            logger.warning("singleinstance: stale instance pid=%s shut down cleanly", stale_pid)
        else:
            # Either it never answered /internal/quit (e.g. an older build
            # without the endpoint, or a hung process) or it answered but
            # didn't actually exit in time -- fall back to a hard kill so
            # startup isn't blocked indefinitely. This is the one path that
            # can leave a ghost tray icon/notify-icon behind.
            logger.warning(
                "singleinstance: stale instance pid=%s did not shut down gracefully -- "
                "hard-terminating (may leave a ghost tray icon)", stale_pid,
            )
            try:
                proc = psutil.Process(stale_pid)
                proc.terminate()
                proc.wait(timeout=_HARD_TERMINATE_TIMEOUT_SECONDS)
            except psutil.NoSuchProcess:
                pass
            except psutil.TimeoutExpired:
                logger.warning("singleinstance: stale instance pid=%s still alive after hard terminate", stale_pid)

        time.sleep(0.3)  # let the OS actually release the port/tray resources

    with open(LOCK_PATH, "w", encoding="utf-8") as f:
        f.write(str(os.getpid()))


def release():
    """Call on graceful exit. Only removes the lock if it still points at us
    -- a newer instance may have already taken over and overwritten it."""
    if _read_pid() == os.getpid():
        try:
            os.remove(LOCK_PATH)
        except OSError:
            pass
