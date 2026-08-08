"""Daily backup of all Carmen Focus data files.

Run directly (`python backup.py`) or via Windows Task Scheduler.
Keeps the last 30 daily snapshots in %USERPROFILE%\CarmenBackups\.
"""
import os
import shutil
from datetime import datetime, timedelta

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "private")
BACKUP_ROOT = os.path.join(os.path.expanduser("~"), "CarmenBackups")
KEEP_DAYS = 30

FILES = [
    "calendar.db",
    "session_history.json",
    "tasks.json",
    "config.json",
]


def run_backup():
    today = datetime.now().strftime("%Y-%m-%d")
    dest = os.path.join(BACKUP_ROOT, today)
    os.makedirs(dest, exist_ok=True)

    copied = []
    for name in FILES:
        src = os.path.join(DATA_DIR, name)
        if os.path.exists(src):
            shutil.copy2(src, os.path.join(dest, name))
            copied.append(name)

    # Prune snapshots older than KEEP_DAYS
    cutoff = datetime.now() - timedelta(days=KEEP_DAYS)
    pruned = []
    for entry in os.listdir(BACKUP_ROOT):
        entry_path = os.path.join(BACKUP_ROOT, entry)
        if not os.path.isdir(entry_path):
            continue
        try:
            snap_date = datetime.strptime(entry, "%Y-%m-%d")
        except ValueError:
            continue
        if snap_date < cutoff:
            shutil.rmtree(entry_path)
            pruned.append(entry)

    print(f"[{datetime.now():%Y-%m-%d %H:%M}] Backed up to {dest}")
    print(f"  Files: {', '.join(copied)}")
    if pruned:
        print(f"  Pruned old snapshots: {', '.join(pruned)}")


if __name__ == "__main__":
    run_backup()
