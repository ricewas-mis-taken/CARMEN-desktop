"""The weekly report card, as plain data: how the score moved, where points came from and went, the pause
breakdown, and one suggestion. Weeks run Monday to Sunday. Past misses are history here, never debt."""
from datetime import date, timedelta

import pause_budget
import score_store

KIND_LABELS = {
    "goals": "Daily goals",
    "period": "Week and month goals",
    "review": "Reviews done",
    "overdue": "Reviews left overdue",
    "pauses": "Pauses to look at other things",
    "unblocks": "Unblocking",
}


def week_start(day):
    return day - timedelta(days=day.weekday())


def last_week(today=None):
    """Monday of the week that just finished."""
    return week_start((today or date.today())) - timedelta(days=7)


def build(start, sessions=None, data=None):
    data = data or score_store.load()
    end = start + timedelta(days=6)
    events = score_store.events_between(start, end, data)
    before = score_store.score_through(start - timedelta(days=1), data)
    after = score_store.score_through(end, data)
    kinds = {}
    for e in events:
        kinds[e["kind"]] = kinds.get(e["kind"], 0) + e["points"]
    rows = [{"kind": k, "label": KIND_LABELS.get(k, k), "points": v}
            for k, v in sorted(kinds.items(), key=lambda kv: kv[1])]
    pause = _pauses(start, end, sessions or [])
    overdue_days = sum(1 for e in events if e["kind"] == "overdue")
    reviews_done = [e for e in events if e["kind"] == "review"]
    late = sum(1 for e in reviews_done if e["points"] < 6)
    return {
        "start": start, "end": end, "before": before, "after": after, "change": after - before,
        "grade": score_store.grade(after), "rows": rows,
        "reviews_done": len(reviews_done), "reviews_late": late, "overdue_days": overdue_days,
        "pause": pause,
        "suggestion": suggestion(kinds, pause, overdue_days, late),
        "empty": not events,
    }


def _pauses(start, end, sessions):
    free = escape = over = 0
    for entry in sessions:
        try:
            day = date.fromisoformat(str(entry.get("startTime"))[:10])
        except ValueError:
            continue
        if not (start <= day <= end):
            continue
        s = pause_budget.summarize(entry)
        free += s["free_seconds"]
        escape += s["escape_seconds"]
        over += s["over_seconds"]
    return {"free_seconds": free, "escape_seconds": escape, "over_seconds": over}


def suggestion(kinds, pause, overdue_days, late):
    """One thing to change next week, the biggest leak first. Empty when nothing needs changing."""
    leaks = {k: v for k, v in kinds.items() if v < 0}
    if not leaks:
        return "Nothing leaked this week. Keep the same plan."
    worst = min(leaks, key=leaks.get)
    if worst == "overdue" or overdue_days:
        return "Reviews slipped. Do the overdue ones first thing on your lightest day, or set aside the ones you no longer need."
    if worst == "pauses":
        return "Pauses to look at other things ate your focus. Keep them inside the budget, or pick Stuck and unblock on purpose."
    if worst == "goals":
        return "Daily goals were the biggest loss. Lower one goal to something you will really do, or move it to another day."
    if worst == "period":
        return "A week or month goal ended short. Plan its hours into specific days at the start of the period."
    if worst == "unblocks":
        return "You went past the unblock budget. Write the plan before you unblock, and keep it short."
    return "Pick the one habit that lost the most points and change only that."
