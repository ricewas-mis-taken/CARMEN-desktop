from datetime import datetime, timedelta

import pytest

import review_store
import session_history
import session_manager


@pytest.fixture(autouse=True)
def _no_side_effects(isolate_state, monkeypatch):
    import enforcer
    monkeypatch.setattr(enforcer, "restore_all_taskbar_previews", lambda: None)
    monkeypatch.setattr(review_store, "auto_pause_for_break", lambda: None)
    monkeypatch.setattr(review_store, "auto_resume_from_break", lambda: None)


def test_pause_racing_pomodoro_phase_expiry_advances_instead_of_ending(monkeypatch):
    appended = []
    monkeypatch.setattr(session_history, "append_entry", appended.append)
    session_manager.start_pomodoro_session(25, 5, 4, "soft", ["x.exe"], ["a.com"])
    # focus block's clock just hit zero, nobody has polled get_status() yet
    session_manager._state["endTime"] = (datetime.now() - timedelta(seconds=1)).isoformat()

    status = session_manager.pause_session()

    assert status["isActive"] is True
    assert status["pomodoro"]["phase"] == "break"
    assert appended == []  # the pomodoro was not finalized to history early
