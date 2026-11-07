import time

import enforcer
import session_manager as sm


def test_nothing_counts_in_the_first_moments_of_a_session_then_it_does(isolate_state, monkeypatch):
    monkeypatch.setattr(sm, "ENFORCEMENT_GRACE_SECONDS", 3.0)
    enforcer._last_recorded_violation.clear()
    sm.start_session(30, "soft", ["blocked.exe"], [], source="manual")
    assert sm.in_enforcement_grace()
    enforcer.record_violation_deduped("blocked.exe")
    assert sm.get_status()["violationCount"] == 0
    monkeypatch.setattr(time, "time", lambda: time.monotonic() + 10_000_000)       # well past the grace
    sm._grace_until["t"] = 0.0
    enforcer.record_violation_deduped("blocked.exe")
    assert sm.get_status()["violationCount"] == 1


def test_a_resume_and_a_pomodoro_focus_start_the_grace_too(isolate_state, monkeypatch):
    monkeypatch.setattr(sm, "ENFORCEMENT_GRACE_SECONDS", 3.0)
    sm.start_session(30, "soft", [], [], source="manual")
    sm._grace_until["t"] = 0.0
    sm.pause_session("break")
    sm.resume_session()
    assert sm.in_enforcement_grace()
    sm._grace_until["t"] = 0.0
    assert not sm.in_enforcement_grace()
