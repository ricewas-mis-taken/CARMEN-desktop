"""Daily backup of all Carmen Focus data files.

Run directly (`python backup.py`) or via Windows Task Scheduler.
Keeps the last 30 daily snapshots in %USERPROFILE%\CarmenBackups\.
"""
import os
import shutil
from datetime import datetime, timedelta

import calendar_store

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "private")
BACKUP_ROOT = os.path.join(os.path.expanduser("~"), "CarmenBackups")
KEEP_DAYS = 30

# calendar.db is handled separately via calendar_store.export_db() -- it runs
# in WAL mode, so a plain file copy can miss recently-committed rows still
# sitting in calendar.db-wal, or copy a page mid-checkpoint. export_db() uses
# SQLite's own online backup API instead, which is safe to call while the app
# is running and always produces a complete, consistent snapshot.
FILES = [
    "session_history.json",
    "tasks.json",
    "board.json",
    "config.json",
    os.path.join("data", "daily_summaries.json"),
]

# Copied recursively rather than file-by-file -- every photo a board/review
# task links to lives under one of these, and a restore with a dangling
# descriptionPhotoPath is as good as losing the attachment outright.
DIRS = [
    os.path.join("data", "board_photos"),
    os.path.join("data", "review_photos"),
]


def run_backup():
    today = datetime.now().strftime("%Y-%m-%d")
    dest = os.path.join(BACKUP_ROOT, today)
    os.makedirs(dest, exist_ok=True)

    copied = []

    calendar_src = os.path.join(DATA_DIR, "calendar.db")
    if os.path.exists(calendar_src):
        if calendar_store.export_db(os.path.join(dest, "calendar.db")):
            copied.append("calendar.db")

    for name in FILES:
        src = os.path.join(DATA_DIR, name)
        if os.path.exists(src):
            dest_path = os.path.join(dest, name)
            os.makedirs(os.path.dirname(dest_path), exist_ok=True)
            shutil.copy2(src, dest_path)
            copied.append(name)

    for name in DIRS:
        src = os.path.join(DATA_DIR, name)
        if os.path.isdir(src):
            shutil.copytree(src, os.path.join(dest, name), dirs_exist_ok=True)
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
