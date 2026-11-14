"""The review wheel: a weighted draw that picks which review to do, and the commitment that follows.

Every review that is due gets a slice. The slice grows with how much the review is costing you:
late ones, ones you pushed back, and ones that already lost points. Whatever the wheel lands on
is "fated": it is the only review you can start until you finish it. Starting another one loses
BREAK_COST points, then the wheel is yours again."""
import random
import uuid
from datetime import date, datetime

import review_store
import score_store

BREAK_COST = 3
MAX_LATE_DAYS = 10

# fate: the problem id the wheel chose; started: whether that review has been begun.
_state = {"fate": None, "started": False, "since": None}


def _day(text):
    try:
        return date.fromisoformat(str(text)[:10])
    except ValueError:
        return None


def points_lost(problem, today=None):
    """Points this review has already cost: days pushed later plus days overdue, 3 each, a week at most each."""
    today = today or date.today()
    due = _day(problem.get("nextReviewDate"))
    original = _day(problem.get("originalDueDate")) or due
    if due is None:
        return 0
    moved = min(max(0, (due - original).days), review_store.MAX_MOVE_DAYS)
    overdue = min(max(0, (today - due).days), review_store.MAX_MOVE_DAYS)
    return (moved + overdue) * 3


def weight(problem, today=None):
    """How big the slice is. Always above zero, so every due review has a chance."""
    today = today or date.today()
    due = _day(problem.get("nextReviewDate"))
    original = _day(problem.get("originalDueDate")) or due
    late = max(0, (today - due).days) if due else 0
    moved = bool(due and original and due > original)
    return 1.0 + min(late, MAX_LATE_DAYS) + (2.0 if moved else 0.0) + points_lost(problem, today) / 3.0


def entries(topic_id, today=None):
    """[(problem, weight)] for every review due on or before today."""
    today = today or date.today()
    out = []
    for problem in review_store.list_problems(topic_id, due_only=True):
        if problem.get("outOfRotation"):
            continue
        out.append((problem, weight(problem, today)))
    return out


def spin(weights, rng=random):
    """(index, position): the winning slice, and how far through it (0 to 1) the pointer should stop."""
    index = rng.choices(range(len(weights)), weights=weights, k=1)[0]
    return index, rng.uniform(0.12, 0.88)


def fate():
    return _state["fate"]


def set_fate(problem_id):
    _state["fate"] = problem_id
    _state["started"] = False
    _state["since"] = datetime.now().isoformat()


def current():
    """The fated problem, or None. A fate is dropped here once its review was finished by any route
    (the review tab, a task card, the mini timer, another computer), deleted, or set aside."""
    fated = _state["fate"]
    if fated is None:
        return None
    problem = review_store.get_problem(fated)
    since = _state["since"] or ""
    done = bool(problem and since and (problem.get("lastReviewedAt") or "") > since)
    if problem is None or problem.get("outOfRotation") or done:
        clear()
        return None
    return problem


def mark_started(problem_id):
    if _state["fate"] == problem_id:
        _state["started"] = True


def review_done():
    """A review finished: if it was the fated one, the wheel is free again."""
    if _state["fate"] is not None and _state["started"]:
        clear()


def clear():
    _state["fate"] = None
    _state["started"] = False
    _state["since"] = None


def break_fate(today=None):
    """Starting something else than the fated review: costs points, then the wheel is free."""
    problem = review_store.get_problem(_state["fate"]) if _state["fate"] else None
    name = problem["name"] if problem else "the wheel's pick"
    score_store.add_event(f"fate:{uuid.uuid4()}", today or date.today(), "fate", -BREAK_COST,
                          f"Skipped the wheel's pick: {name}")
    clear()
