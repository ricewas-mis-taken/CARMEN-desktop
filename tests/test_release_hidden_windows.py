"""A window hard lock hid must be given back as soon as hard lock stops
enforcing -- otherwise it opens black and cannot be moved."""
import pytest

import enforcer
import window_tracker


@pytest.fixture
def restored(monkeypatch):
    calls = []
    monkeypatch.setattr(enforcer, "restore_all_taskbar_previews", lambda: calls.append(1))
    return calls


def _status(**kw):
    base = {"isActive": True, "isPaused": False, "isBreak": False, "lockMode": "hard"}
    base.update(kw)
    return base


def test_running_hard_lock_keeps_its_windows_hidden(restored):
    window_tracker.release_hidden_windows_unless_hard_locked(_status())
    assert restored == []


@pytest.mark.parametrize("change", [
    {"isPaused": True}, {"isBreak": True}, {"isActive": False}, {"lockMode": "soft"},
])
def test_everything_else_gives_them_back(restored, change):
    window_tracker.release_hidden_windows_unless_hard_locked(_status(**change))
    assert restored == [1]
