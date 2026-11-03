import discord_presence as dp


def _status(**kw):
    return {"isActive": True, "isPaused": False, "isBreak": False, "pomodoro": None, "source": "task",
            "isBurnout": False, "startTime": "2026-11-02T10:00:00", "eventTitle": "Maths", **kw}


def test_idle_shows_nothing():
    assert dp.describe({"isActive": False}, None) is None


def test_pomodoro_focus_and_break_with_names_hidden():
    pomo = {"currentCycle": 2, "totalCycles": 4}
    details, state, started = dp.describe(_status(pomodoro=pomo), None)
    assert details == "Pomodoro focus" and state == "Cycle 2 of 4" and started
    details, _, started = dp.describe(_status(pomodoro=pomo, isBreak=True, isPaused=True), None)
    assert details == "On a break" and started is None
    assert "Maths" not in details


def test_names_only_when_asked_for():
    assert dp.describe(_status(), None, show_names=True)[0] == "Focus session: Maths"
    assert dp.describe(_status(), None)[0] == "Focus session"


def test_review_session_and_independent_review():
    s = _status(source="review", reviewProblemName="Quadratics 1")
    assert dp.describe(s, None)[0] == "Reviewing"
    assert dp.describe(s, None, show_names=True)[0] == "Reviewing: Quadratics 1"
    details, state, _ = dp.describe({"isActive": False}, {"problemName": "P", "isPaused": True})
    assert details == "Reviewing" and state == "paused"


def test_burnout_and_paused():
    assert dp.describe(_status(isBurnout=True), None)[0] == "Working until burnout"
    assert dp.describe(_status(isPaused=True), None)[1:] == ("paused", None)
