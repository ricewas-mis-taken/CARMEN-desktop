"""Persistence + pure scheduling/progress math for the Tasks tab.

Tasks live in tasks.json (same atomic-write pattern as config.py /
session_history.py). A task's actual work sessions are NOT stored here --
starting a task just calls session_manager.start_session(source="task",
event_id=task["id"], event_title=task["name"]), the same generic mechanism
calendar_scheduler.py already uses for calendar-event-triggered sessions.
That means every completed task session shows up in session_history.json
like any other, and this module's job is to read it back out and turn it
into "minutes worked on task X today" / "how much vacation time has task X
banked" -- no separate log of its own to keep in sync.
"""
import copy
import json
import os
import uuid
from datetime import date, datetime, timedelta

TASKS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "private", "tasks.json")

WEEKDAY_CODES = ["MO", "TU", "WE", "TH", "FR", "SA", "SU"]

# "Until I burnout" isn't literally unbounded -- session_manager always needs
# a concrete duration -- so it's a long enough ceiling that pause/end are the
# only realistic way the session stops.
BURNOUT_MINUTES = 8 * 60

DEFAULT_TASK = {
    "name": "",
    "color": "#5B8DEF",
    "targetMinutes": 30,
    "recurrence": "daily",  # "daily" or "weekly_days"
    "weekdays": [],  # WEEKDAY_CODES entries, only used for "weekly_days"
    "lockMode": "soft",
    "processBlocklist": [],
    "domainWhitelist": [],
    "cashedInDates": {},  # {"YYYY-MM-DD": minutes} spent from the vacation balance
    "archived": False,
    # Sync scaffolding (multi-device sync) -- tasks.json has no schema
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
    tasks.json when something really was migrated."""
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


def load_tasks():
    if not os.path.exists(TASKS_PATH):
        return []
    try:
        with open(TASKS_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return []
    tasks = data.get("tasks", []) if isinstance(data, dict) else []
    tasks = [t for t in tasks if isinstance(t, dict)]

    # Not any(...) -- that would short-circuit on the first task that
    # actually needs backfilling and leave every task after it unmigrated.
    needs_save = [_backfill_sync_fields(task) for task in tasks]
    if any(needs_save):
        save_tasks(tasks)

    return tasks


def save_tasks(tasks):
    # private/ (gitignored, holds every real data file) won't exist yet on
    # a fresh clone.
    os.makedirs(os.path.dirname(TASKS_PATH), exist_ok=True)
    tmp_path = TASKS_PATH + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump({"tasks": tasks}, f, indent=2)
    os.replace(tmp_path, TASKS_PATH)


def get_task(task_id):
    for task in load_tasks():
        if task["id"] == task_id:
            return task
    return None


def create_task(data):
    task = copy.deepcopy(DEFAULT_TASK)
    task.update(data)
    task["id"] = _new_id()
    task["createdAt"] = datetime.now().isoformat()
    task["updatedAt"] = task["createdAt"]
    tasks = load_tasks()
    tasks.append(task)
    save_tasks(tasks)
    return task


def update_task(task_id, data):
    tasks = load_tasks()
    updated = None
    for task in tasks:
        if task["id"] == task_id:
            task.update(data)
            task["updatedAt"] = datetime.now().isoformat()
            updated = task
            break
    if updated is not None:
        save_tasks(tasks)
    return updated


def delete_task(task_id):
    tasks = load_tasks()
    remaining = [t for t in tasks if t["id"] != task_id]
    if len(remaining) != len(tasks):
        save_tasks(remaining)
        return True
    return False


# --- scheduling ---

def is_scheduled_on(task, day):
    """Whether `task` calls for work on date `day` at all -- "daily" always
    does; "weekly_days" only on the checked weekdays."""
    if task.get("recurrence") == "weekly_days":
        code = WEEKDAY_CODES[day.weekday()]
        return code in (task.get("weekdays") or [])
    return True


def required_minutes_for_date(task, day):
    """Target minutes for `day`, after subtracting whatever vacation time was
    cashed in against that specific date. Never negative, and 0 on a day the
    task isn't scheduled at all (nothing to cash in against, either)."""
    if not is_scheduled_on(task, day):
        return 0
    target = task.get("targetMinutes", 0)
    cashed = (task.get("cashedInDates") or {}).get(day.isoformat(), 0)
    return max(0, target - cashed)


# --- worked-time math (pause-aware) ---

def worked_seconds(start_iso, end_iso, violation_log):
    """Wall-clock seconds actually worked between start_iso and end_iso,
    excluding any time spent paused. violation_log is the same list
    session_manager stores pause/resume entries in (each a dict with
    "kind" in {"pause", "resume"} and a "timestamp"); a session that ended
    while still paused correctly stops counting at the last pause.

    Shared by both a finished session_history entry (end_iso is that
    session's recorded endTime) and the currently-running session (end_iso
    is "now") so a task's displayed progress never jumps when the active
    session finalizes into history.
    """
    if not start_iso:
        return 0
    start = datetime.fromisoformat(start_iso)
    end = datetime.fromisoformat(end_iso) if end_iso else datetime.now()
    if end <= start:
        return 0

    events = sorted(
        (e for e in (violation_log or []) if e.get("kind") in ("pause", "resume")),
        key=lambda e: e["timestamp"],
    )

    total = 0.0
    cursor = start
    paused = False
    for event in events:
        ts = datetime.fromisoformat(event["timestamp"])
        if ts <= cursor:
            continue
        if not paused:
            total += (ts - cursor).total_seconds()
        cursor = ts
        paused = event["kind"] == "pause"

    if not paused and end > cursor:
        total += (end - cursor).total_seconds()

    return max(0, int(total))


def _task_history_entries(task_id, sessions):
    return [
        s for s in sessions
        if s.get("source") in ("task", "review") and s.get("eventId") == task_id and s.get("startTime")
    ]


def logged_seconds_for_date(task, day, sessions, live_status=None):
    """Total seconds worked on `task` on date `day`, from finished
    session_history entries plus (if `day` is today and a live session for
    this task is running) the in-progress session's elapsed time so far."""
    total = 0
    for entry in _task_history_entries(task["id"], sessions):
        start = datetime.fromisoformat(entry["startTime"])
        if start.date() != day:
            continue
        total += worked_seconds(entry["startTime"], entry.get("endTime"), entry.get("violationLog"))

    if (
        live_status
        and live_status.get("isActive")
        and live_status.get("source") in ("task", "review")
        and live_status.get("eventId") == task["id"]
        and live_status.get("startTime")
    ):
        start = datetime.fromisoformat(live_status["startTime"])
        if start.date() == day:
            total += worked_seconds(live_status["startTime"], None, live_status.get("violationLog"))

    return total


def vacation_balance_minutes(task, sessions, today=None):
    """Minutes of "vacation" this task has banked: the sum of every past
    scheduled day's surplus (minutes logged beyond that day's requirement,
    which already accounts for anything cashed in against it) minus every
    minute ever cashed in, regardless of which date it was spent against.
    Today itself never contributes surplus -- a day isn't "banked" until
    it's over, so same-day overwork can't be cashed in against itself."""
    today = today or date.today()
    cashed_in_dates = task.get("cashedInDates") or {}

    total_earned = 0.0
    seen_days = set()
    for entry in _task_history_entries(task["id"], sessions):
        start = datetime.fromisoformat(entry["startTime"])
        day = start.date()
        if day >= today or day in seen_days or not is_scheduled_on(task, day):
            continue
        seen_days.add(day)
        logged_minutes = logged_seconds_for_date(task, day, sessions) / 60
        required = required_minutes_for_date(task, day)
        total_earned += max(0, logged_minutes - required)

    total_spent = sum(cashed_in_dates.values())
    return max(0.0, total_earned - total_spent)


def cash_in(task_id, target_date, minutes, sessions):
    """Spends `minutes` of task's banked vacation time against target_date
    (a date object), reducing that date's required minutes. Raises
    ValueError if the task doesn't have enough banked or minutes isn't
    positive. Returns the updated task."""
    if minutes <= 0:
        raise ValueError("minutes must be positive")
    task = get_task(task_id)
    if task is None:
        raise ValueError("no such task")
    balance = vacation_balance_minutes(task, sessions)
    # Round rather than compare against the raw float -- the UI shows and
    # accepts whole minutes rounded from the true balance (e.g. 0.97m reads
    # as "1m banked"), so comparing a whole-minute request against the
    # unrounded balance would reject exactly the amount the user was shown.
    available = int(round(balance))
    if minutes > available:
        raise ValueError(f"only {available} vacation minute(s) available")

    cashed_in_dates = dict(task.get("cashedInDates") or {})
    key = target_date.isoformat()
    cashed_in_dates[key] = cashed_in_dates.get(key, 0) + minutes
    return update_task(task_id, {"cashedInDates": cashed_in_dates})
