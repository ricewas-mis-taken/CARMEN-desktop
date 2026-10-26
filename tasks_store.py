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
import sync_trigger
import paths

TASKS_PATH = paths.private_path("tasks.json")

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
    # "daily", "weekly_days" (targetMinutes each checked weekday) or one of
    # PERIOD_RECURRENCES (targetMinutes is one total for the whole week / two
    # weeks / month -- see "goal periods" below).
    "recurrence": "daily",
    "weekdays": [],  # WEEKDAY_CODES entries, only used for "weekly_days"
    # Minutes for particular weekdays of a "weekly_days" task ({"MO": 30, "WE": 60}); a ticked
    # weekday with no entry here uses targetMinutes. Empty = the same time every ticked day.
    "dayMinutes": {},
    # Length in days of a "custom_total" stretch, counted from periodAnchor.
    "periodDays": None,
    "periodAnchor": None,  # "YYYY-MM-DD" Monday two-week stretches count from ("biweekly_total")
    "lockMode": "soft",
    "processBlocklist": [],
    "domainWhitelist": [],
    # Quiet taskbar while this task runs (see taskbar_quiet.py).
    "hideTaskbarBadges": False,
    "stopTaskbarFlashing": False,
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
            {"date": task["createdAt"][:10], "minutes": task.get("targetMinutes", 0),
             **({"dayMinutes": dict(task["dayMinutes"])} if task.get("dayMinutes") else {})}
        ]
        _ensure_period_anchor(task)
        tasks = load_tasks(include_deleted=True)
        tasks.append(task)
        save_tasks(tasks)
        sync_trigger.note_change()
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
                new_days = data.get("dayMinutes")
                target_changed = new_target is not None and new_target != task.get("targetMinutes")
                days_changed = new_days is not None and new_days != (task.get("dayMinutes") or {})
                if target_changed or days_changed:
                    if new_target is None:
                        new_target = task.get("targetMinutes", 0)
                    if new_days is None:
                        new_days = task.get("dayMinutes") or {}
                    # Record when the target changed instead of overwriting it in
                    # place, so vacation_balance_minutes can look up whatever
                    # target was actually in effect on a given past day -- a
                    # goal-time change today must never retroactively change
                    # what counted as "surplus" on days before it.
                    history = list(task.get("targetMinutesHistory") or [])
                    today_key = date.today().isoformat()
                    entry = {"date": today_key, "minutes": new_target}
                    # Per-day times are only written when there are some, or when they
                    # were just removed (so the history says the weekdays are equal again).
                    if new_days or (history and history[-1].get("dayMinutes")):
                        entry["dayMinutes"] = dict(new_days)
                    if history and history[-1]["date"] == today_key:
                        history[-1] = entry
                    else:
                        history.append(entry)
                    task["targetMinutesHistory"] = history
                task.update(data)
                _ensure_period_anchor(task)
                task["updatedAt"] = datetime.now().isoformat()
                updated = task
                break
        if updated is not None:
            save_tasks(tasks)
            sync_trigger.note_change()
        return updated


def add_domains_to_whitelist(task_id, domains):
    """Merges `domains` into task_id's own saved domainWhitelist, case-
    insensitive deduped against what's already there -- the atomic version
    of what api_server.py's task_domain_whitelist_add route used to do
    itself as get_task() then update_task() with the merge done in plain
    Python in between. A red-team pass found that gap: two concurrent adds
    of different domains to the same task both read the same starting
    list, both wrote back their own merged version, and whichever
    update_task() call landed last silently discarded the other's domain --
    both requests reported success, but only one domain actually stuck.
    Doing the whole read-merge-write inside one _lock acquisition (this
    module's _lock is an RLock, so load_tasks()/save_tasks() below can
    safely nest inside it) closes that gap, the same way
    session_manager.add_domain_to_whitelist() already does for the
    active-session whitelist.

    Returns the updated task dict, or None if task_id doesn't exist."""
    with _lock:
        tasks = load_tasks(include_deleted=True)
        updated = None
        for task in tasks:
            if task["id"] == task_id:
                existing = list(task.get("domainWhitelist") or [])
                existing_lower = {d.lower() for d in existing}
                for domain in domains:
                    domain = domain.strip()
                    if domain.lower() not in existing_lower:
                        existing.append(domain)
                        existing_lower.add(domain.lower())
                task["domainWhitelist"] = existing
                task["updatedAt"] = datetime.now().isoformat()
                updated = task
                break
        if updated is not None:
            save_tasks(tasks)
            sync_trigger.note_change()
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
            sync_trigger.note_change()
        return found


# --- scheduling ---

def is_scheduled_on(task, day):
    """Whether `task` calls for work on date `day` at all -- "daily" always
    does; "weekly_days" only on the checked weekdays."""
    if task.get("recurrence") == "weekly_days":
        code = WEEKDAY_CODES[day.weekday()]
        return code in (task.get("weekdays") or [])
    return True  # daily, and every day counts toward a period goal


def _target_minutes_for_date(task, day):
    """The goal-time value that was actually in effect on `day`, from
    targetMinutesHistory (oldest first) -- not necessarily today's
    targetMinutes. A later goal-time change must not reach backward and
    change what counted as surplus/vacation on earlier days. A "weekly_days"
    task may give a weekday its own time (dayMinutes); that is kept in the same
    history entry, so it can't reach backward either."""
    history = task.get("targetMinutesHistory") or []
    if not history:
        return _minutes_for_weekday(task, task.get("dayMinutes"), task.get("targetMinutes", 0), day)
    day_key = day.isoformat()
    applicable = history[0]
    for entry in history:
        if entry["date"] <= day_key:
            applicable = entry
        else:
            break
    return _minutes_for_weekday(task, applicable.get("dayMinutes"), applicable["minutes"], day)


def _minutes_for_weekday(task, day_minutes, default, day):
    if task.get("recurrence") == "weekly_days" and isinstance(day_minutes, dict):
        own = day_minutes.get(WEEKDAY_CODES[day.weekday()])
        if isinstance(own, (int, float)) and not isinstance(own, bool) and own > 0:
            return own
    return default


def required_minutes_for_date(task, day):
    """Target minutes for `day` (as of whatever the goal time actually was
    on that day), after subtracting whatever vacation time was cashed in
    against that specific date. Never negative, and 0 on a day the task
    isn't scheduled at all (nothing to cash in against, either). For a period
    goal there is no per-day target: this is the whole period's requirement."""
    if is_period_task(task):
        return required_minutes_for_period(task, day)
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


def _worked_seconds_on_day(start_iso, end_iso, violation_log, day):
    """worked_seconds() restricted to calendar `day`: a session that crosses
    midnight is split between the days it actually spans instead of being
    credited wholly to the day it started on. Computed as a difference of two
    cumulative worked_seconds() values so a pause in effect at midnight is
    still respected."""
    day_start = datetime.combine(day, datetime.min.time())
    day_end = day_start + timedelta(days=1)
    start = datetime.fromisoformat(start_iso)
    end = datetime.fromisoformat(end_iso) if end_iso else datetime.now()
    if end <= day_start or start >= day_end:
        return 0
    upto_end = worked_seconds(start_iso, min(end, day_end).isoformat(), violation_log)
    before_day = worked_seconds(start_iso, day_start.isoformat(), violation_log) if start < day_start else 0
    return max(0, upto_end - before_day)


def _logged_seconds_by_day(entries, days):
    """{day: worked seconds} for just `days`, summed over history `entries` in one
    pass -- the same numbers logged_seconds_for_date() gives day by day."""
    totals = {}
    for entry in entries:
        start = datetime.fromisoformat(entry["startTime"]).date()
        end_iso = entry.get("endTime")
        end = datetime.fromisoformat(end_iso).date() if end_iso else date.today()
        day = start
        while day <= end:
            if day in days:
                totals[day] = totals.get(day, 0) + _worked_seconds_on_day(
                    entry["startTime"], end_iso, entry.get("violationLog"), day)
            day += timedelta(days=1)
    return totals


def logged_seconds_for_date(task, day, sessions, live_status=None):
    """Total seconds worked on `task` on date `day`, from finished
    session_history entries plus (if a live session for this task is running
    and overlaps `day`) the in-progress session's elapsed time so far.
    Sessions that cross midnight count toward each day they overlap."""
    total = 0
    for entry in _task_history_entries(task["id"], sessions):
        total += _worked_seconds_on_day(entry["startTime"], entry.get("endTime"), entry.get("violationLog"), day)

    if (
        live_status
        and live_status.get("isActive")
        and live_status.get("source") in ("task", "review")
        and live_status.get("eventId") == task["id"]
        and live_status.get("startTime")
    ):
        total += _worked_seconds_on_day(live_status["startTime"], None, live_status.get("violationLog"), day)

    return total


# --- goal periods (a total per week / 2 weeks / month, due by its end) ---
#
# "daily" and "weekly_days" goals are per day: each scheduled day has its own
# target. A period goal is one total for a stretch of days ("10 hours a week"):
# any day in the stretch counts toward it and it's only due on the stretch's last
# day. Weeks run Monday to Sunday (WEEKDAY_CODES); a two-week stretch counts
# from the Monday stored in periodAnchor; a month is the calendar month.

PERIOD_RECURRENCES = ("weekly_total", "biweekly_total", "monthly_total", "custom_total")

MIN_PERIOD_DAYS, MAX_PERIOD_DAYS = 2, 366

PERIOD_PHRASES = {
    "daily": "today",
    "weekly_days": "today",
    "weekly_total": "this week",
    "biweekly_total": "these 2 weeks",
    "monthly_total": "this month",
}


def is_period_task(task):
    return task.get("recurrence") in PERIOD_RECURRENCES


def monday_of(day):
    return day - timedelta(days=day.weekday())


def _anchor_for(task, day):
    """The Monday a two-week stretch is counted from. A task with no anchor
    (saved before it had one) counts from the Monday of its creation week."""
    raw = task.get("periodAnchor") or (task.get("createdAt") or "")[:10]
    try:
        return monday_of(date.fromisoformat(raw))
    except ValueError:
        return monday_of(day)


def period_bounds(task, day):
    """(first day, last day), both inclusive, of the stretch `day` falls in.
    A per-day goal's stretch is just that day."""
    kind = task.get("recurrence")
    if kind == "weekly_total":
        start = monday_of(day)
        return start, start + timedelta(days=6)
    if kind == "biweekly_total":
        anchor = _anchor_for(task, day)
        start = anchor + timedelta(days=((day - anchor).days // 14) * 14)
        return start, start + timedelta(days=13)
    if kind == "custom_total":
        length = custom_period_days(task)
        raw = task.get("periodAnchor") or (task.get("createdAt") or "")[:10]
        try:
            anchor = date.fromisoformat(raw[:10])
        except ValueError:
            anchor = day
        start = anchor + timedelta(days=((day - anchor).days // length) * length)
        return start, start + timedelta(days=length - 1)
    if kind == "monthly_total":
        start = day.replace(day=1)
        next_month = (start + timedelta(days=32)).replace(day=1)
        return start, next_month - timedelta(days=1)
    return day, day


def _ensure_period_anchor(task):
    """A two-week goal needs a Monday to count from; give it this week's the
    first time it's set to that, so the stretch you start in is the first one."""
    if task.get("recurrence") == "biweekly_total" and not task.get("periodAnchor"):
        task["periodAnchor"] = monday_of(date.today()).isoformat()
    # A custom stretch starts on the day it is set up, not on a Monday.
    if task.get("recurrence") == "custom_total" and not task.get("periodAnchor"):
        task["periodAnchor"] = date.today().isoformat()


def custom_period_days(task):
    """How long a "custom_total" stretch is, in days; a missing or silly value is a week."""
    days = task.get("periodDays")
    if isinstance(days, int) and not isinstance(days, bool) and MIN_PERIOD_DAYS <= days <= MAX_PERIOD_DAYS:
        return days
    return 7


def period_phrase(task):
    if task.get("recurrence") == "custom_total":
        return f"these {custom_period_days(task)} days"
    return PERIOD_PHRASES.get(task.get("recurrence"), "today")


def days_left_in_period(task, day):
    """Days still available to work, counting `day` itself."""
    return (period_bounds(task, day)[1] - day).days + 1


def _cashed_in_period(task, start, end):
    total = 0
    for key, minutes in (task.get("cashedInDates") or {}).items():
        try:
            when = date.fromisoformat(key)
        except ValueError:
            continue
        if start <= when <= end:
            total += minutes
    return total


def required_minutes_for_period(task, day, today=None):
    """The period's total target minus vacation cashed in against any day of it.
    The target is whatever was in effect on the period's last day (or today, for
    one still running), so a mid-period change to the target applies to the whole
    period it was made in, not just the days after it. Never negative."""
    if not is_period_task(task):
        return required_minutes_for_date(task, day)
    today = today or date.today()
    start, end = period_bounds(task, day)
    target = _target_minutes_for_date(task, min(end, today))
    return max(0, target - _cashed_in_period(task, start, end))


def logged_seconds_for_period(task, day, sessions, live_status=None):
    """Seconds worked on `task` across the stretch `day` falls in."""
    start, end = period_bounds(task, day)
    total = 0
    cursor = start
    while cursor <= end:
        total += logged_seconds_for_date(task, cursor, sessions, live_status=live_status)
        cursor += timedelta(days=1)
    return total


def _period_surplus(task, sessions, today):
    """Minutes worked beyond the target in every period that has already ended
    (the running one doesn't count: a period isn't banked until it's over)."""
    seen = set()
    earned = 0.0
    for entry in _task_history_entries(task["id"], sessions):
        first = datetime.fromisoformat(entry["startTime"]).date()
        last = datetime.fromisoformat(entry["endTime"]).date() if entry.get("endTime") else first
        for when in {first, last}:
            start, end = period_bounds(task, when)
            if end >= today or start in seen:
                continue
            seen.add(start)
            logged = logged_seconds_for_period(task, when, sessions) / 60
            earned += max(0, logged - required_minutes_for_period(task, when, today))
    return earned


def goal_snapshot(task, day, sessions, live_status=None):
    """What the Tasks card shows for `day`, whichever kind of goal this is: how
    much is required, how much is logged, and how much vacation was cashed in, all
    in minutes and all for the goal's own stretch (the day itself for a per-day
    goal, the week / 2 weeks / month for a period goal)."""
    start, end = period_bounds(task, day)
    if is_period_task(task):
        required = required_minutes_for_period(task, day)
        cashed = _cashed_in_period(task, start, end)
        logged = logged_seconds_for_period(task, day, sessions, live_status=live_status) / 60
    else:
        required = required_minutes_for_date(task, day)
        cashed = (task.get("cashedInDates") or {}).get(day.isoformat(), 0)
        logged = logged_seconds_for_date(task, day, sessions, live_status=live_status) / 60
    return {
        "isPeriod": is_period_task(task),
        "phrase": period_phrase(task),
        "start": start,
        "end": end,
        "daysLeft": days_left_in_period(task, day),
        "required": required,
        "cashed": cashed,
        "logged": logged,
    }


def vacation_balance_minutes(task, sessions, today=None):
    """Minutes of "vacation" this task has banked: the sum of every past
    scheduled day's surplus (minutes logged beyond that day's requirement,
    which already accounts for anything cashed in against it) minus every
    minute ever cashed in, regardless of which date it was spent against.
    Today itself never contributes surplus -- a day isn't "banked" until
    it's over, so same-day overwork can't be cashed in against itself."""
    today = today or date.today()
    cashed_in_dates = task.get("cashedInDates") or {}

    if is_period_task(task):
        return max(0.0, _period_surplus(task, sessions, today) - sum(cashed_in_dates.values()))

    total_earned = 0.0
    for day in start_days:
        logged_minutes = logged_by_day.get(day, 0) / 60
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
