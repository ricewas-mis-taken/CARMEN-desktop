"""Tests for sync_trigger.py's debounce/enable/disable behavior. Never
touches real auth or network -- auth_manager.is_logged_in() and
sync_client.sync_now() are both monkeypatched. DEBOUNCE_SECONDS is
shortened so these run fast without sleeping for the real 3s default.
"""
import time

import pytest

import auth_manager
import sync_client
import sync_trigger

# Captured before any fixture runs -- conftest.py's autouse
# disable_sync_trigger fixture no-ops sync_trigger.note_change globally
# (so ordinary store tests never risk touching real auth/network), which
# would otherwise mask the very behavior this file exists to test.
_real_note_change = sync_trigger.note_change


class _FakeResult:
    def __init__(self, success=True, not_logged_in=False, error=None):
        self.success = success
        self.not_logged_in = not_logged_in
        self.error = error


@pytest.fixture(autouse=True)
def fast_debounce(monkeypatch):
    monkeypatch.setattr(sync_trigger, "note_change", _real_note_change)
    monkeypatch.setattr(sync_trigger, "DEBOUNCE_SECONDS", 0.05)
    monkeypatch.setattr(sync_trigger, "_enabled", True)
    monkeypatch.setattr(sync_trigger, "_timer", None)
    yield
    if sync_trigger._timer is not None:
        sync_trigger._timer.cancel()


def test_note_change_is_a_noop_when_logged_out(monkeypatch):
    monkeypatch.setattr(auth_manager, "may_be_logged_in", lambda: False)
    calls = []
    monkeypatch.setattr(sync_client, "sync_now", lambda: calls.append(1) or _FakeResult())

    sync_trigger.note_change()
    time.sleep(0.15)

    assert calls == []
    assert sync_trigger._timer is None


def test_note_change_fires_sync_now_after_the_debounce_window(monkeypatch):
    monkeypatch.setattr(auth_manager, "may_be_logged_in", lambda: True)
    calls = []
    monkeypatch.setattr(sync_client, "sync_now", lambda: calls.append(1) or _FakeResult())

    sync_trigger.note_change()
    assert calls == []  # not yet -- still debouncing
    time.sleep(0.15)

    assert calls == [1]


def test_rapid_successive_calls_collapse_into_one_sync(monkeypatch):
    monkeypatch.setattr(auth_manager, "may_be_logged_in", lambda: True)
    calls = []
    monkeypatch.setattr(sync_client, "sync_now", lambda: calls.append(1) or _FakeResult())

    for _ in range(5):
        sync_trigger.note_change()
        time.sleep(0.01)
    time.sleep(0.15)

    assert calls == [1]


def test_disable_cancels_pending_sync_and_blocks_future_calls_until_enable(monkeypatch):
    monkeypatch.setattr(auth_manager, "may_be_logged_in", lambda: True)
    calls = []
    monkeypatch.setattr(sync_client, "sync_now", lambda: calls.append(1) or _FakeResult())

    sync_trigger.note_change()
    sync_trigger.disable()
    time.sleep(0.15)
    assert calls == []  # the pending timer was cancelled

    sync_trigger.note_change()
    time.sleep(0.15)
    assert calls == []  # still disabled

    sync_trigger.enable()
    sync_trigger.note_change()
    time.sleep(0.15)
    assert calls == [1]


def test_fire_survives_sync_now_raising(monkeypatch):
    monkeypatch.setattr(auth_manager, "may_be_logged_in", lambda: True)

    def boom():
        raise RuntimeError("boom")

    monkeypatch.setattr(sync_client, "sync_now", boom)

    sync_trigger.note_change()
    time.sleep(0.15)  # must not crash the test process


def test_fire_logs_but_does_not_raise_on_a_failed_sync(monkeypatch):
    monkeypatch.setattr(auth_manager, "may_be_logged_in", lambda: True)
    monkeypatch.setattr(
        sync_client, "sync_now", lambda: _FakeResult(success=False, not_logged_in=False, error="couldn't reach server")
    )

    sync_trigger.note_change()
    time.sleep(0.15)  # must not crash the test process


def test_note_change_never_touches_the_network_check(monkeypatch):
    """note_change() runs on the window thread after every save; is_logged_in() can
    block on a network token refresh, so it must not be what decides."""
    monkeypatch.setattr(auth_manager, "may_be_logged_in", lambda: True)

    def boom():
        raise AssertionError("note_change() called the networked login check")

    monkeypatch.setattr(auth_manager, "is_logged_in", boom)
    monkeypatch.setattr(sync_client, "sync_now", lambda: _FakeResult())
    sync_trigger.note_change()          # must not raise


def test_may_be_logged_in_uses_local_state_only(monkeypatch):
    monkeypatch.setattr(auth_manager, "_access_token", None)
    monkeypatch.setattr(auth_manager, "_current_user", None)
    monkeypatch.setattr(auth_manager, "_load_refresh_token", lambda: None)
    assert auth_manager.may_be_logged_in() is False
    monkeypatch.setattr(auth_manager, "_load_refresh_token", lambda: "saved")
    assert auth_manager.may_be_logged_in() is True
    monkeypatch.setattr(auth_manager, "_load_refresh_token", lambda: None)
    monkeypatch.setattr(auth_manager, "_access_token", "t")
    monkeypatch.setattr(auth_manager, "_current_user", {"id": "u"})
    assert auth_manager.may_be_logged_in() is True
