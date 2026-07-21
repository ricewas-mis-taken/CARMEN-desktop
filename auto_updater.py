"""Keeps the installed app in sync with the git remote without anyone
having to remember to `git pull` and relaunch by hand.

Carmen Focus is launched via autostart.py's registry entry on every login,
not run from a terminal someone is watching -- so "pull the latest code"
has to happen on its own. Once a minute, this checks whether origin/<branch>
has moved past the local HEAD; if the working tree is clean and the update
is a plain fast-forward, it pulls and triggers a full process restart (via
os.execv in main.py) so the freshly-pulled code actually takes effect.

Skips quietly (retrying next minute) on a dirty working tree, a diverged
history that isn't a fast-forward, or any git/network failure -- this must
never crash the polling/enforcement threads it runs alongside.
"""
import os
import subprocess
import threading

from calendar_log import logger

CHECK_INTERVAL_SECONDS = 60
REPO_ROOT = os.path.dirname(os.path.abspath(__file__))

_restart_requested = threading.Event()


def restart_was_requested():
    return _restart_requested.is_set()


_CREATE_NO_WINDOW = subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0


def _run_git(*args):
    return subprocess.run(
        ["git", *args],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=30,
        creationflags=_CREATE_NO_WINDOW,
    )


def _current_branch():
    result = _run_git("rev-parse", "--abbrev-ref", "HEAD")
    branch = result.stdout.strip()
    if result.returncode != 0 or not branch or branch == "HEAD":
        # Detached HEAD (e.g. checked out to a specific commit/tag) has no
        # upstream branch to fast-forward onto.
        return None
    return branch


def _check_and_pull():
    branch = _current_branch()
    if branch is None:
        return False

    fetch = _run_git("fetch", "origin", branch)
    if fetch.returncode != 0:
        logger.warning("auto_updater: git fetch failed: %s", fetch.stderr.strip())
        return False

    local = _run_git("rev-parse", "HEAD")
    remote = _run_git("rev-parse", f"origin/{branch}")
    if local.returncode != 0 or remote.returncode != 0 or local.stdout.strip() == remote.stdout.strip():
        return False  # already up to date (or couldn't tell -- treat the same, safe)

    status = _run_git("status", "--porcelain")
    if status.stdout.strip():
        logger.info("auto_updater: update available on origin/%s but working tree is dirty -- skipping", branch)
        return False

    pull = _run_git("pull", "--ff-only", "origin", branch)
    if pull.returncode != 0:
        logger.warning("auto_updater: git pull --ff-only failed: %s", pull.stderr.strip())
        return False

    logger.info("auto_updater: pulled update on %s, restarting", branch)
    return True


def _loop(stop_event, on_update_ready):
    while not stop_event.wait(CHECK_INTERVAL_SECONDS):
        try:
            if _check_and_pull():
                _restart_requested.set()
                on_update_ready()
                return
        except Exception:
            logger.exception("auto_updater check failed")


def start(stop_event, on_update_ready):
    """on_update_ready() is called, from this background thread, the moment
    an update has actually been pulled -- main.py wires it to the same
    graceful-quit path as the tray's Quit button, then re-execs the process
    once the Qt event loop exits (see restart_was_requested())."""
    thread = threading.Thread(target=_loop, args=(stop_event, on_update_ready), daemon=True)
    thread.start()
    return thread
