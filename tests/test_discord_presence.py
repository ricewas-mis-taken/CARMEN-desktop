import discord_presence as dp


def _status(**kw):
    return {"isActive": True, "isPaused": False, "isBreak": False, "pomodoro": None, "source": "task",
            "isBurnout": False, "startTime": "2026-11-02T10:00:00", "endTime": "2026-11-02T10:25:00",
            "eventTitle": "Maths", **kw}


POMO = {"currentCycle": 2, "totalCycles": 4}


def test_idle_shows_nothing():
    assert dp.describe({"isActive": False}, None) is None


def test_pomodoro_focus_counts_down_and_shows_the_cycle():
    card = dp.describe(_status(pomodoro=POMO), None)
    assert card["details"] == "Pomodoro focus" and card["state"] == "Cycle"
    assert card["party_size"] == [2, 4] and card["end"] and "start" not in card


def test_a_break_counts_down_without_a_cycle():
    card = dp.describe(_status(pomodoro=POMO, isBreak=True, isPaused=True), None)
    assert card["details"] == "Taking a break" and card["end"] and "party_size" not in card


def test_names_only_when_asked_for():
    assert dp.describe(_status(), None, show_names=True)["details"] == "Focus session: Maths"
    assert dp.describe(_status(), None)["details"] == "Focus session"


def test_reviews_and_burnout_count_up():
    review = dp.describe(_status(source="review", reviewProblemName="Quadratics 1"), None)
    assert review["details"] == "Reviewing" and review["start"] and "end" not in review
    assert dp.describe(_status(source="review", reviewProblemName="Quadratics 1"), None,
                       show_names=True)["details"] == "Reviewing: Quadratics 1"
    burnout = dp.describe(_status(isBurnout=True), None)
    assert burnout["details"] == "Working until burnout" and burnout["start"] and "end" not in burnout
    side = dp.describe({"isActive": False}, {"problemName": "P", "isPaused": True})
    assert side["details"] == "Reviewing" and side["state"] == "Paused"


def test_paused_shows_no_clock():
    card = dp.describe(_status(isPaused=True), None)
    assert card["state"] == "Paused" and "start" not in card and "end" not in card
