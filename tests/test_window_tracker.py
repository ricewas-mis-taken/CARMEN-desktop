"""Regression test for the hard-lock redirect storm: an offending process
that never actually leaves the foreground (observed with Discord -- a
stray popup/overlay window regrabs focus right after hard_lock_redirect()
minimizes it) used to retrigger enforcer.hard_lock_redirect() on every
single poll tick, each call re-issuing SW_MINIMIZE/SetForegroundWindow
(the app "flashing") and spawning another lock overlay (windows piling up).
window_tracker.HARD_REDIRECT_COOLDOWN_SECONDS throttles that."""
import threading
import time

import pytest

import enforcer
import session_manager
import window_tracker


@pytest.fixture
def fast_polling(monkeypatch):
    """Speeds up the loop's own pacing without touching the cooldown, so a
    handful of ticks happen within a short, deterministic test sleep."""
    monkeypatch.setattr(window_tracker, "POLL_INTERVAL_SECONDS", 0.01)
    monkeypatch.setattr(window_tracker, "HARD_REDIRECT_COOLDOWN_SECONDS", 0.2)
    yield


def test_stuck_foreground_process_does_not_spam_redirects(isolate_state, fast_polling, monkeypatch):
    session_manager.start_session(25, "hard", ["discord.exe"], [])

    monkeypatch.setattr(
        window_tracker, "get_active_window",
        lambda: {"title": "Discord", "process_name": "discord.exe", "pid": 4242, "hwnd": 111},
    )

    redirect_calls = []
    monkeypatch.setattr("enforcer.hard_lock_redirect", lambda name: redirect_calls.append(name))
    monkeypatch.setattr(enforcer, "sweep_minimize_blocked_windows", lambda: None)

    stop_event = threading.Event()
    thread = threading.Thread(target=window_tracker.run_polling_loop, args=(stop_event,), daemon=True)
    thread.start()
    try:
        # ~50 poll ticks' worth of wall time at 0.01s/tick -- if the old
        # behavior (reset-and-retrigger every tick) were still in place,
        # this would rack up dozens of calls instead of a couple.
        time.sleep(0.5)
    finally:
        stop_event.set()
        thread.join(timeout=2)

    assert 1 <= len(redirect_calls) <= 5
    assert all(name == "discord.exe" for name in redirect_calls)


def test_process_that_actually_leaves_still_gets_redirected_again(isolate_state, fast_polling, monkeypatch):
    """Sanity check the cooldown doesn't just permanently silence a process:
    once cooldown elapses, a still-offending (or newly-offending) process is
    redirected again."""
    session_manager.start_session(25, "hard", ["discord.exe"], [])
    monkeypatch.setattr(
        window_tracker, "get_active_window",
        lambda: {"title": "Discord", "process_name": "discord.exe", "pid": 4242, "hwnd": 111},
    )
    redirect_calls = []
    monkeypatch.setattr("enforcer.hard_lock_redirect", lambda name: redirect_calls.append(name))
    monkeypatch.setattr(enforcer, "sweep_minimize_blocked_windows", lambda: None)

    stop_event = threading.Event()
    thread = threading.Thread(target=window_tracker.run_polling_loop, args=(stop_event,), daemon=True)
    thread.start()
    try:
        time.sleep(0.5)
    finally:
        stop_event.set()
        thread.join(timeout=2)

    # Across ~2.5 cooldown windows (0.5s / 0.2s), it should have redirected
    # more than once -- proving the cooldown resets rather than sticking
    # forever -- while still nowhere near one-per-tick.
    assert len(redirect_calls) >= 2


def test_reopened_window_is_redirected_immediately_not_after_cooldown(isolate_state, fast_polling, monkeypatch):
    """Regression test: a blocklisted app that gets minimized, then reopened
    by the user as a brand-new window (different hwnd), used to be treated
    as "recently redirected" and left alone for the rest of
    HARD_REDIRECT_COOLDOWN_SECONDS just because the process name matched --
    letting it be used freely for that whole window. The cooldown should
    only suppress redirects for the exact same still-foreground window
    (the actual stuck-popup case it exists for), not a genuinely new one."""
    session_manager.start_session(25, "hard", ["discord.exe"], [])
    monkeypatch.setattr(window_tracker, "HARD_REDIRECT_COOLDOWN_SECONDS", 5.0)

    current_hwnd = {"value": 111}
    monkeypatch.setattr(
        window_tracker, "get_active_window",
        lambda: {"title": "Discord", "process_name": "discord.exe", "pid": 4242, "hwnd": current_hwnd["value"]},
    )

    redirect_calls = []
    monkeypatch.setattr("enforcer.hard_lock_redirect", lambda name: redirect_calls.append(name))
    monkeypatch.setattr(enforcer, "sweep_minimize_blocked_windows", lambda: None)

    stop_event = threading.Event()
    thread = threading.Thread(target=window_tracker.run_polling_loop, args=(stop_event,), daemon=True)
    thread.start()
    try:
        time.sleep(0.1)
        assert len(redirect_calls) >= 1
        # Simulate the user reopening the app: a fresh window, well within
        # the (5s) cooldown window of the first redirect.
        current_hwnd["value"] = 222
        time.sleep(0.1)
    finally:
        stop_event.set()
        thread.join(timeout=2)

    assert len(redirect_calls) >= 2


def test_hard_lock_sweeps_background_windows_every_tick(isolate_state, fast_polling, monkeypatch):
    """Regression test: enforcement used to only ever look at the current
    foreground window, so a blocklisted app already open in the background
    (e.g. it was open before the session started, or it isn't the window
    the user happens to be focused on this particular tick) was never
    caught at all. Hard lock should sweep every visible window each tick,
    not just the focused one."""
    session_manager.start_session(25, "hard", ["discord.exe"], [])
    monkeypatch.setattr(
        window_tracker, "get_active_window",
        lambda: {"title": "Notepad", "process_name": "notepad.exe", "pid": 1, "hwnd": 999},
    )
    monkeypatch.setattr("enforcer.hard_lock_redirect", lambda name: None)

    sweep_calls = []
    monkeypatch.setattr(enforcer, "sweep_minimize_blocked_windows", lambda: sweep_calls.append(True))

    stop_event = threading.Event()
    thread = threading.Thread(target=window_tracker.run_polling_loop, args=(stop_event,), daemon=True)
    thread.start()
    try:
        time.sleep(0.1)
    finally:
        stop_event.set()
        thread.join(timeout=2)

    assert len(sweep_calls) >= 2
