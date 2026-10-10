"""A paused session can wait while a different one runs. Only one runs at a
time; the paused one neither counts time nor enforces anything, and comes
back (still paused) when the running one ends."""
import json
from datetime import datetime, timedelta

import pytest

import session_history
import session_manager as sm
import tasks_store

pytestmark = pytest.mark.usefixtures("isolate_state")


def _start(title, blocklist, minutes=30, **kw):
    return sm.start_session(minutes, "hard", blocklist, [f"{title}.com"], source="task",
                            event_id=title, event_title=title, **kw)


def _paused_a():
    _start("A", ["a.exe"])
    sm.pause_session()


def test_starting_over_a_paused_session_parks_it_instead_of_ending_it():
    _paused_a()
    status = _start("B", ["b.exe"])
    assert status["eventTitle"] == "B" and not status["isPaused"]
    assert status["processBlocklist"] == ["b.exe"]
    assert status["parkedSession"]["eventTitle"] == "A"
    assert session_history.load_all() == []  # A is not finished, so not filed


def test_the_parked_session_enforces_nothing_while_the_other_runs():
    _paused_a()
    assert sm.is_blocked("a.exe")  # a paused session still enforces by itself
    _start("B", ["b.exe"])
    assert not sm.is_blocked("a.exe") and sm.is_blocked("b.exe")


def test_when_the_running_session_ends_the_parked_one_returns_paused_with_its_own_rules():
    _paused_a()
    frozen = sm.get_status()["secondsRemaining"]
    _start("B", ["b.exe"])
    sm.end_session()
    status = sm.get_status()
    assert status["isActive"] and status["isPaused"]
    assert status["eventTitle"] == "A" and status["processBlocklist"] == ["a.exe"]
    assert status["secondsRemaining"] == frozen
    assert status["parkedSession"] is None
    assert sm.is_blocked("a.exe") and not sm.is_blocked("b.exe")
    assert [h["eventTitle"] for h in session_history.load_all()] == ["B"]


def test_parked_session_returns_when_the_running_one_runs_out_its_clock():
    _paused_a()
    _start("B", ["b.exe"], minutes=1)
    sm._state["endTime"] = (datetime.now() - timedelta(seconds=1)).isoformat()
    status = sm.get_status()
    assert status["eventTitle"] == "A" and status["isPaused"]


def test_parked_time_is_never_counted_as_worked():
    _paused_a()
    # pretend A was paused for an hour while B ran
    for entry in sm._state["violationLog"]:
        entry["timestamp"] = (datetime.now() - timedelta(hours=1)).isoformat()
    sm._state["startTime"] = (datetime.now() - timedelta(hours=1, minutes=10)).isoformat()
    _start("B", ["b.exe"])
    sm.end_session()
    sm.resume_session()
    status = sm.get_status()
    worked = tasks_store.worked_seconds(status["startTime"], None, status["violationLog"])
    assert worked < 15 * 60  # the 10 minutes before the pause, not the hour away


def test_resume_and_pause_only_ever_touch_the_running_session():
    _paused_a()
    _start("B", ["b.exe"])
    sm.pause_session()
    sm.resume_session()
    status = sm.get_status()
    assert status["eventTitle"] == "B" and not status["isPaused"]
    assert status["parkedSession"]["eventTitle"] == "A"


def test_starting_over_a_running_session_still_replaces_it():
    _start("A", ["a.exe"])
    _start("B", ["b.exe"])
    assert sm.get_status()["parkedSession"] is None
    assert [h["endType"] for h in session_history.load_all()] == ["superseded"]


def test_any_number_of_paused_sessions_can_wait_behind_the_running_one():
    for n in range(100):
        _start(f"T{n}", [f"t{n}.exe"])
        sm.pause_session()
    status = _start("T100", ["t100.exe"])
    assert status["eventTitle"] == "T100" and not status["isPaused"]
    assert len(status["parkedSessions"]) == 100
    assert session_history.load_all() == []  # nothing was thrown away
    assert [sm.is_blocked(f"t{n}.exe") for n in range(100)] == [False] * 100 and sm.is_blocked("t100.exe")


def test_waiting_sessions_come_back_most_recent_first_each_as_the_next_one_ends():
    for name in ("A", "B", "C"):
        _start(name, [f"{name.lower()}.exe"])
        sm.pause_session()
    _start("D", ["d.exe"])
    assert [p["eventTitle"] for p in sm.get_status()["parkedSessions"]] == ["C", "B", "A"]  # next first
    seen = []
    for _ in range(3):
        sm.end_session()
        status = sm.get_status()
        assert status["isPaused"]
        seen.append(status["eventTitle"])
        sm.resume_session()
    assert seen == ["C", "B", "A"]


def test_starting_over_a_paused_one_again_adds_another_waiting_session():
    _paused_a()
    _start("B", ["b.exe"])
    sm.pause_session()
    _start("C", ["c.exe"])
    status = sm.get_status()
    assert status["eventTitle"] == "C"
    assert [p["eventTitle"] for p in status["parkedSessions"]] == ["B", "A"]
    assert session_history.load_all() == []


def test_swap_can_pick_any_waiting_session_by_id():
    for name in ("A", "B", "C"):
        _start(name, [])
        sm.pause_session()
    _start("D", [])
    oldest = sm.get_status()["parkedSessions"][-1]
    assert oldest["eventTitle"] == "A"
    status = sm.swap_with_parked(oldest["parkId"])
    assert status["eventTitle"] == "A" and not status["isPaused"]
    assert [p["eventTitle"] for p in status["parkedSessions"]] == ["D", "C", "B"]
    assert sm.swap_with_parked("no-such-id")["eventTitle"] == "A"


def test_ending_one_chosen_waiting_session_leaves_the_others_waiting():
    for name in ("A", "B", "C"):
        _start(name, [])
        sm.pause_session()
    _start("D", [])
    middle = [p for p in sm.get_status()["parkedSessions"] if p["eventTitle"] == "B"][0]
    assert sm.end_parked_session(park_id=middle["parkId"])["eventTitle"] == "B"
    assert [p["eventTitle"] for p in sm.get_status()["parkedSessions"]] == ["C", "A"]
    assert [h["eventTitle"] for h in session_history.load_all()] == ["B"]


def test_ending_the_parked_session_files_it_and_leaves_the_running_one_alone():
    _paused_a()
    _start("B", ["b.exe"])
    result = sm.end_parked_session()
    assert result["eventTitle"] == "A"
    status = sm.get_status()
    assert status["eventTitle"] == "B" and status["isActive"] and not status["isPaused"]
    assert status["parkedSession"] is None
    assert [h["eventTitle"] for h in session_history.load_all()] == ["A"]
    assert sm.end_parked_session() is None


def test_swap_puts_the_parked_one_back_to_work_and_parks_the_other():
    _paused_a()
    _start("B", ["b.exe"])
    status = sm.swap_with_parked()
    assert status["eventTitle"] == "A" and not status["isPaused"]
    assert status["parkedSession"]["eventTitle"] == "B"
    assert sm.is_blocked("a.exe") and not sm.is_blocked("b.exe")
    again = sm.swap_with_parked()
    assert again["eventTitle"] == "B" and again["parkedSession"]["eventTitle"] == "A"
    kinds = [e["kind"] for e in again["violationLog"] if e["kind"] in ("pause", "resume")]
    assert kinds == ["pause", "resume", "pause", "resume"][: len(kinds)]  # always alternating, so no time leaks


def test_swap_without_a_parked_session_changes_nothing():
    _start("A", ["a.exe"])
    status = sm.swap_with_parked()
    assert status["eventTitle"] == "A" and not status["isPaused"]


def test_a_parked_session_survives_an_app_restart():
    _paused_a()
    _start("B", ["b.exe"])
    saved = json.load(open(sm.STATE_PATH, encoding="utf-8"))
    assert [p["eventTitle"] for p in saved["parkedSessions"]] == ["A"]
    sm._state["parkedSessions"] = []
    sm._state.update(saved)
    sm.end_session()
    assert sm.get_status()["eventTitle"] == "A"


def test_a_parked_pomodoro_keeps_its_phase_and_cycle():
    sm.start_pomodoro_session(25, 5, 4, "hard", ["a.exe"], [], source="task", event_id="A", event_title="A")
    sm.pause_session()
    _start("B", ["b.exe"])
    assert sm.get_status()["parkedSession"]["pomodoro"]["currentCycle"] == 1
    sm.end_session()
    status = sm.get_status()
    assert status["pomodoro"]["totalCycles"] == 4 and status["isPaused"]


def test_open_violation_of_the_running_session_is_untouched_by_ending_the_parked_one():
    _paused_a()
    _start("B", ["b.exe"])
    sm.record_violation("b.exe")
    sm.end_parked_session()
    entry = sm.get_status()["violationLog"][-1]
    assert entry["process"] == "b.exe" and entry["resolvedAt"] is None  # still open
    sm.record_acceptable("notepad.exe")
    resolved = sm.get_status()["violationLog"][-1]
    assert resolved["process"] == "b.exe" and resolved["resolvedAt"] is not None
