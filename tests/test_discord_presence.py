import discord_presence as dp


def _status(**kw):
    return {"isActive": True, "isPaused": False, "isBreak": False, "pomodoro": None, "source": "task",
            "isBurnout": False, "startTime": "2026-11-02T10:00:00", "endTime": "2026-11-02T10:25:00",
            "eventTitle": "Maths", **kw}


POMO = {"currentCycle": 2, "totalCycles": 4, "focusMinutes": 25, "breakMinutes": 5}


def test_idle_shows_nothing():
    assert dp.describe({"isActive": False}, None) is None


def test_pomodoro_focus_counts_down_and_shows_the_cycle():
    card = dp.describe(_status(pomodoro=POMO), None)
    assert card["details"] == "Pomodoro focus" and card["state"] == "25 min focus"
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
    assert side is None


def test_paused_shows_no_clock():
    card = dp.describe(_status(isPaused=True), None)
    assert card is None


def test_a_break_says_how_long_it_is():
    card = dp.describe(_status(pomodoro=POMO, isBreak=True), None)
    assert card["state"] == "5 min break"


def test_a_custom_length_says_its_length_and_counts_down():
    card = dp.describe(_status(), None)
    assert card["state"] == "25 min session" and card["end"]


def test_quick_switching_is_not_shown(monkeypatch):
    import threading
    sent = []

    class Client:
        def update(self, **kw): sent.append(kw)
        def clear(self): sent.append(None)

    p = dp.Presence(threading.Event())
    p._client = Client()
    cards = iter([dp.describe(_status(isBurnout=True), None), dp.describe(_status(), None),
                  dp.describe(_status(isBurnout=True), None), dp.describe(_status(isBurnout=True), None)])
    monkeypatch.setattr(dp, "describe", lambda *a, **k: next(cards))
    monkeypatch.setattr(dp.session_manager, "get_status", lambda: {})
    monkeypatch.setattr(dp.review_store, "get_active_review", lambda: None)
    for _ in range(4):
        p._update({})
    assert len(sent) == 1 and sent[0]["details"] == "Working until burnout"
