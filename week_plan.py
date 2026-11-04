"""The week ahead as plain data: what has to be reviewed on each day, how loaded each day is, and which
days are heavy. No Qt here so it can be tested and reused."""
from datetime import date, timedelta

import review_store
import tasks_store

REVIEW_WEIGHT = 8.0        # minutes of work a review is assumed to take
HEAVY_FACTOR = 1.35        # a day this much above the week's average load is heavy
HEAVY_MINIMUM = 40.0       # ...but never call a light day heavy


def build(today=None, days=7, tasks=None, problems=None):
    """[{date, reviews, review_minutes, goal_minutes, load, heavy}] for `days` days from today.
    Reviews already overdue are shown on today. Period goals (week, month) are suggestions, so they
    add nothing to a day's load."""
    today = today or date.today()
    tasks = tasks if tasks is not None else tasks_store.load_tasks()
    last = today + timedelta(days=days - 1)
    problems = problems if problems is not None else review_store.problems_between(today, last)
    plan = []
    for i in range(days):
        day = today + timedelta(days=i)
        reviews = [p for p in problems if _due(p, today) == day]
        goal = sum(tasks_store.required_minutes_for_date(t, day) for t in tasks
                   if not tasks_store.is_period_task(t) and tasks_store.is_scheduled_on(t, day))
        review_minutes = len(reviews) * REVIEW_WEIGHT
        plan.append({"date": day, "reviews": reviews, "review_minutes": review_minutes,
                     "goal_minutes": goal, "load": goal + review_minutes, "heavy": False})
    average = sum(d["load"] for d in plan) / len(plan) if plan else 0
    for d in plan:
        d["heavy"] = d["load"] >= HEAVY_MINIMUM and d["load"] > average * HEAVY_FACTOR
    return plan


def _due(problem, today):
    due = date.fromisoformat(str(problem["nextReviewDate"])[:10])
    return max(due, today)


def lightest_day(plan, after=None):
    """The least loaded day, for moving a review somewhere easier (ties go to the earlier day)."""
    options = [d for d in plan if after is None or d["date"] >= after]
    return min(options, key=lambda d: (d["load"], d["date"]))["date"] if options else None
