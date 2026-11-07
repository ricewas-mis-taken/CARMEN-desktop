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


def test_the_api_pause_takes_a_reason_and_ignores_unknown_ones(isolate_state):
    import api_server
    import config
    client = api_server.app.test_client()
    client.environ_base["HTTP_X_CARMEN_TOKEN"] = config.get_api_token()
    session_manager.start_session(25, "soft", [], [])
    client.post("/session/pause", json={"reason": "stuck"})
    assert session_manager._state["violationLog"][-1]["pauseReason"] == "stuck"
    session_manager.resume_session()
    client.post("/session/pause", json={"reason": "made-up"})
    assert session_manager._state["violationLog"][-1]["pauseReason"] is None
    session_manager.resume_session()
    client.post("/session/pause")                                  # an old extension: no body at all
    assert session_manager._state["violationLog"][-1]["pauseReason"] is None


def test_the_reason_popup_pauses_with_the_chosen_reason(qtbot, isolate_state):
    from qt_ui import pause_reason
    session_manager.start_session(25, "soft", [], [])
    done = []
    popup = pause_reason.ask(None, lambda: done.append(1))
    qtbot.addWidget(popup)
    assert not session_manager.get_status()["isPaused"]
    popup._choose("checking")                       # checking something needs a word on what
    assert not session_manager.get_status()["isPaused"] and done == []
    popup._choose("checking", "looking up the formula")
    assert session_manager.get_status()["isPaused"] and done == [1]
    marker = session_manager._state["violationLog"][-1]
    assert marker["pauseReason"] == "checking" and marker["pauseNote"] == "looking up the formula"


def test_the_budget_note_for_the_task_and_mini_timer(qtbot, isolate_state):
    from qt_ui import pause_reason
    session_manager.start_session(50, "soft", [], [])
    popup = pause_reason.ask(None)
    qtbot.addWidget(popup)
    popup.close()
    text, _colour = pause_reason.budget_note(session_manager.get_status())
    assert text.startswith("Pause budget:") and "left" in text


def test_live_budget_counts_an_open_checking_pause():
    from datetime import timedelta
    log = _log((10, "pause", "checking"))
    now = T0 + timedelta(minutes=13)
    live = pause_budget.live({"isActive": True, "startTime": T0.isoformat(), "secondsRemaining": 47 * 60,
                              "violationLog": log}, now)
    assert live["budget_seconds"] == 12 * 60 - 12 * 60 + 360 or live["budget_seconds"] == 360
    assert live["used_seconds"] == 180 and live["left_seconds"] == 180 and live["over_seconds"] == 0
    assert pause_budget.live({"isActive": False}) is None


def test_break_pauses_are_not_in_the_live_budget():
    from datetime import timedelta
    log = _log((10, "pause", "break"))
    live = pause_budget.live({"isActive": True, "startTime": T0.isoformat(), "secondsRemaining": 1800,
                              "violationLog": log}, T0 + timedelta(minutes=20))
    assert live["used_seconds"] == 0


def test_a_long_break_is_free_for_the_time_set_then_counts():
    log = [{"kind": "pause", "timestamp": T0.isoformat(), "pauseReason": "long_break", "pauseMinutes": 30},
           {"kind": "resume", "timestamp": (T0 + timedelta(minutes=50)).isoformat()}]
    items = pause_budget.pauses(log, T0.isoformat(), (T0 + timedelta(minutes=60)).isoformat())
    assert items == [{"reason": "long_break", "seconds": 30 * 60 + 60}, {"reason": "overrun", "seconds": 20 * 60 - 60}]
    result = pause_budget.summarize({"violationLog": log, "startTime": T0.isoformat(),
                                     "endTime": (T0 + timedelta(minutes=60)).isoformat()})
    assert result["free_seconds"] == 31 * 60 and result["escape_seconds"] == 19 * 60


def test_a_long_break_within_its_time_costs_nothing():
    log = [{"kind": "pause", "timestamp": T0.isoformat(), "pauseReason": "long_break", "pauseMinutes": 60},
           {"kind": "resume", "timestamp": (T0 + timedelta(minutes=58)).isoformat()}]
    result = pause_budget.summarize({"violationLog": log, "startTime": T0.isoformat(),
                                     "endTime": (T0 + timedelta(minutes=120)).isoformat()})
    assert result["escape_seconds"] == 0 and result["cost"] == 0


def test_the_break_over_notice_comes_once(isolate_state):
    session_manager.start_session(60, "soft", [], [])
    session_manager.pause_session("long_break", None, 15)
    assert session_manager.pop_break_over() is None
    later = datetime.now() + timedelta(minutes=16)
    assert session_manager.pop_break_over(later) == 15
    assert session_manager.pop_break_over(later) is None


def test_the_popup_sends_the_minutes_of_a_long_break(qtbot, isolate_state):
    from qt_ui import pause_reason
    session_manager.start_session(60, "soft", [], [])
    popup = pause_reason.ask(None)
    qtbot.addWidget(popup)
    popup._minutes.setValue(45)
    popup._choose("long_break")
    marker = session_manager._state["violationLog"][-1]
    assert marker["pauseReason"] == "long_break" and marker["pauseMinutes"] == 45
