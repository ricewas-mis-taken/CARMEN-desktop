"""Keeps the Windows taskbar quiet while a focus session runs.

Two per-session choices (session_manager's hideTaskbarBadges / stopTaskbarFlashing) map
onto the two toggles in Settings -> Personalization -> Taskbar:
  - "Show badges on taskbar apps"
  - "Show flashing on taskbar apps"

Windows 11 no longer reads these from the registry (the old TaskbarBadges /
TaskbarFlashing values are ignored), so taskbar_quiet.ps1 flips the real toggles in the
Settings app, out of sight, the way a person would. While a session that asked for it
is running (not paused, not on a pomodoro break) the toggle is switched off; whenever
that stops being true the value it had before is put back. The originals are written to
disk first, so a crash or a restart mid-session can't leave the taskbar permanently
quiet: restore_all() runs at startup and at quit. Windows only; a no-op elsewhere.
"""
import json
import os
import subprocess
import sys
import threading
import time

from calendar_log import logger

_HERE = os.path.dirname(os.path.abspath(__file__))
SAVED_PATH = os.path.join(_HERE, "private", "taskbar_quiet_settings.json")   # originals of what is held off
# Files of the registry-based version. Their "originals" can be a leftover off, so they are
# never trusted: anything it was holding is put back to Windows' default (on).
LEGACY_PATHS = [os.path.join(_HERE, "private", "taskbar_quiet_saved.json"),
                os.path.join(_HERE, "private", "taskbar_quiet_user.json")]
PENDING_PATH = os.path.join(_HERE, "private", "taskbar_quiet_pending.json")  # written by the script before it changes anything
SCRIPT_PATH = os.path.join(_HERE, "taskbar_quiet.ps1")

# PARKED (2026-10-14): flipping the Settings toggles never got clean enough (the Settings
# window shows for a moment, an auto-hidden taskbar can pop up). Carmen leaves the
# user's notification settings alone. Flip to True to pick the work back up.
ENABLED = False

SETTINGS = ("badges", "flashing")
RETRY_SECONDS = 30
RUN_TIMEOUT_SECONDS = 40

_lock = threading.Lock()
_busy = False
_next_attempt = 0.0


def _run_script(want, save_path=None):
    """Runs the Settings script. `want` is {"badges": bool, ...} (True = on). Returns the
    values found before changing anything, e.g. {"badges": True}, or None on failure."""
    if sys.platform != "win32":
        return None
    arg = ",".join(f"{name}={'on' if value else 'off'}" for name, value in want.items())
    command = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", SCRIPT_PATH, "-Want", arg]
    if save_path:
        command += ["-Save", save_path]
    try:
        done = subprocess.run(
            command, capture_output=True, text=True, timeout=RUN_TIMEOUT_SECONDS,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if done.returncode != 0:
            logger.warning("taskbar_quiet: the Settings script exited with %s", done.returncode)
            return None
        lines = [line for line in done.stdout.strip().splitlines() if line.strip()]
        data = json.loads(lines[-1]) if lines else {}
        return data if isinstance(data, dict) else None
    except Exception:
        logger.exception("taskbar_quiet: could not run the Settings script")
        return None


def _read_json(path):
    try:
        with open(path, "r", encoding="utf-8-sig") as f:
            data = json.load(f)
        return {k: bool(v) for k, v in data.items() if k in SETTINGS} if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _load_saved():
    return _read_json(SAVED_PATH)


def _save_saved(saved):
    if not saved:
        try:
            os.remove(SAVED_PATH)
        except OSError:
            pass
        return
    os.makedirs(os.path.dirname(SAVED_PATH), exist_ok=True)
    with open(SAVED_PATH, "w", encoding="utf-8") as f:
        json.dump(saved, f)


def _fold_in_pending(saved):
    """A run that died after reading the originals left them in the pending file."""
    for name, value in _read_json(PENDING_PATH).items():
        saved.setdefault(name, value)
    try:
        os.remove(PENDING_PATH)
    except OSError:
        pass
    return saved


def wanted(status):
    """Which settings this /status-shaped dict wants switched off right now."""
    quiet = bool(status.get("isActive")) and not status.get("isPaused") and not status.get("isBreak")
    return {
        "badges": quiet and bool(status.get("hideTaskbarBadges")),
        "flashing": quiet and bool(status.get("stopTaskbarFlashing")),
    }


def _spawn(fn):
    threading.Thread(target=fn, name="taskbar-quiet", daemon=True).start()


def _apply(to_off, to_restore):
    """One run of the script: switch `to_off` off and put `to_restore` back."""
    global _busy, _next_attempt
    try:
        saved = _fold_in_pending(_load_saved())
        want = {name: False for name in to_off}
        want.update({name: saved[name] for name in to_restore if name in saved})
        if not want:
            return
        before = _run_script(want, PENDING_PATH if to_off else None)
        if before is None:
            _next_attempt = time.monotonic() + RETRY_SECONDS
            return
        saved = _fold_in_pending(_load_saved())
        for name in to_off:
            saved[name] = before.get(name, saved.get(name, True))
        for name in to_restore:
            saved.pop(name, None)
        _save_saved(saved)
    except Exception:
        logger.exception("taskbar_quiet: could not update the taskbar settings")
        _next_attempt = time.monotonic() + RETRY_SECONDS
    finally:
        with _lock:
            _busy = False


def reconcile(status, wait=False):
    """Brings the taskbar toggles in line with `status`. Cheap when nothing changed (one
    small file read), so the polling loop can call it every tick; the slow part (opening
    Settings for a couple of seconds) runs on its own thread. wait=True runs it here."""
    global _busy
    if sys.platform != "win32" or not ENABLED:
        return
    want = wanted(status)
    saved = _load_saved()
    if os.path.exists(PENDING_PATH) and not _busy:
        saved = _fold_in_pending(saved)
        _save_saved(saved)
    to_off = [name for name in SETTINGS if want[name] and name not in saved]
    to_restore = [name for name in saved if not want.get(name)]
    if not to_off and not to_restore:
        return
    with _lock:
        if _busy or (not wait and time.monotonic() < _next_attempt):
            return
        _busy = True
    if wait:
        _apply(to_off, to_restore)
    else:
        _spawn(lambda: _apply(to_off, to_restore))


def _migrate_legacy():
    held = _read_json(LEGACY_PATHS[0])
    for path in LEGACY_PATHS:
        try:
            os.remove(path)
        except OSError:
            pass
    if held:
        saved = _load_saved()
        for name in held:
            saved.setdefault(name, True)
        _save_saved(saved)


def restore_all():
    """Puts back everything this module switched off (startup and quit)."""
    _migrate_legacy()
    reconcile({"isActive": False}, wait=True)
