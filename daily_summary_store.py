"""Writes a per-task daily summary to data/daily_summaries.json after each
session ends, so historical work-time data survives even if session_history.json
is lost or trimmed. At most one day (today's in-progress sessions) is
unprotected at any given time.

Format:
{
  "2026-07-25": {
    "<taskId>": {"taskName": "usaco", "secondsWorked": 3600},
    ...
  },
  ...
}

Once a day's entry is written it is never overwritten — the first write is
canonical, so calling flush_through_yesterday() repeatedly is safe and cheap.
"""
import json
import os
from datetime import date, datetime

SUMMARY_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "data", "daily_summaries.json"
)


def _load():
    if not os.path.exists(SUMMARY_PATH):
        return {}
    try:
        with open(SUMMARY_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def _save(data):
    tmp = SUMMARY_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, SUMMARY_PATH)


def flush_through_yesterday():
    """Computes and persists per-task second totals for all days before today
    that aren't already recorded. Already-written days are skipped (first write
    is canonical). A no-op if there are no sessions or tasks."""
    try:
        import session_history
        import tasks_store

        today = date.today()
        sessions = session_history.load_all()
        tasks = tasks_store.load_tasks()
        if not sessions or not tasks:
            return

        summaries = _load()
        changed = False

        past_days = {
            datetime.fromisoformat(s["startTime"]).date()
            for s in sessions
            if s.get("startTime")
            and datetime.fromisoformat(s["startTime"]).date() < today
        }

        for day in past_days:
            day_str = day.isoformat()
            if day_str in summaries:
                continue
            day_entry = {}
            for task in tasks:
                secs = tasks_store.logged_seconds_for_date(task, day, sessions)
                if secs > 0:
                    day_entry[task["id"]] = {
                        "taskName": task["name"],
                        "secondsWorked": secs,
                    }
            if day_entry:
                summaries[day_str] = day_entry
                changed = True

        if changed:
            _save(summaries)
    except Exception:
        pass  # never crash the caller over a backup write
