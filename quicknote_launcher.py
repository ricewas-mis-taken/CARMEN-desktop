"""What the desktop right-click entry "QuickNote" runs.

Asks the running Carmen Focus app to open its QuickNote window. If the app is not
running it is started with --quicknote, which opens the note as soon as it is up.
Kept tiny and dependency-light: Explorer starts this on every click.
"""
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

API_URL = "http://127.0.0.1:5847/quicknote/open"


def _api_token():
    try:
        with open(os.path.join(HERE, "private", "config.json"), "r", encoding="utf-8") as f:
            return json.load(f).get("apiToken", "")
    except (OSError, ValueError):
        return ""


def ask_running_app():
    """True if a running app answered and is opening the note."""
    request = urllib.request.Request(
        API_URL, data=b"{}", method="POST",
        headers={"X-Carmen-Token": _api_token(), "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=3) as response:
            return response.status == 200
    except (urllib.error.URLError, OSError):
        return False


def start_app_with_note():
    flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    pythonw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
    subprocess.Popen(
        [pythonw if os.path.exists(pythonw) else sys.executable, os.path.join(HERE, "main.py"), "--quicknote"],
        cwd=HERE, close_fds=True, creationflags=flags,
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )


def main():
    if not ask_running_app():
        start_app_with_note()


if __name__ == "__main__":
    main()
