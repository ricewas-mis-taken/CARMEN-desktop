"""Pomodoro session state machine: start_pomodoro_session() plus the
auto-advance logic in _advance_pomodoro_locked(), triggered the same way a
plain session's natural end already is -- get_status() noticing endTime has
passed. Expiry is simulated by rewinding session_manager's own internal
endTime rather than sleeping real minutes."""
from datetime import datetime, timedelta

import session_manager
import tasks_store


def _expire_now():
    session_manager._state["endTime"] = (datetime.now() - timedelta(seconds=1)).isoformat()


def test_start_pomodoro_session_begins_on_focus_phase(isolate_state):
    session_manager.start_pomodoro_session(25, 5, 4, "soft", ["bad.exe"], ["good.com"])
    status = session_manager.get_status()
    assert status["isActive"]
    assert not status["isBreak"]
    assert status["pomodoro"] == {
        "focusMinutes": 25, "breakMinutes": 5, "totalCycles": 4,
        "currentCycle": 1, "phase": "focus",
    }
    assert status["processBlocklist"] == ["bad.exe"]


def test_focus_expiry_switches_to_break_without_ending_session(isolate_state):
    session_manager.start_pomodoro_session(25, 5, 4, "soft", ["bad.exe"], [])
    _expire_now()

    status = session_manager.get_status()
    assert status["isActive"]
    assert status["isBreak"]
    assert status["pomodoro"]["phase"] == "break"
    assert status["pomodoro"]["currentCycle"] == 1
    assert status["secondsRemaining"] > 0

    pending = session_manager.pop_pending_phase_change()
    assert pending == {"phase": "break", "cycle": 1, "totalCycles": 4}
    assert session_manager.pop_pending_natural_end() is None


def test_break_expiry_advances_cycle_and_resumes_focus(isolate_state):
    session_manager.start_pomodoro_session(25, 5, 2, "soft", [], [])
    _expire_now()
    session_manager.get_status()  # focus -> break
    session_manager.pop_pending_phase_change()

    _expire_now()
    status = session_manager.get_status()
    assert status["isActive"]
    assert not status["isBreak"]
    assert status["pomodoro"]["phase"] == "focus"
    assert status["pomodoro"]["currentCycle"] == 2

    pending = session_manager.pop_pending_phase_change()
    assert pending == {"phase": "focus", "cycle": 2, "totalCycles": 2}


def test_last_break_expiry_ends_the_whole_session(isolate_state):
    session_manager.start_pomodoro_session(25, 5, 1, "soft", [], [])
    _expire_now()
    session_manager.get_status()  # focus -> break (cycle 1 of 1)
    session_manager.pop_pending_phase_change()

    _expire_now()
    status = session_manager.get_status()
    assert not status["isActive"]
    assert status["pomodoro"] is None
    assert not status["isBreak"]

    summary = session_manager.pop_pending_natural_end()
    assert summary is not None
    assert summary["endType"] == "natural"


def test_manual_end_mid_pomodoro_clears_pomodoro_state(isolate_state):
    session_manager.start_pomodoro_session(25, 5, 4, "soft", [], [])
    session_manager.end_session(end_type="manual")
    status = session_manager.get_status()
    assert status["pomodoro"] is None
    assert not status["isBreak"]


def test_worked_seconds_excludes_break_time(isolate_state):
    """A break isn't work -- _advance_pomodoro_locked must append the same
    pause/resume violationLog markers pause_session()/resume_session() do,
    so tasks_store.worked_seconds() (and the extension's identical replay)
    don't credit break minutes toward the task's worked time."""
    session_manager.start_pomodoro_session(25, 5, 2, "soft", [], [])
    # Simulate 25 real minutes of focus having actually elapsed.
    session_manager._state["startTime"] = (datetime.now() - timedelta(minutes=25)).isoformat()
    _expire_now()
    status = session_manager.get_status()  # focus -> break, pause marker at ~now
    session_manager.pop_pending_phase_change()
    assert status["isBreak"]

    # Checked mid-break (no resume yet) -- worked time must stop counting at
    # the pause marker, not keep accruing into the break itself.
    worked = tasks_store.worked_seconds(status["startTime"], None, status["violationLog"])
    assert 24 * 60 <= worked <= 25 * 60 + 5


def test_plain_start_session_clears_any_leftover_pomodoro_state(isolate_state):
    session_manager.start_pomodoro_session(25, 5, 4, "soft", [], [])
    _expire_now()
    session_manager.get_status()  # focus -> break

    session_manager.start_session(10, "soft", [], [])
    status = session_manager.get_status()
    assert status["pomodoro"] is None
    assert not status["isBreak"]
