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
import screentime_store
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
    monkeypatch.setattr(enforcer, "sweep_minimize_blocked_windows", lambda: [])

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


def test_soft_lock_rewarns_periodically_while_staying_on_the_same_app(isolate_state, fast_polling, monkeypatch):
    """Regression test: soft_lock_warning used to only ever fire once per
    continuous stretch of focus on a blocked app (gated by the same
    process_name != last_flagged_process check violation logging uses) --
    staying on the same blocked app for the rest of the session after that
    one warning never nagged again. SOFT_LOCK_REWARN_SECONDS should make it
    recur while the user stays off-task."""
    monkeypatch.setattr(window_tracker, "SOFT_LOCK_REWARN_SECONDS", 0.1)
    session_manager.start_session(25, "soft", ["discord.exe"], [])

    monkeypatch.setattr(
        window_tracker, "get_active_window",
        lambda: {"title": "Discord", "process_name": "discord.exe", "pid": 4242, "hwnd": 111},
    )

    warning_calls = []
    monkeypatch.setattr(enforcer, "soft_lock_warning", lambda name, hwnd: warning_calls.append(name))

    stop_event = threading.Event()
    thread = threading.Thread(target=window_tracker.run_polling_loop, args=(stop_event,), daemon=True)
    thread.start()
    try:
        time.sleep(0.5)
    finally:
        stop_event.set()
        thread.join(timeout=2)

    # At 0.1s rewarn cadence over ~0.5s of continuous focus, several warnings
    # should have fired -- one alone would mean the old "once ever" bug.
    assert len(warning_calls) >= 2
    assert all(name == "discord.exe" for name in warning_calls)


def test_soft_lock_rewarns_immediately_when_the_app_is_reopened(isolate_state, fast_polling, monkeypatch):
    """Regression test: comparing only process_name couldn't tell "same
    window, stayed foreground the whole time" apart from "closed and
    reopened a new window of the same app" -- reopening within
    SOFT_LOCK_REWARN_SECONDS of the last warning silently got no warning at
    all until that cooldown ran out, even though it's a fresh violation. A
    different hwnd for the same process name must warn immediately."""
    monkeypatch.setattr(window_tracker, "SOFT_LOCK_REWARN_SECONDS", 1000)  # so only the hwnd change explains a 2nd call
    session_manager.start_session(25, "soft", ["discord.exe"], [])

    window_holder = {"hwnd": 111}
    monkeypatch.setattr(
        window_tracker, "get_active_window",
        lambda: {"title": "Discord", "process_name": "discord.exe", "pid": 4242, "hwnd": window_holder["hwnd"]},
    )

    warning_calls = []
    monkeypatch.setattr(enforcer, "soft_lock_warning", lambda name, hwnd: warning_calls.append((name, hwnd)))

    stop_event = threading.Event()
    thread = threading.Thread(target=window_tracker.run_polling_loop, args=(stop_event,), daemon=True)
    thread.start()
    try:
        time.sleep(0.2)
        assert len(warning_calls) == 1  # the first sighting, no rewarn cooldown elapsed yet

        # Simulate closing and reopening the same app -- a brand new hwnd.
        window_holder["hwnd"] = 222
        time.sleep(0.2)
    finally:
        stop_event.set()
        thread.join(timeout=2)

    assert warning_calls == [("discord.exe", 111), ("discord.exe", 222)]


def test_lock_mode_switched_to_hard_mid_violation_takes_effect_without_leaving(
    isolate_state, fast_polling, monkeypatch
):
    """Regression test: hard_lock_redirect/soft_lock_warning used to only
    ever be dispatched inside the `is_new_flag` branch (process_name !=
    last_flagged_process) -- true only on the *first* tick a blocked app
    becomes foreground. Staying continuously on the same blocked window
    while switching lock mode soft -> hard via qt_ui/picker_dialogs.py's
    Edit Session Rules dialog (session_manager.update_blocklist) never left
    that window, so is_new_flag stayed False forever and hard_lock_redirect
    was never reached at all -- the session's saved lockMode said "hard" but
    enforcement kept warning like soft indefinitely."""
    session_manager.start_session(25, "soft", ["discord.exe"], [])

    monkeypatch.setattr(
        window_tracker, "get_active_window",
        lambda: {"title": "Discord", "process_name": "discord.exe", "pid": 4242, "hwnd": 111},
    )

    warning_calls = []
    redirect_calls = []
    monkeypatch.setattr(enforcer, "soft_lock_warning", lambda name, hwnd: warning_calls.append(name))
    monkeypatch.setattr(enforcer, "hard_lock_redirect", lambda name: redirect_calls.append(name))
    monkeypatch.setattr(enforcer, "sweep_minimize_blocked_windows", lambda: [])

    stop_event = threading.Event()
    thread = threading.Thread(target=window_tracker.run_polling_loop, args=(stop_event,), daemon=True)
    thread.start()
    try:
        time.sleep(0.1)
        assert warning_calls, "expected at least one soft warning before switching modes"
        assert not redirect_calls

        # Switch to hard mode without the foreground ever leaving discord.exe.
        session_manager.update_blocklist(["discord.exe"], [], lock_mode="hard")
        time.sleep(0.1)
    finally:
        stop_event.set()
        thread.join(timeout=2)

    assert redirect_calls, "hard lock must kick in on the next tick, without needing to leave and return"


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
    monkeypatch.setattr(enforcer, "sweep_minimize_blocked_windows", lambda: [])

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
    monkeypatch.setattr(enforcer, "sweep_minimize_blocked_windows", lambda: [])

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

    def fake_sweep():
        sweep_calls.append(True)
        return []

    monkeypatch.setattr(enforcer, "sweep_minimize_blocked_windows", fake_sweep)

    stop_event = threading.Event()
    thread = threading.Thread(target=window_tracker.run_polling_loop, args=(stop_event,), daemon=True)
    thread.start()
    try:
        time.sleep(0.1)
    finally:
        stop_event.set()
        thread.join(timeout=2)

    assert len(sweep_calls) >= 2


def test_sweep_caught_window_shows_overlay_and_records_violation(isolate_state, fast_polling, monkeypatch):
    """Regression test: sweep_minimize_blocked_windows() catching a window
    that was never seen as the foreground window (e.g. it opened but hadn't
    actually grabbed OS focus yet by the time this tick's foreground check
    ran) used to minimize it in total silence -- no violation recorded, no
    overlay shown, no "Unblock" button anywhere. The user just sees the app
    they clicked never appear, with no way to let it through."""
    session_manager.start_session(25, "hard", ["discord.exe"], [])
    # The foreground window this whole test is something unrelated -- the
    # sweep is what has to catch discord.exe and surface it.
    monkeypatch.setattr(
        window_tracker, "get_active_window",
        lambda: {"title": "Notepad", "process_name": "notepad.exe", "pid": 1, "hwnd": 999},
    )
    monkeypatch.setattr("enforcer.hard_lock_redirect", lambda name: None)
    monkeypatch.setattr(enforcer, "sweep_minimize_blocked_windows", lambda: [("discord.exe", 555)])

    notice_calls = []
    monkeypatch.setattr(enforcer, "show_blocked_notice", lambda name: notice_calls.append(name))

    stop_event = threading.Event()
    thread = threading.Thread(target=window_tracker.run_polling_loop, args=(stop_event,), daemon=True)
    thread.start()
    try:
        time.sleep(0.1)
    finally:
        stop_event.set()
        thread.join(timeout=2)

    assert notice_calls
    assert all(name == "discord.exe" for name in notice_calls)
    status = session_manager.get_status()
    assert status["violationCount"] >= 1


def test_sweep_notice_not_suppressed_by_a_just_logged_violation(isolate_state, fast_polling, monkeypatch):
    """Regression test: the sweep's overlay notice used to share its cooldown
    with the violation-log dedup (last_violation_time, a real 5s cooldown
    never sped up by fast_polling). If the foreground path had already
    logged a violation for a process moments earlier -- e.g. right before
    its own redirect got cooldown-suppressed for still being the same
    window -- that same shared cooldown also silently blocked the sweep's
    notice for the process for the next several seconds, even though the
    window kept getting minimized on every tick in that gap. Net effect:
    the window disappears, but no message and no Unblock button ever shows
    up. The notice must be gated on its own independent (hwnd-keyed)
    cooldown instead."""
    session_manager.start_session(25, "hard", ["discord.exe"], [])

    foreground = {"name": "discord.exe", "hwnd": 111}
    monkeypatch.setattr(
        window_tracker, "get_active_window",
        lambda: {"title": "x", "process_name": foreground["name"], "pid": 1, "hwnd": foreground["hwnd"]},
    )
    monkeypatch.setattr("enforcer.hard_lock_redirect", lambda name: None)
    monkeypatch.setattr(enforcer, "sweep_minimize_blocked_windows", lambda: [])

    notice_calls = []
    monkeypatch.setattr(enforcer, "show_blocked_notice", lambda name: notice_calls.append(name))

    stop_event = threading.Event()
    thread = threading.Thread(target=window_tracker.run_polling_loop, args=(stop_event,), daemon=True)
    thread.start()
    try:
        # Let the foreground path log a violation for discord.exe first (its
        # own VIOLATION_COOLDOWN_SECONDS entry is now "fresh", real 5s value).
        time.sleep(0.05)
        status = session_manager.get_status()
        assert status["violationCount"] >= 1

        # Now switch away in the foreground branch's eyes (an acceptable app)
        # while the sweep starts independently catching discord.exe every
        # tick -- as if the user minimized it, then reopened it somewhere
        # the foreground check doesn't sample it, well within the 5s
        # violation-log cooldown from moments ago.
        foreground["name"] = "notepad.exe"
        foreground["hwnd"] = 222
        monkeypatch.setattr(enforcer, "sweep_minimize_blocked_windows", lambda: [("discord.exe", 333)])
        time.sleep(0.05)
    finally:
        stop_event.set()
        thread.join(timeout=2)

    assert notice_calls, "sweep must notify even though a violation was just logged moments ago"


def test_screen_time_is_tracked_even_with_no_session_active(
    isolate_state, fast_polling, monkeypatch, tmp_path,
):
    """Screen time is a general, always-on tally, not tied to enforcement --
    it must keep accumulating for the foreground app even when there's no
    active session at all (the enforcement branch below it is entirely
    skipped in that case)."""
    monkeypatch.setattr(screentime_store, "STATE_PATH", str(tmp_path / "screentime.json"))
    monkeypatch.setattr(screentime_store, "_data", {})
    monkeypatch.setattr(screentime_store, "_last_flush", 0.0)

    monkeypatch.setattr(
        window_tracker, "get_active_window",
        lambda: {"title": "Code", "process_name": "code.exe", "pid": 111, "hwnd": 222},
    )

    assert not session_manager.is_active()

    stop_event = threading.Event()
    thread = threading.Thread(target=window_tracker.run_polling_loop, args=(stop_event,), daemon=True)
    thread.start()
    try:
        time.sleep(0.2)
    finally:
        stop_event.set()
        thread.join(timeout=2)

    today = screentime_store._day_key()
    assert screentime_store.get_day(today)["apps"].get("code.exe", 0) > 0


def test_screen_time_skips_exempt_processes(isolate_state, fast_polling, monkeypatch, tmp_path):
    monkeypatch.setattr(screentime_store, "STATE_PATH", str(tmp_path / "screentime.json"))
    monkeypatch.setattr(screentime_store, "_data", {})
    monkeypatch.setattr(screentime_store, "_last_flush", 0.0)

    monkeypatch.setattr(
        window_tracker, "get_active_window",
        lambda: {"title": "Explorer", "process_name": "explorer.exe", "pid": 111, "hwnd": 222},
    )

    stop_event = threading.Event()
    thread = threading.Thread(target=window_tracker.run_polling_loop, args=(stop_event,), daemon=True)
    thread.start()
    try:
        time.sleep(0.2)
    finally:
        stop_event.set()
        thread.join(timeout=2)

    today = screentime_store._day_key()
    assert screentime_store.get_day(today)["apps"] == {}
