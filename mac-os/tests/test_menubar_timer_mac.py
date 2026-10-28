"""Title and detail text for the menu-bar timer (pure functions)."""
from mac_os import menubar_timer_mac as m


def _status(**kw):
    base = {"isActive": True, "isPaused": False, "isBreak": False, "isBurnout": False,
            "secondsRemaining": 754, "startTime": None, "violationLog": [],
            "lockMode": "hard", "violationCount": 2}
    base.update(kw)
    return base


def test_idle_shows_only_the_dot():
    assert m.title_for(_status(isActive=False)) == ""


def test_timed_session_counts_down():
    assert m.title_for(_status()) == "12:34"


def test_paused_is_marked():
    assert m.title_for(_status(isPaused=True)) == "⏸ 12:34"


def test_details_list_lock_and_violations():
    assert m.detail_lines(_status()) == ["Lock mode: hard", "Violations: 2"]
    assert m.detail_lines(_status(isPaused=True))[0] == "Paused"
    assert m.detail_lines(_status(isActive=False)) == ["No active session"]
