"""Persistence for "The Board" tab: a flat, importance-sorted task list
distinct from the Tasks tab's recurring-with-minutes-targets tracker.

Board tasks live in board.json (same atomic-write pattern as tasks.json /
config.py). A board task's "info" is any mix of text/photo/link -- unlike
review_store's problems, which force a single description_type, all three
fields are independently optional here and whichever are filled in get
shown together in the detail popup.

Recurring board tasks (e.g. "clean toilet every Monday") don't use
tasks_store's scheduling engine -- they just carry a set of weekday codes.
Finishing one computes next_due_date (the next matching weekday) instead of
deleting it; reactivate_due_recurring() is called on every Board tab refresh
to pull finished recurring tasks whose next_due_date has arrived back into
the active list.
"""
import copy
import json
import os
import uuid
from datetime import date, datetime, timedelta

from tasks_store import WEEKDAY_CODES

BOARD_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "board.json")
PHOTOS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "board_photos")

DEFAULT_BOARD_TASK = {
    "name": "",
    "importance": 5,  # 1-10, most important sorts first
    "recurringDays": [],  # WEEKDAY_CODES entries; empty = one-off task
    "descriptionText": "",
    "descriptionPhotoPath": None,
    "descriptionLink": "",
    "firstOpenedAt": None,  # set the first time "View Details" is opened
    "finished": False,
    "finishedAt": None,
    "nextDueDate": None,  # recurring tasks only, set when finished
}


def _new_id():
    return uuid.uuid4().hex


def load_board():
    if not os.path.exists(BOARD_PATH):
        return []
    try:
        with open(BOARD_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return []
    tasks = data.get("tasks", []) if isinstance(data, dict) else []
    return [t for t in tasks if isinstance(t, dict)]


def save_board(tasks):
    tmp_path = BOARD_PATH + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump({"tasks": tasks}, f, indent=2)
    os.replace(tmp_path, BOARD_PATH)


def get_task(task_id):
    for task in load_board():
        if task["id"] == task_id:
            return task
    return None


def save_photo_bytes(data, original_filename):
    os.makedirs(PHOTOS_DIR, exist_ok=True)
    ext = os.path.splitext(original_filename or "")[1].lower() or ".png"
    filename = f"{uuid.uuid4().hex}{ext}"
    path = os.path.join(PHOTOS_DIR, filename)
    with open(path, "wb") as f:
        f.write(data)
    return path


def create_task(
    name, importance, recurring_days=None,
    description_text="", description_link="",
    photo_bytes=None, photo_filename=None,
):
    task = copy.deepcopy(DEFAULT_BOARD_TASK)
    task["id"] = _new_id()
    task["name"] = name
    task["importance"] = max(1, min(10, int(importance)))
    task["recurringDays"] = [d for d in (recurring_days or []) if d in WEEKDAY_CODES]
    task["descriptionText"] = description_text or ""
    task["descriptionLink"] = description_link or ""
    if photo_bytes is not None:
        task["descriptionPhotoPath"] = save_photo_bytes(photo_bytes, photo_filename)
    task["createdAt"] = datetime.now().isoformat()

    tasks = load_board()
    tasks.append(task)
    save_board(tasks)
    return task


def delete_task(task_id):
    tasks = [t for t in load_board() if t["id"] != task_id]
    save_board(tasks)


def mark_opened(task_id):
    """Records the first time a task's detail popup is opened -- shown in
    place of a "first reviewed" date, since board tasks have no review
    concept. A no-op after the first call."""
    tasks = load_board()
    changed = False
    for task in tasks:
        if task["id"] == task_id and not task.get("firstOpenedAt"):
            task["firstOpenedAt"] = datetime.now().isoformat()
            changed = True
    if changed:
        save_board(tasks)
    return get_task(task_id)


def _next_weekday_date(recurring_days, after):
    """First date strictly after `after` whose weekday code is in
    recurring_days. Searches up to 7 days ahead (guaranteed to find one)."""
    for offset in range(1, 8):
        candidate = after + timedelta(days=offset)
        if WEEKDAY_CODES[candidate.weekday()] in recurring_days:
            return candidate
    return after + timedelta(days=1)


def finish_task(task_id):
    """Marks a task finished. Recurring tasks get a next_due_date instead of
    staying finished forever -- reactivate_due_recurring() pulls them back
    into the active list once that date arrives."""
    tasks = load_board()
    for task in tasks:
        if task["id"] == task_id:
            task["finished"] = True
            task["finishedAt"] = datetime.now().isoformat()
            if task.get("recurringDays"):
                task["nextDueDate"] = _next_weekday_date(task["recurringDays"], date.today()).isoformat()
            else:
                task["nextDueDate"] = None
    save_board(tasks)
    return get_task(task_id)


def reactivate_due_recurring():
    """Moves finished recurring tasks whose next_due_date has arrived back
    into the active list, resetting their "opened" stamp for the new
    occurrence. Call on every Board tab refresh."""
    tasks = load_board()
    today = date.today().isoformat()
    changed = False
    for task in tasks:
        if task.get("finished") and task.get("recurringDays") and task.get("nextDueDate"):
            if task["nextDueDate"] <= today:
                task["finished"] = False
                task["finishedAt"] = None
                task["nextDueDate"] = None
                task["firstOpenedAt"] = None
                changed = True
    if changed:
        save_board(tasks)


def list_active_tasks():
    reactivate_due_recurring()
    tasks = [t for t in load_board() if not t.get("finished")]
    tasks.sort(key=lambda t: (-t.get("importance", 0), t.get("name", "").lower()))
    return tasks


def list_finished_tasks():
    tasks = [t for t in load_board() if t.get("finished")]
    tasks.sort(key=lambda t: t.get("finishedAt") or "", reverse=True)
    return tasks
