"""Persistence for "The Board" tab: a flat, importance-sorted task list
distinct from the Tasks tab's recurring-with-minutes-targets tracker.

Board tasks live in board.json (same atomic-write pattern as tasks.json /
config.py). A board task's "info" is any mix of text/photo/link -- unlike
review_store's problems, which force a single description_type, all three
fields are independently optional here and whichever are filled in get
shown together in the detail popup.

Recurring board tasks (e.g. "clean toilet every Monday") don't use
tasks_store's scheduling engine. A task's recurrence is either a specific set
of weekday codes ("days" pattern, e.g. Mon+Wed+Fri) or one of the fixed
"weekly"/"monthly"/"yearly" patterns, which always land on the start of the
next week/month/year rather than a specific weekday. Finishing a recurring
task computes next_due_date instead of leaving it finished forever;
reactivate_due_recurring() is called on every Board tab refresh to pull
finished recurring tasks whose next_due_date has arrived back into the
active list. While waiting on that date, a recurring task shows up in
list_upcoming_tasks() rather than list_finished_tasks() -- it's not really
"finished", just dormant until its next occurrence.
"""
import copy
import json
import os
import uuid
from datetime import date, datetime, timedelta

from tasks_store import WEEKDAY_CODES

BOARD_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "board.json")
PHOTOS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "board_photos")

RECURRENCE_PATTERNS = ("days", "weekly", "monthly", "yearly")

DEFAULT_BOARD_TASK = {
    "name": "",
    "importance": 5,  # 1-10, most important sorts first
    "recurringDays": [],  # WEEKDAY_CODES entries; used when recurrencePattern == "days"
    "recurrencePattern": "none",  # "none" | one of RECURRENCE_PATTERNS
    "descriptionText": "",
    "descriptionPhotoPath": None,
    "descriptionLink": "",
    "firstOpenedAt": None,  # set the first time "View Details" is opened
    "finished": False,
    "finishedAt": None,
    "nextDueDate": None,  # recurring tasks only, set when finished
    # Sync scaffolding (multi-device sync) -- board.json has no schema
    # versioning of its own, so pre-existing tasks get these backfilled by
    # _backfill_sync_fields() below rather than a one-time migration step.
    # deviceId/isDeleted are left untouched here and by every write in this
    # module -- populating "which device" and rewriting delete_task()'s
    # current hard delete (the row is filtered out and gone -- no
    # tombstone, so a deletion could never propagate to another device) into
    # a real soft-delete is the sync module's job (Phase 3), not this
    # schema-shape change.
    "updatedAt": None,
    "deviceId": None,
    "isDeleted": False,
}


def _new_id():
    return uuid.uuid4().hex


def _backfill_sync_fields(task):
    """Adds updatedAt/deviceId/isDeleted to a task dict saved before they
    existed. updatedAt backfills from createdAt (the closest existing
    "when was this last touched" a task already has) instead of "now", to
    avoid manufacturing a fake recent-write time for old data. Returns
    True if the dict was actually changed, so callers only need to rewrite
    board.json when something really was migrated."""
    changed = False
    if "updatedAt" not in task:
        task["updatedAt"] = task.get("createdAt") or datetime.now().isoformat()
        changed = True
    if "deviceId" not in task:
        task["deviceId"] = None
        changed = True
    if "isDeleted" not in task:
        task["isDeleted"] = False
        changed = True
    return changed


def load_board():
    if not os.path.exists(BOARD_PATH):
        return []
    try:
        with open(BOARD_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return []
    tasks = data.get("tasks", []) if isinstance(data, dict) else []
    tasks = [t for t in tasks if isinstance(t, dict)]

    # Not any(...) -- that would short-circuit on the first task that
    # actually needs backfilling and leave every task after it unmigrated.
    needs_save = [_backfill_sync_fields(task) for task in tasks]
    if any(needs_save):
        save_board(tasks)

    return tasks


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


def _resolve_recurrence(recurring_days, recurrence_pattern):
    """Specific weekdays win if given; otherwise a fixed weekly/monthly/
    yearly pattern; otherwise the task doesn't recur at all."""
    recurring_days = [d for d in (recurring_days or []) if d in WEEKDAY_CODES]
    if recurring_days:
        return recurring_days, "days"
    if recurrence_pattern in ("weekly", "monthly", "yearly"):
        return [], recurrence_pattern
    return [], "none"


def create_task(
    name, importance, recurring_days=None, recurrence_pattern=None,
    description_text="", description_link="",
    photo_bytes=None, photo_filename=None,
):
    task = copy.deepcopy(DEFAULT_BOARD_TASK)
    task["id"] = _new_id()
    task["name"] = name
    task["importance"] = max(1, min(10, int(importance)))
    task["recurringDays"], task["recurrencePattern"] = _resolve_recurrence(recurring_days, recurrence_pattern)
    task["descriptionText"] = description_text or ""
    task["descriptionLink"] = description_link or ""
    if photo_bytes is not None:
        task["descriptionPhotoPath"] = save_photo_bytes(photo_bytes, photo_filename)
    task["createdAt"] = datetime.now().isoformat()
    task["updatedAt"] = task["createdAt"]

    tasks = load_board()
    tasks.append(task)
    save_board(tasks)
    return task


def update_task(
    task_id, name, recurring_days=None, recurrence_pattern=None,
    description_text="", description_link="",
    photo_bytes=None, photo_filename=None, remove_photo=False,
):
    """Replaces a task's editable details (name, recurrence, info) in
    place. Importance is intentionally not touched here -- it has its own
    dedicated editor on the board card's badge."""
    tasks = load_board()
    for task in tasks:
        if task["id"] == task_id:
            task["name"] = name
            task["recurringDays"], task["recurrencePattern"] = _resolve_recurrence(
                recurring_days, recurrence_pattern
            )
            task["descriptionText"] = description_text or ""
            task["descriptionLink"] = description_link or ""
            if remove_photo and not photo_bytes:
                task["descriptionPhotoPath"] = None
            if photo_bytes is not None:
                task["descriptionPhotoPath"] = save_photo_bytes(photo_bytes, photo_filename)
            task["updatedAt"] = datetime.now().isoformat()
    save_board(tasks)
    return get_task(task_id)


def delete_task(task_id):
    tasks = [t for t in load_board() if t["id"] != task_id]
    save_board(tasks)


def update_importance(task_id, importance):
    tasks = load_board()
    for task in tasks:
        if task["id"] == task_id:
            task["importance"] = max(1, min(10, int(importance)))
            task["updatedAt"] = datetime.now().isoformat()
    save_board(tasks)
    return get_task(task_id)


def mark_opened(task_id):
    """Records the first time a task's detail popup is opened -- shown in
    place of a "first reviewed" date, since board tasks have no review
    concept. A no-op after the first call."""
    tasks = load_board()
    changed = False
    for task in tasks:
        if task["id"] == task_id and not task.get("firstOpenedAt"):
            task["firstOpenedAt"] = datetime.now().isoformat()
            task["updatedAt"] = task["firstOpenedAt"]
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


def _next_week_start(after):
    """The next Monday strictly after `after` -- "weekly" tasks always land
    on the start of the week, regardless of which day they were finished."""
    return after + timedelta(days=7 - after.weekday())


def _next_month_start(after):
    """The 1st of the next calendar month strictly after `after`."""
    if after.month == 12:
        return date(after.year + 1, 1, 1)
    return date(after.year, after.month + 1, 1)


def _next_year_start(after):
    """January 1st of the next calendar year."""
    return date(after.year + 1, 1, 1)


def _is_recurring(task):
    return bool(task.get("recurringDays")) or task.get("recurrencePattern") in ("weekly", "monthly", "yearly")


def finish_task(task_id):
    """Marks a task finished. Recurring tasks get a next_due_date instead of
    staying finished forever -- reactivate_due_recurring() pulls them back
    into the active list once that date arrives."""
    tasks = load_board()
    today = date.today()
    for task in tasks:
        if task["id"] == task_id:
            task["finished"] = True
            task["finishedAt"] = datetime.now().isoformat()
            task["updatedAt"] = task["finishedAt"]
            pattern = task.get("recurrencePattern") or ("days" if task.get("recurringDays") else "none")
            if pattern == "days" and task.get("recurringDays"):
                task["nextDueDate"] = _next_weekday_date(task["recurringDays"], today).isoformat()
            elif pattern == "weekly":
                task["nextDueDate"] = _next_week_start(today).isoformat()
            elif pattern == "monthly":
                task["nextDueDate"] = _next_month_start(today).isoformat()
            elif pattern == "yearly":
                task["nextDueDate"] = _next_year_start(today).isoformat()
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
        if task.get("finished") and _is_recurring(task) and task.get("nextDueDate"):
            if task["nextDueDate"] <= today:
                task["finished"] = False
                task["finishedAt"] = None
                task["nextDueDate"] = None
                task["firstOpenedAt"] = None
                task["updatedAt"] = datetime.now().isoformat()
                changed = True
    if changed:
        save_board(tasks)


def list_active_tasks():
    reactivate_due_recurring()
    tasks = [t for t in load_board() if not t.get("finished")]
    tasks.sort(key=lambda t: (-t.get("importance", 0), t.get("name", "").lower()))
    return tasks


def list_finished_tasks():
    """One-off tasks that are actually done -- recurring tasks waiting on
    their next occurrence show up in list_upcoming_tasks() instead."""
    tasks = [t for t in load_board() if t.get("finished") and not _is_recurring(t)]
    tasks.sort(key=lambda t: t.get("finishedAt") or "", reverse=True)
    return tasks


def list_upcoming_tasks():
    """Finished recurring tasks waiting for their next_due_date, soonest
    first -- these live off the main board until reactivate_due_recurring()
    pulls them back in."""
    tasks = [t for t in load_board() if t.get("finished") and _is_recurring(t)]
    tasks.sort(key=lambda t: t.get("nextDueDate") or "")
    return tasks
