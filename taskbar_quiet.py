"""Keeps the Windows taskbar quiet while a focus session runs.

Two per-session choices (session_manager's hideTaskbarBadges /
stopTaskbarFlashing) map onto two Windows taskbar settings:
  - "Show badges (unread messages counter) on taskbar apps"  -> TaskbarBadges
  - "Show flashing on taskbar apps"                          -> TaskbarFlashing

While a session that asked for it is running (not paused, not on a pomodoro
break) the setting is switched off; whenever that stops being true the value
the user had before is put back. The originals are written to disk first, so
a crash or a restart mid-session can't leave the taskbar permanently quiet:
restore_all() runs at startup and at quit. Windows only; a no-op elsewhere.
"""
import json
import os
import sys

from calendar_log import logger

SAVED_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "private", "taskbar_quiet_saved.json")

# What the user's own setting was the last time this module was not holding it off.
USER_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "private", "taskbar_quiet_user.json")

_KEY_PATH = r"Software\Microsoft\Windows\CurrentVersion\Explorer\Advanced"
_VALUE_NAMES = {"badges": "TaskbarBadges", "flashing": "TaskbarFlashing"}
# What Windows treats as "on" when the value has never been written.
_DEFAULT_ON = 1


def _read_value(name):
    """Current DWORD value, or None if it was never set (or not Windows)."""
    if sys.platform != "win32":
        return None
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _KEY_PATH) as key:
            return int(winreg.QueryValueEx(key, name)[0])
    except OSError:
        return None


def _write_value(name, value):
    if sys.platform != "win32":
        return
    import winreg
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _KEY_PATH, 0, winreg.KEY_SET_VALUE) as key:
        winreg.SetValueEx(key, name, 0, winreg.REG_DWORD, int(value))


def _broadcast_change():
    """Tells the shell its taskbar settings changed so it re-reads them now."""
    if sys.platform != "win32":
        return
    import ctypes
    ctypes.windll.user32.SendMessageTimeoutW(0xFFFF, 0x001A, 0, "TraySettings", 0x0002, 1000, None)


def _load_saved():
    try:
        with open(SAVED_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


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


def _load_user():
    try:
        with open(USER_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _remember_user_values(saved):
    """While nothing is being held off, note what the user's settings really are, so a
    leftover "off" from a crash or kill can never be mistaken for their own choice."""
    known = _load_user()
    changed = False
    for setting, name in _VALUE_NAMES.items():
        if setting in saved:
            continue
        value = _read_value(name)
        value = _DEFAULT_ON if value is None else value
        if known.get(setting) != value:
            known[setting] = value
            changed = True
    if changed:
        os.makedirs(os.path.dirname(USER_PATH), exist_ok=True)
        with open(USER_PATH, "w", encoding="utf-8") as f:
            json.dump(known, f)


def _value_to_put_back(setting, saved_original):
    """The user's own value. An "off" we only found lying around (never seen while we
    were not holding it) is ours from an earlier run, so it goes back to on."""
    known = _load_user().get(setting)
    if known is not None:
        return known
    return _DEFAULT_ON if not saved_original else saved_original


def wanted(status):
    """Which settings this /status-shaped dict wants switched off right now."""
    quiet = bool(status.get("isActive")) and not status.get("isPaused") and not status.get("isBreak")
    return {
        "badges": quiet and bool(status.get("hideTaskbarBadges")),
        "flashing": quiet and bool(status.get("stopTaskbarFlashing")),
    }


def reconcile(status):
    """Brings the Windows settings in line with `status`. Cheap when nothing
    changed (a small file read), so the polling loop can call it every tick."""
    want = wanted(status)
    saved = _load_saved()
    changed = False
    for setting, name in _VALUE_NAMES.items():
        try:
            if want[setting] and setting not in saved:
                original = _value_to_put_back(setting, _read_value(name))
                saved[setting] = original
                _save_saved(saved)  # remembered before touching anything
                _write_value(name, 0)
                changed = True
            elif not want[setting] and setting in saved:
                _write_value(name, _value_to_put_back(setting, saved[setting]))
                del saved[setting]
                _save_saved(saved)
                changed = True
        except Exception:
            logger.exception("taskbar_quiet: could not update %s", name)
    try:
        _remember_user_values(saved)
    except Exception:
        logger.exception("taskbar_quiet: could not note the user's settings")
    if changed:
        try:
            _broadcast_change()
        except Exception:
            logger.exception("taskbar_quiet: could not notify the shell")


def restore_all():
    """Puts back everything this module switched off (startup and quit)."""
    reconcile({"isActive": False})
