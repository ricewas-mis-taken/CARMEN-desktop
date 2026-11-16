"""Tells you once, the moment a task's daily target is reached.

check() is polled by the window tracker while a task session runs. It adds the live session to what the task has
logged today (the same numbers the task card shows) and, the first time that reaches the day's target, returns what
to say. The task is remembered for that day in goal_notified.json, so it is said once however long you carry on.
Only per-day goals have a daily target; week and month goals are left to their own card."""
import json
import os
from datetime import date

import paths
import session_history
import tasks_store

PATH = paths.private_path("goal_notified.json")


def _load():
    try:
        with open(PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            return data
    except (OSError, ValueError):
        pass
    return {}


def _save(data):
    tmp = PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f)
    os.replace(tmp, PATH)


def check(status, today=None, sessions=None):
    """{"name", "required", "logged"} (minutes) when the running task session has just taken its task to the day's
    target for the first time today, else None."""
    if not status or not status.get("isActive") or status.get("isPaused") or status.get("isBreak"):
        return None
    if status.get("source") != "task" or not status.get("eventId"):
        return None
    task = tasks_store.get_task(status["eventId"])
    if not task or tasks_store.is_period_task(task):
        return None
    today = today or date.today()
    day = today.isoformat()
    seen = _load()
    if task["id"] in seen.get(day, []):
        return None
    sessions = session_history.load_all() if sessions is None else sessions
    snap = tasks_store.goal_snapshot(task, today, sessions, live_status=status)
    required = snap["required"]
    if required <= 0 or snap["logged"] + snap["cashed"] < required:
        return None
    seen = {day: seen.get(day, []) + [task["id"]]}           # older days are dropped as they are no use
    _save(seen)
    return {"name": task.get("name") or "Your task", "required": required, "logged": snap["logged"]}
