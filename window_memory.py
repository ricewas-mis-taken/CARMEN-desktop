"""Remembers which windows were open, so a crash that restarts Carmen puts them back.

A normal relaunch saves this just before closing (update_center). A crash gives no such moment, so the
window state is written to private/window_state.json whenever it changes (checked every couple of seconds on
the window thread), and the restart reads it back."""
import json
import os

import paths

PATH = paths.private_path("window_state.json")
CHECK_MS = 2000
_last = {"state": None}
_timer = None


def collect():
    """{"main": {"open", "page"}, "mini": bool} for the windows as they are now."""
    from qt_ui import main_window, mini_timer
    return {"main": main_window.remembered(), "mini": bool(mini_timer.is_open())}


def save_if_changed(state=None):
    """Writes the state when it differs from the last write. Returns True when it wrote."""
    state = state if state is not None else collect()
    if state == _last["state"]:
        return False
    tmp = PATH + ".tmp"
    try:
        os.makedirs(os.path.dirname(PATH), exist_ok=True)
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(state, f)
        os.replace(tmp, PATH)
    except OSError:
        return False
    _last["state"] = state
    return True


def load():
    """The saved state, or {} when there is none or it is unreadable."""
    try:
        with open(PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def install(parent=None):
    """Call once on the Qt main thread after QApplication exists."""
    global _timer
    from PySide6.QtCore import QTimer
    _timer = QTimer(parent)
    _timer.setInterval(CHECK_MS)
    _timer.timeout.connect(save_if_changed)
    _timer.start()
