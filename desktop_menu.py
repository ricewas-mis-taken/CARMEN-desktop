"""Adds "QuickNote" to the Windows desktop's right-click menu.

A per-user registry entry under HKCU\\Software\\Classes\\DesktopBackground\\Shell
(no admin rights needed). On Windows 11 it shows under "Show more options". It
runs quicknote_launcher.py, which asks the running app to open the note.
Idempotent: only writes if the entry is missing or points somewhere stale (the
repo was moved), so it is safe to call on every start.
"""
import os
import sys

from calendar_log import logger

MENU_KEY = r"Software\Classes\DesktopBackground\Shell\CarmenQuickNote"
MENU_TEXT = "QuickNote (Carmen Focus)"


def _pythonw():
    pythonw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
    return pythonw if os.path.exists(pythonw) else sys.executable


def desired_entry():
    """(label, icon, command) the menu entry should have."""
    launcher = os.path.join(os.path.dirname(os.path.abspath(__file__)), "quicknote_launcher.py")
    return MENU_TEXT, _pythonw(), f'"{_pythonw()}" "{launcher}"'


def ensure_registered(winreg=None):
    """Creates or repairs the entry. Returns True if it wrote anything."""
    if winreg is None:
        if sys.platform != "win32":
            return False
        import winreg
    try:
        label, icon, command = desired_entry()
        if _read(winreg, MENU_KEY, "") == label and _read(winreg, MENU_KEY + r"\command", "") == command:
            return False
        for key_path, name, value in (
            (MENU_KEY, "", label),
            (MENU_KEY, "Icon", icon),
            (MENU_KEY + r"\command", "", command),
        ):
            with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_SET_VALUE) as key:
                winreg.SetValueEx(key, name, 0, winreg.REG_SZ, value)
        return True
    except Exception:
        logger.exception("desktop_menu.ensure_registered failed")
        return False


def remove(winreg=None):
    """Takes the entry out again."""
    if winreg is None:
        if sys.platform != "win32":
            return
        import winreg
    for key_path in (MENU_KEY + r"\command", MENU_KEY):
        try:
            winreg.DeleteKey(winreg.HKEY_CURRENT_USER, key_path)
        except OSError:
            pass


def _read(winreg, key_path, name):
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_READ) as key:
            return winreg.QueryValueEx(key, name)[0]
    except OSError:
        return None
