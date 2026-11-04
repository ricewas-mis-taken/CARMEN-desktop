from datetime import datetime, timedelta

import pause_budget
import score_store
import session_history
import session_manager

T0 = datetime(2026, 11, 3, 9, 0, 0)


def _log(*items):
    """items: (minutes after start, 'pause'|'resume', reason)"""
    return [{"kind": k, "timestamp": (T0 + timedelta(minutes=m)).isoformat(), **({"pauseReason": r} if r else {})}
            for m, k, r in items]


def _entry(log, minutes=60):
    return {"startTime": T0.isoformat(), "endTime": (T0 + timedelta(minutes=minutes)).isoformat(), "violationLog": log}


def test_free_reasons_never_cost_anything():
    log = _log((10, "pause", "break"), (40, "resume", None), (45, "pause", "interrupted"), (55, "resume", None))
    result = pause_budget.summarize(_entry(log))
    assert result["cost"] == 0 and result["escape_seconds"] == 0 and result["free_seconds"] == 40 * 60


def test_checking_within_the_budget_is_free_and_past_it_costs_a_point_per_three_minutes():
    inside = pause_budget.summarize(_entry(_log((10, "pause", "checking"), (15, "resume", None))))   # 5 min < 6 min budget
    assert inside["cost"] == 0 and inside["budget_seconds"] == 360
    over = pause_budget.summarize(_entry(_log((10, "pause", "checking"), (25, "resume", None))))     # 15 min, 9 over
    assert over["over_seconds"] == 540 and over["cost"] == 3


def test_leaving_the_reason_out_counts_as_checking():
    result = pause_budget.summarize(_entry(_log((10, "pause", None), (30, "resume", None))))
    assert result["escape_seconds"] == 20 * 60 and result["cost"] > 0


def test_a_pause_still_open_at_the_end_runs_to_the_end():
    result = pause_budget.summarize(_entry(_log((30, "pause", "checking"))))                          # 30 min left open
    assert result["escape_seconds"] == 30 * 60


def test_the_cost_is_capped():
    result = pause_budget.summarize(_entry(_log((0, "pause", "checking"), (600, "resume", None)), minutes=600))
    assert result["cost"] == pause_budget.MAX_COST


def test_a_session_ending_scores_its_escape_pauses_once(isolate_state):
    session_manager.start_session(1, "soft", [], [])
    session_manager.pause_session("checking")
    log = session_manager.get_status()
    assert log["isPaused"] is True
    entry_log = list(session_manager._state["violationLog"])
    assert entry_log[-1]["pauseReason"] == "checking"
    # age the pause so it exceeds the minimum budget
    entry_log[-1]["timestamp"] = (datetime.now() - timedelta(minutes=20)).isoformat()
    session_manager._state["violationLog"] = entry_log
    session_manager.end_session("manual")
    events = [e for e in score_store.load()["events"] if e["kind"] == "pauses"]
    assert len(events) == 1 and events[0]["points"] < 0


def test_a_pause_with_no_reason_is_recorded_as_none_not_break(isolate_state):
    session_manager.start_session(25, "soft", [], [])
    session_manager.pause_session()
    assert session_manager._state["violationLog"][-1]["pauseReason"] is None
    assert pause_budget.reason_of(session_manager._state["violationLog"][-1]) == "unspecified"
