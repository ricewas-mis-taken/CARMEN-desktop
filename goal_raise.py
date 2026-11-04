"""When someone keeps going well past a daily goal, the goal is too low. This finds those goals and says
exactly what to raise them to. It only looks at per-day goals (a week or month goal has no single day to
compare against)."""
from datetime import date, timedelta

LOOK_BACK_DAYS = 7
MIN_DAYS = 4               # scheduled days with data needed before saying anything
OVER_BY = 1.25             # a day counts as "well over" at 125% of the goal
NEEDED_SHARE = 0.7         # ...and most of the scheduled days must be like that
MAX_RAISE = 2.0            # never suggest more than double in one step
STEP = 5                   # minutes


def suggested_target(task, today, sessions, tasks_store):
    """New minutes per day for `task`, or None when the goal is fine where it is."""
    if tasks_store.is_period_task(task):
        return None
    rows = []
    for back in range(1, LOOK_BACK_DAYS + 1):
        day = today - timedelta(days=back)
        required = tasks_store.required_minutes_for_date(task, day)
        if required <= 0:
            continue
        rows.append((required, tasks_store.logged_seconds_for_date(task, day, sessions) / 60))
    if len(rows) < MIN_DAYS:
        return None
    over = [(r, l) for r, l in rows if l >= r * OVER_BY]
    if len(over) < len(rows) * NEEDED_SHARE:
        return None
    current = max(r for r, _ in rows)
    typical = sorted(l for _, l in over)[len(over) // 2]           # the middle of the days that went over
    new = int(min(typical, current * MAX_RAISE) // STEP * STEP)
    return new if new > current else None


def suggestions(tasks, today, sessions, tasks_store):
    """[(task, new_minutes)] for every goal that should go up."""
    found = []
    for task in tasks:
        new = suggested_target(task, today, sessions, tasks_store)
        if new:
            found.append((task, new))
    return found
