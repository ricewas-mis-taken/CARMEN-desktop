"""Shared file logger for the calendar module.

Mirrors the rest of this app's philosophy — a corrupt/failed write must never
crash the tray app or the background scheduler thread — but unlike
config.py/session_manager.py (which just silently fall back to defaults),
calendar.db failures and scheduler-loop exceptions get logged here so they're
actually diagnosable, since they run unattended in a background thread
instead of inline with a user-triggered action.
"""
import logging
from logging.handlers import RotatingFileHandler
import os
import paths

LOG_PATH = paths.private_path("calendar_errors.log")

logger = logging.getLogger("carmen_calendar")
logger.setLevel(logging.WARNING)

if not logger.handlers:
    # private/ (gitignored -- holds every real data file, not just this
    # log) won't exist yet on a fresh clone, and FileHandler doesn't create
    # its parent directory itself -- without this, the very first import of
    # this module would crash the whole app before anything else even runs.
    os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
    # Rotated: this one log collects every background thread's failures and used to
    # grow without limit (2 MB and 17,000 lines of repeats was seen).
    handler = RotatingFileHandler(LOG_PATH, maxBytes=1_000_000, backupCount=2, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    logger.addHandler(handler)
