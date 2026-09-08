"""Registers Carmen Focus to launch automatically on Windows sign-in, so it
doesn't need to be started by hand with `python main.py` every time.

Uses the per-user Run registry key (HKCU\\...\\CurrentVersion\\Run) rather
than a Startup-folder shortcut -- no COM/pywin32 shell-link dance needed,
just winreg (stdlib), and HKCU means no admin elevation is required.
"""
import sys

if sys.platform == "darwin":
    import os as _os
    _mac_os_dir = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "mac-os")
    if _mac_os_dir not in sys.path:
        sys.path.insert(0, _mac_os_dir)
    from mac_os import autostart_mac as _mac

    ensure_autostart_registered = _mac.ensure_autostart_registered
else:
    import os
    import winreg

    from calendar_log import logger

    RUN_KEY_PATH = r"Software\Microsoft\Windows\CurrentVersion\Run"
    VALUE_NAME = "CarmenFocus"


    def _pythonw_executable():
        # sys.executable is python.exe when running under `python main.py`,
        # which would pop a console window on every login -- pythonw.exe (same
        # directory) runs silently, matching how a tray-resident app should
        # start.
        pythonw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
        return pythonw if os.path.exists(pythonw) else sys.executable


    def ensure_autostart_registered():
        """Idempotent: only touches the registry if the value is missing or
        points somewhere stale (e.g. the repo was moved). Safe to call on every
        startup from main.py."""
        try:
            main_py = os.path.join(os.path.dirname(os.path.abspath(__file__)), "main.py")
            command = f'"{_pythonw_executable()}" "{main_py}"'

            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY_PATH, 0, winreg.KEY_READ) as key:
                try:
                    existing, _ = winreg.QueryValueEx(key, VALUE_NAME)
                except FileNotFoundError:
                    existing = None
            if existing == command:
                return

            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY_PATH, 0, winreg.KEY_SET_VALUE) as key:
                winreg.SetValueEx(key, VALUE_NAME, 0, winreg.REG_SZ, command)
        except Exception:
            logger.exception("ensure_autostart_registered failed")
