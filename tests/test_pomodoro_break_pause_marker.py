"""Pausing and resuming *during a pomodoro break* must not make the break
count as worked time. The focus->break transition already logs a "pause"
marker (see session_manager._advance_pomodoro_locked); a manual pause/resume
inside the break used to log a further "resume" marker, which tasks_store.
worked_seconds replays as "working again" for the rest of the break."""
from datetime import datetime, timedelta

import session_manager
import tasks_store


def test_manual_pause_resume_in_break_does_not_count_break_as_worked(isolate_state, isolate_review_db):
    session_manager.start_pomodoro_session(25, 5, 2, "soft", [], [])
    now = datetime.now()
    session_manager._state["startTime"] = (now - timedelta(minutes=26)).isoformat()
    session_manager._state["endTime"] = (now - timedelta(minutes=1)).isoformat()
    status = session_manager.get_status()  # focus -> break, pause marker at the old endTime
    assert status["isBreak"]

    session_manager.pause_session()
    session_manager.resume_session()

    status = session_manager.get_status()
    assert status["isBreak"]
    later = (datetime.now() + timedelta(minutes=2)).isoformat()
    worked = tasks_store.worked_seconds(status["startTime"], later, status["violationLog"])
    assert worked == 25 * 60 or abs(worked - 25 * 60) <= 2, worked
