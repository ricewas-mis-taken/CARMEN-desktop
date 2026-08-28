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
import threading
import uuid
from datetime import date, datetime, timedelta

import device_id

TASKS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "private", "tasks.json")

# Guards every read-modify-write sequence below (create/update/delete/cash_in)
# against a lost-update race -- the Flask API thread and the Qt thread can
# both call into this module. Reentrant because cash_in() needs to hold it
# across its own balance check *and* the update_task() call it makes.
_lock = threading.RLock()

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
    "targetMinutesHistory": [],  # [{"date": "YYYY-MM-DD", "minutes": N}], oldest first
    "archived": False,
    # Sync scaffolding (multi-device sync). See load_tasks()/delete_task()
    # for how isDeleted is populated and filtered.
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
    if "targetMinutesHistory" not in task:
        # Best-effort backfill for tasks saved before target-minute history
        # was tracked -- there's no way to recover what the target actually
        # was on past days, so this assumes it's always been today's value.
        # Not perfect for pre-existing data, but every change from now on
        # is recorded, so required_minutes_for_date stops drifting for good.
        created_date = (task.get("createdAt") or datetime.now().isoformat())[:10]
        task["targetMinutesHistory"] = [
            {"date": created_date, "minutes": task.get("targetMinutes", 0)}
        ]
        changed = True
    return changed


class TasksLoadError(Exception):
    """Raised when tasks.json exists but can't be read/parsed. Only raised
    for include_deleted=True callers (the read-modify-write mutators below) --
    a plain display read still returns [] so a transient glitch doesn't crash
    the UI. A mutator must NOT treat a failed read as "no tasks yet": doing so
    would let its own save_tasks() call overwrite every real task on disk
    with just whatever it's adding/changing."""


def load_tasks(include_deleted=False):
    """By default excludes soft-deleted tasks (see delete_task()), matching
    the pre-tombstone behavior every existing caller expects. Callers that
    read-modify-write the whole file (create_task, update_task, etc.) must
    pass include_deleted=True so a save doesn't silently drop tombstones
    that a sync module will need later."""
    if not os.path.exists(TASKS_PATH):
        return []
    try:
        with open(TASKS_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError) as exc:
        if include_deleted:
            raise TasksLoadError(f"failed to read {TASKS_PATH}: {exc}") from exc
        return []
    tasks = data.get("tasks", []) if isinstance(data, dict) else []
    tasks = [t for t in tasks if isinstance(t, dict)]

    # Not any(...) -- that would short-circuit on the first task that
    # actually needs backfilling and leave every task after it unmigrated.
    needs_save = [_backfill_sync_fields(task) for task in tasks]
    if any(needs_save):
        save_tasks(tasks)

    if not include_deleted:
        tasks = [t for t in tasks if not t.get("isDeleted")]
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


class DuplicateColorError(Exception):
    """Raised when a task's color collides with another (non-deleted) task's
    color -- colors are how tasks are told apart at a glance elsewhere in
    the app (progress bars, calendar blocks), so two tasks sharing one
    defeats that instantly. Callers (the Qt task editor) catch this to show
    a real message instead of silently letting the colors collide."""


def _color_in_use(color, exclude_id=None):
    color = color.lower()
    return any(
        task["id"] != exclude_id and (task.get("color") or "").lower() == color
        for task in load_tasks()
    )


# Only used when create_task() gets no explicit color -- picks the first of
# these not already in use instead of always handing out
# DEFAULT_TASK["color"], which let two colorless tasks collide silently
# (the uniqueness check only ever ran against an explicitly-supplied color).
_FALLBACK_COLOR_PALETTE = [
    "#5B8DEF", "#E53935", "#43A047", "#FB8C00", "#8E24AA",
    "#00897B", "#3949AB", "#D81B60", "#6D4C41", "#546E7A",
]


def _pick_available_color():
    used = {(task.get("color") or "").lower() for task in load_tasks()}
    for candidate in _FALLBACK_COLOR_PALETTE:
        if candidate.lower() not in used:
            return candidate
    return DEFAULT_TASK["color"]


def create_task(data):
    with _lock:
        color = data.get("color")
        if color:
            if _color_in_use(color):
                raise DuplicateColorError("Another task is already using this color.")
        else:
            color = _pick_available_color()

        task = copy.deepcopy(DEFAULT_TASK)
        task.update(data)
        task["color"] = color
        task["id"] = _new_id()
        task["createdAt"] = datetime.now().isoformat()
        task["updatedAt"] = task["createdAt"]
        task["targetMinutesHistory"] = [
            {"date": task["createdAt"][:10], "minutes": task.get("targetMinutes", 0)}
        ]
        tasks = load_tasks(include_deleted=True)
        tasks.append(task)
        save_tasks(tasks)
        return task


def update_task(task_id, data):
    with _lock:
        new_color = data.get("color")
        if new_color and _color_in_use(new_color, exclude_id=task_id):
            raise DuplicateColorError("Another task is already using this color.")

        tasks = load_tasks(include_deleted=True)
        updated = None
        for task in tasks:
            if task["id"] == task_id:
                new_target = data.get("targetMinutes")
                if new_target is not None and new_target != task.get("targetMinutes"):
                    # Record when the target changed instead of overwriting it in
                    # place, so vacation_balance_minutes can look up whatever
                    # target was actually in effect on a given past day -- a
                    # goal-time change today must never retroactively change
                    # what counted as "surplus" on days before it.
                    history = list(task.get("targetMinutesHistory") or [])
                    today_key = date.today().isoformat()
                    if history and history[-1]["date"] == today_key:
                        history[-1] = {"date": today_key, "minutes": new_target}
                    else:
                        history.append({"date": today_key, "minutes": new_target})
                    task["targetMinutesHistory"] = history
                task.update(data)
                task["updatedAt"] = datetime.now().isoformat()
                updated = task
                break
        if updated is not None:
            save_tasks(tasks)
        return updated


def delete_task(task_id):
    """Soft-deletes: the row is tombstoned (isDeleted=True) rather than
    removed, so a future sync push can tell other devices this task was
    deleted instead of the deletion silently never propagating. Permanent
    physical removal is deliberately not implemented yet -- a follow-up
    once sync_now() can confirm every known device has seen the tombstone."""
    with _lock:
        tasks = load_tasks(include_deleted=True)
        found = False
        for task in tasks:
            if task["id"] == task_id and not task.get("isDeleted"):
                task["isDeleted"] = True
                task["updatedAt"] = datetime.now().isoformat()
                task["deviceId"] = device_id.get_device_id()
                found = True
        if found:
            save_tasks(tasks)
        return found


# --- scheduling ---

def is_scheduled_on(task, day):
    """Whether `task` calls for work on date `day` at all -- "daily" always
    does; "weekly_days" only on the checked weekdays."""
    if task.get("recurrence") == "weekly_days":
        code = WEEKDAY_CODES[day.weekday()]
        return code in (task.get("weekdays") or [])
    return True


def _target_minutes_for_date(task, day):
    """The goal-time value that was actually in effect on `day`, from
    targetMinutesHistory (oldest first) -- not necessarily today's
    targetMinutes. A later goal-time change must not reach backward and
    change what counted as surplus/vacation on earlier days."""
    history = task.get("targetMinutesHistory") or []
    if not history:
        return task.get("targetMinutes", 0)
    day_key = day.isoformat()
    applicable = history[0]["minutes"]
    for entry in history:
        if entry["date"] <= day_key:
            applicable = entry["minutes"]
        else:
            break
    return applicable


def required_minutes_for_date(task, day):
    """Target minutes for `day` (as of whatever the goal time actually was
    on that day), after subtracting whatever vacation time was cashed in
    against that specific date. Never negative, and 0 on a day the task
    isn't scheduled at all (nothing to cash in against, either)."""
    if not is_scheduled_on(task, day):
        return 0
    target = _target_minutes_for_date(task, day)
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
    # Held across the balance check *and* the update_task() write below --
    # otherwise two near-simultaneous cash_in() calls can both read the same
    # balance, both pass the check, and both succeed, double-spending the
    # same banked minutes. _lock is reentrant so update_task()'s own
    # acquisition here doesn't deadlock.
    with _lock:
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
