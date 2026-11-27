"""Saving a finished session as an attempt at a problem, after the fact.

A first attempt whose Add Problem form was never finished, or a plain focus session that was really a go at a
problem, sits in the session history as just time. This files it as an attempt at a problem (an existing one, or
a new one made on the spot), so the time, the schedule and the stats all count it, and marks the history entry
so it is not saved twice."""
import review_store
import session_history
import session_manager
import tasks_store

_NOT_PLAIN = ("task", "review", "calendar-event")


def linked(session):
    return session.get("linkedProblemId") is not None


def linkable(session):
    """True for a first attempt, or a plain focus session (no task, review or calendar event), not yet saved."""
    if not session or linked(session):
        return False
    if session_manager.is_first_attempt(session):
        return True
    return session.get("source") not in _NOT_PLAIN and not session.get("reviewProblemName")


def seconds_of(session):
    return max(0, int(tasks_store.worked_seconds(
        session["startTime"], session["endTime"], session.get("violationLog"))))


def link(session, problem_id, self_solved=True, shakiness=3):
    """Files the session as an attempt at this problem. Returns the updated problem, or None if it could not be
    (not linkable, or the problem is gone)."""
    if not linkable(session):
        return None
    before = review_store.get_problem(problem_id)
    if before is None:
        return None
    problem = review_store.record_first_attempt(
        problem_id, seconds_of(session), self_solved=self_solved, shakiness=shakiness)
    if problem is None:
        return None
    session_history.update_entry(session_history.entry_id(session), {
        "source": "review",
        "linkedProblemId": problem["id"],
        "reviewProblemId": problem["id"],
        "reviewProblemName": problem["name"],
        "reviewSubjectName": problem.get("subjectName"),
    })
    return problem
