"""Shared file logger for the calendar module.

Mirrors the rest of this app's philosophy — a corrupt/failed write must never
crash the tray app or the background scheduler thread — but unlike
config.py/session_manager.py (which just silently fall back to defaults),
calendar.db failures and scheduler-loop exceptions get logged here so they're
actually diagnosable, since they run unattended in a background thread
instead of inline with a user-triggered action.
"""
import logging
import os

LOG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "private", "calendar_errors.log")

logger = logging.getLogger("carmen_calendar")
logger.setLevel(logging.WARNING)

if not logger.handlers:
    # private/ (gitignored -- holds every real data file, not just this
    # log) won't exist yet on a fresh clone, and FileHandler doesn't create
    # its parent directory itself -- without this, the very first import of
    # this module would crash the whole app before anything else even runs.
    os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
    handler = logging.FileHandler(LOG_PATH, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    logger.addHandler(handler)
