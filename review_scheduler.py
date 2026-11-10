"""Spaced-repetition interval math for the Review tab (qt_ui/review_tab.py,
review_store.py). Deliberately free of sqlite/Flask/PySide6 imports so it can
be unit-tested in isolation, same separation calendar_recurrence.py keeps
from calendar_store.py.

Higher star rating (a problem the user found easy) stretches the interval out
via STAR_MULTIPLIERS < 1.0, so easy problems get reviewed less often than hard
ones at the same schedule stage.
"""
from datetime import date, timedelta

BASE_INTERVALS_DAYS = [1, 4, 10, 21, 45, 90]
STAR_MULTIPLIERS = {1: 1.0, 2: 0.85, 3: 0.70, 4: 0.55, 5: 0.40}

# Shakiness 1 = solid/confident → longer interval; 5 = very shaky → shorter interval.
SHAKINESS_MULTIPLIERS = {1: 1.6, 2: 1.3, 3: 1.0, 4: 0.70, 5: 0.45}


def today():
    return date.today()


def compute_next_interval(schedule_stage: int, stars: int) -> int:
    stage = min(schedule_stage, len(BASE_INTERVALS_DAYS) - 1)
    base = BASE_INTERVALS_DAYS[stage]
    return max(1, round(base * STAR_MULTIPLIERS[stars]))


def schedule_new_problem(stars: int) -> dict:
    interval = compute_next_interval(0, stars)
    return {"schedule_stage": 0, "next_review_date": today() + timedelta(days=interval)}


def schedule_after_review(schedule_stage: int, stars: int, shakiness: int = 3) -> dict:
    """Advances the schedule stage and applies both star and shakiness multipliers."""
    new_stage = min(schedule_stage + 1, len(BASE_INTERVALS_DAYS) - 1)
    base_interval = compute_next_interval(new_stage, stars)
    shak_mult = SHAKINESS_MULTIPLIERS.get(shakiness, 1.0)
    interval = max(1, round(base_interval * shak_mult))
    return {"schedule_stage": new_stage, "next_review_date": today() + timedelta(days=interval)}


def schedule_checked_answer(schedule_stage: int, stars: int) -> dict:
    """Full reset to stage 0, regardless of the stage this problem had
    climbed to -- checking the answer means it wasn't actually recalled, so
    a stage the schedule had "earned" (e.g. a 21-day interval reached after
    solving it three times running) no longer reflects what's actually
    remembered. The whole interval ladder re-climbs from scratch (1, 4, 10,
    21, 45, 90 days, scaled by star/shakiness) instead of resuming from
    wherever it left off. next_review_date is stage 0's own interval --
    always 1 day, since round(1 * a multiplier <= 1.0) can't exceed 1 --
    rather than a hardcoded "tomorrow" that happened to coincidentally
    match it."""
    return {"schedule_stage": 0, "next_review_date": today() + timedelta(days=compute_next_interval(0, stars))}
