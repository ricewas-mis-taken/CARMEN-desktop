"""state_events.py -- the change counter behind GET /events/wait."""
import threading
import time

import pytest

import session_manager
import state_events


@pytest.fixture(autouse=True)
def fresh_counter(monkeypatch):
    monkeypatch.setattr(state_events, "_version", 0)
    monkeypatch.setattr(state_events, "_waiters", 0)


def test_no_since_returns_the_baseline_immediately():
    assert state_events.wait_for_change(None, 5) == (0, False)


def test_a_stale_since_returns_immediately_as_changed():
    state_events.bump()
    assert state_events.wait_for_change(0, 5) == (1, True)


def test_times_out_unchanged():
    start = time.time()
    assert state_events.wait_for_change(0, 0.2) == (0, False)
    assert time.time() - start >= 0.15


def test_a_bump_wakes_a_waiter_right_away():
    result = {}

    def wait():
        result["r"] = state_events.wait_for_change(0, 5)

    t = threading.Thread(target=wait)
    t.start()
    time.sleep(0.1)
    start = time.time()
    state_events.bump()
    t.join(2)
    assert result["r"] == (1, True)
    assert time.time() - start < 1


def test_waiters_are_capped(monkeypatch):
    monkeypatch.setattr(state_events, "MAX_WAITERS", 1)
    threads = [threading.Thread(target=state_events.wait_for_change, args=(0, 0.6)) for _ in range(1)]
    for t in threads:
        t.start()
    time.sleep(0.1)
    start = time.time()
    assert state_events.wait_for_change(0, 5) == (0, False)
    assert time.time() - start < 0.3
    for t in threads:
        t.join(2)


def test_starting_and_ending_a_session_bump_the_counter(isolate_state):
    before = state_events.current()
    session_manager.start_session(25, "soft", [], [])
    assert state_events.current() > before
    mid = state_events.current()
    session_manager.pause_session()
    session_manager.resume_session()
    assert state_events.current() > mid
    after_resume = state_events.current()
    session_manager.end_session()
    assert state_events.current() > after_resume
