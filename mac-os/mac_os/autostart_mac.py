"""Registers Carmen Focus to launch automatically on macOS login, via a
per-user LaunchAgent .plist -- the macOS analogue of autostart.py's Windows
Run-key registration (see that file's module docstring).

Uses ~/Library/LaunchAgents rather than a system-wide LaunchDaemon, so no
elevated privileges are required, matching the Windows version's use of HKCU
(no admin elevation needed either).
"""
import os
import plistlib
import subprocess
import sys

_repo_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

from calendar_log import logger

LABEL = "com.carmenfocus.app"
PLIST_PATH = os.path.expanduser("~/Library/LaunchAgents/com.carmenfocus.app.plist")

# mac-os/mac_os/autostart_mac.py -> repo root is two directories up.
_MAIN_PY_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "main.py",
)


def _plist_contents():
    # sys.executable here is a stand-in for the eventual PyInstaller-built
    # .app's actual binary (see PORT_SPEC.md's Subsystem 5/Packaging
    # sections) -- once packaging exists this should point at that bundled
    # binary instead, the same way autostart.py's _pythonw_executable()
    # comment explains preferring pythonw.exe over a bare python.exe so
    # login doesn't flash a visible console/dock icon.
    return {
        "Label": LABEL,
        "ProgramArguments": [sys.executable, _MAIN_PY_PATH],
        "RunAtLoad": True,
        "KeepAlive": False,
    }


def ensure_autostart_registered():
    """Idempotent: only (re)writes the plist and reloads it via launchctl if
    it's missing or points somewhere stale (e.g. the repo was moved, or the
    interpreter path changed). Safe to call on every startup from main.py."""
    try:
        desired = plistlib.dumps(_plist_contents())

        existing = None
        if os.path.exists(PLIST_PATH):
            try:
                with open(PLIST_PATH, "rb") as f:
                    existing = f.read()
            except OSError:
                existing = None

        if existing == desired:
            return

        os.makedirs(os.path.dirname(PLIST_PATH), exist_ok=True)
        with open(PLIST_PATH, "wb") as f:
            f.write(desired)

        subprocess.run(["launchctl", "load", PLIST_PATH], check=False)
    except Exception:
        logger.exception("ensure_autostart_registered failed")
