"""The week ahead as plain data: what has to be reviewed on each day, how loaded each day is, and which
days are heavy. No Qt here so it can be tested and reused."""
from datetime import date, timedelta

import review_store
import tasks_store

REVIEW_WEIGHT = 8.0        # minutes a review is assumed to take when nothing is known about it yet
RECENCY = 0.6              # each older attempt counts this much of the one after it
PRIOR_WEIGHT = 1.5         # how many attempts' worth of "a typical review for you" a problem starts with
MIN_REVIEW, MAX_REVIEW = 2.0, 60.0


def typical_minutes(history):
    """What a review usually takes you, in minutes: the average over every review you have timed, each problem's
    own average first so a problem done often does not drown the rest. REVIEW_WEIGHT until there is any."""
    means = [_recent_mean(times) for times in history.values() if times]
    return sum(means) / len(means) if means else REVIEW_WEIGHT


def _recent_mean(attempts):
    """Minutes, newest attempt first, each older one counting RECENCY times as much as the next."""
    weights = [RECENCY ** i for i in range(len(attempts))]
    return sum(w * (a[0] if isinstance(a, tuple) else a) / 60 for w, a in zip(weights, attempts)) / sum(weights)


def expected_minutes(problem_id, history, typical=None):
    """How long this review should take. The problem's own recent attempts (newest count most) are blended with
    your typical review, so a problem timed once is not trusted as much as one timed five times; when its last
    attempt was shaky it is expected to run longer, and when it was solid, shorter."""
    typical = typical if typical is not None else typical_minutes(history)
    attempts = history.get(problem_id) or []
    if not attempts:
        return round(min(MAX_REVIEW, max(MIN_REVIEW, typical)), 1)
    weight = sum(RECENCY ** i for i in range(len(attempts)))
    own = _recent_mean(attempts)
    blended = (weight * own + PRIOR_WEIGHT * typical) / (weight + PRIOR_WEIGHT)
    shakiness = attempts[0][1]
    if shakiness:
        blended *= 1 + 0.06 * (shakiness - 3)              # 5/5 shaky: 12% longer, 1/5: 12% shorter
    return round(min(MAX_REVIEW, max(MIN_REVIEW, blended)), 1)
HEAVY_FACTOR = 1.25        # a day this much above the week's average load is heavy
HEAVY_MINIMUM = 40.0       # ...but never call a light day heavy


def build(today=None, days=7, tasks=None, problems=None, history=None):
    """[{date, reviews, review_minutes, goal_minutes, load, heavy}] for `days` days from today.
    Reviews already overdue are shown on today. Period goals (week, month) are suggestions, so they
    add nothing to a day's load."""
    today = today or date.today()
    tasks = tasks if tasks is not None else tasks_store.load_tasks()
    last = today + timedelta(days=days - 1)
    problems = problems if problems is not None else review_store.problems_between(today, last)
    history = history if history is not None else review_store.all_session_history()
    typical = typical_minutes(history)
    plan = []
    for i in range(days):
        day = today + timedelta(days=i)
        reviews = [p for p in problems if _due(p, today) == day]
        goal = sum(tasks_store.required_minutes_for_date(t, day) for t in tasks
                   if not tasks_store.is_period_task(t) and tasks_store.is_scheduled_on(t, day))
        review_minutes = sum(expected_minutes(p["id"], history, typical) for p in reviews)
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
