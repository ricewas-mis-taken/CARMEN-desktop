"""The review wheel: slice sizes, where it stops, and the fate lock after a spin."""
import random
from datetime import date, timedelta

import pytest

import qt_ui.review_tab as review_tab
import qt_ui.review_wheel as wheel_ui
import review_store
import review_wheel
import score_store


@pytest.fixture(autouse=True)
def _fresh_fate():
    review_wheel.clear()
    yield
    review_wheel.clear()


def _problem(due_in=0, original_in=None):
    today = date.today()
    out = {"nextReviewDate": (today + timedelta(days=due_in)).isoformat()}
    if original_in is not None:
        out["originalDueDate"] = (today + timedelta(days=original_in)).isoformat()
    return out


def test_late_and_pushed_back_and_points_losing_reviews_get_bigger_slices():
    on_time = review_wheel.weight(_problem(0))
    late = review_wheel.weight(_problem(-4))
    pushed = review_wheel.weight(_problem(0, original_in=-2))
    assert on_time == 1.0
    assert late > on_time and pushed > on_time
    assert review_wheel.weight(_problem(-4)) > review_wheel.weight(_problem(-1))
    assert review_wheel.points_lost(_problem(-2)) == 6
    assert review_wheel.points_lost(_problem(0)) == 0


def test_a_bigger_slice_wins_more_often():
    rng = random.Random(7)
    wins = [0, 0]
    for _ in range(2000):
        wins[review_wheel.spin([1.0, 5.0], rng)[0]] += 1
    assert wins[1] > wins[0] * 3


def test_the_wheel_stops_with_the_pointer_inside_the_winning_slice(qtbot):
    landed = []
    wheel = wheel_ui.WheelWidget(["A", "B", "C"], [1, 6, 2], landed.append)
    qtbot.addWidget(wheel)
    wheel.show()
    for seed in range(5):
        landed.clear()
        wheel.spin(rng=random.Random(seed))
        qtbot.waitUntil(lambda: bool(landed), timeout=12000)
        assert wheel.slice_under_pointer() == landed[0]
        wheel._winner = None


def test_it_shows_only_names_and_every_slice_has_its_own_colour(qtbot):
    names = ["Parts drill", "Trig sub", "Epsilon proof"]
    wheel = wheel_ui.WheelWidget(names, [1, 1, 1])
    qtbot.addWidget(wheel)
    wheel.resize(420, 420)
    assert wheel.names == names
    assert len(set(wheel_ui.COLOURS[:3])) == 3
    assert not wheel.grab().isNull()


def test_the_winner_pops_with_confetti_and_the_lights_and_pointer_move(qtbot):
    landed = []
    wheel = wheel_ui.WheelWidget(["A", "B", "C", "D"], [1, 2, 3, 1], landed.append)
    qtbot.addWidget(wheel)
    wheel.resize(460, 460)
    wheel.show()
    qtbot.waitExposed(wheel)
    phase = wheel._phase
    wheel.spin(rng=random.Random(2))
    kicked = []
    wheel._frame.timeout.connect(lambda: kicked.append(abs(wheel._pointer)))
    qtbot.waitUntil(lambda: bool(landed), timeout=12000)
    assert wheel._phase > phase                                   # the lights run on their own clock
    assert max(kicked) > 1.0                                      # the pointer was flicked by the pegs
    assert wheel._confetti                                        # a burst at the win
    qtbot.waitUntil(lambda: wheel._pop >= 1.0, timeout=3000)      # the winning slice comes out
    assert not wheel.grab().isNull()


def test_the_animation_clock_stops_when_the_wheel_is_hidden(qtbot):
    wheel = wheel_ui.WheelWidget(["A", "B"], [1, 1])
    qtbot.addWidget(wheel)
    wheel.show()
    qtbot.waitExposed(wheel)
    assert wheel._frame.isActive()
    wheel.hide()
    assert not wheel._frame.isActive()


@pytest.mark.parametrize("count", [1, 2, 12, 40])
def test_the_wheel_paints_with_any_number_of_slices(qtbot, count):
    wheel = wheel_ui.WheelWidget([f"Problem {i}" for i in range(count)], [1 + i % 4 for i in range(count)])
    qtbot.addWidget(wheel)
    wheel.resize(420, 420)
    assert not wheel.grab().isNull()
    wheel._winner, wheel._pop = count - 1, 1.0
    assert not wheel.grab().isNull()


def _topic_with_two(qtbot):
    topic = review_store.create_topic("Calc")
    subject = review_store.create_subject(topic["id"], "Integrals", "#4A90D9")
    one = review_store.create_problem(topic["id"], subject["id"], "One", stars=3, description_type="text", description_text="x")
    two = review_store.create_problem(topic["id"], subject["id"], "Two", stars=3, description_type="text", description_text="x")
    view = review_tab._TopicView(topic["id"])
    qtbot.addWidget(view)
    return view, one, two


def test_only_due_reviews_that_are_in_rotation_are_on_the_wheel(qtbot, isolate_review_db, isolate_state, monkeypatch):
    view, one, two = _topic_with_two(qtbot)
    assert review_wheel.entries(view._topic_id) == []          # new problems are not due yet
    due = [dict(one, nextReviewDate=date.today().isoformat(), outOfRotation=False),
           dict(two, nextReviewDate=date.today().isoformat(), outOfRotation=True)]
    monkeypatch.setattr(review_store, "list_problems", lambda topic_id, due_only=True: due)
    assert [p["name"] for p, _ in review_wheel.entries(view._topic_id)] == ["One"]


def test_starting_another_review_after_the_wheel_picked_asks_and_costs_points(
        qtbot, isolate_review_db, isolate_state, monkeypatch):
    view, one, two = _topic_with_two(qtbot)
    started = []
    monkeypatch.setattr(review_tab, "_ReviewStartDialog", lambda problem, on_start: started.append(problem["name"]))
    asked = []
    answer = {"yes": False}
    monkeypatch.setattr(review_tab, "_confirm", lambda parent, title, text: asked.append(text) or answer["yes"])
    review_wheel.set_fate(one["id"])

    view._start_problem(two)                       # says no: nothing starts, nothing lost
    assert asked and "One" in asked[0] and not started
    assert review_wheel.fate() == one["id"]
    assert not [e for e in score_store.events() if e["kind"] == "fate"]

    view._start_problem(one)                       # the pick itself needs no question
    assert started == ["One"] and len(asked) == 1

    answer["yes"] = True
    view._start_problem(two)                       # says yes: it costs points and frees the wheel
    assert started == ["One", "Two"]
    fines = [e for e in score_store.events() if e["kind"] == "fate"]
    assert len(fines) == 1 and fines[0]["points"] == -review_wheel.BREAK_COST
    assert review_wheel.fate() is None


def test_finishing_the_fated_review_frees_the_wheel(qtbot, isolate_review_db, isolate_state):
    view, one, two = _topic_with_two(qtbot)
    review_wheel.set_fate(one["id"])
    review_wheel.review_done()                      # not started yet: still fated
    assert review_wheel.fate() == one["id"]
    review_wheel.mark_started(one["id"])
    review_wheel.review_done()
    assert review_wheel.fate() is None


def test_a_review_finished_any_other_way_frees_the_wheel_too(qtbot, isolate_review_db, isolate_state):
    view, one, two = _topic_with_two(qtbot)
    review_wheel.set_fate(one["id"])
    assert review_wheel.current()["id"] == one["id"]
    review_store.finish_review_for_problem(one["id"], 120)       # a task card or the mini timer, not the banner
    assert review_wheel.current() is None and review_wheel.fate() is None


def test_the_button_names_the_fated_review_and_reopens_it_instead_of_the_wheel(
        qtbot, isolate_review_db, isolate_state, monkeypatch):
    view, one, two = _topic_with_two(qtbot)
    view.resize(420, 600)                                   # a narrow window must still show the pick
    view.show()
    qtbot.waitExposed(view)
    assert view._wheel_button.isVisible() and not view._fate_strip.isVisible()
    review_wheel.set_fate(one["id"])
    view._show_wheel_button()
    assert view._fate_strip.isVisible() and not view._wheel_button.isVisible()
    assert "One" in view._fate_strip._label.text()
    started = []
    monkeypatch.setattr(review_tab, "_ReviewStartDialog", lambda problem, on_start: started.append(problem["name"]))
    view._open_wheel()
    assert started == ["One"]


def test_a_fate_whose_problem_was_deleted_is_dropped(qtbot, isolate_review_db, isolate_state):
    view, one, two = _topic_with_two(qtbot)
    review_wheel.set_fate("gone")
    assert view._active_fate() is None and review_wheel.fate() is None


def test_a_pick_from_another_topic_is_started_in_its_own_topic(qtbot, isolate_review_db, isolate_state, monkeypatch):
    tab = review_tab.ReviewTab()
    qtbot.addWidget(tab)
    a = review_store.create_topic("A")
    b = review_store.create_topic("B")
    sub = review_store.create_subject(a["id"], "S", "#4A90D9")
    problem = review_store.create_problem(a["id"], sub["id"], "Mine", stars=3, description_type="text", description_text="x")
    tab._reload() if hasattr(tab, "_reload") else tab.refresh()
    started = []
    monkeypatch.setattr(review_tab, "_ReviewStartDialog", lambda p, on_start: started.append((p["name"], on_start.__self__._topic_id)))
    review_wheel.set_fate(problem["id"])
    tab._topic_views[b["id"]]._open_wheel()
    assert started == [("Mine", a["id"])]


def test_a_review_started_on_the_wheels_pick_is_filed_as_fated(isolate_state):
    import session_history
    import session_manager as sm
    sm.start_session(480, "soft", [], [], source="review", event_id="t", event_title="Calc review",
                     review_problem_name="Parts drill", review_problem_id=7, is_burnout=True, fated=True)
    assert sm.get_status()["fated"] is True
    sm.end_session()
    sm.start_session(480, "soft", [], [], source="review", event_id="t", event_title="Calc review",
                     review_problem_name="Other", review_problem_id=8, is_burnout=True)
    sm.end_session()
    filed = {e["reviewProblemName"]: e["fated"] for e in session_history.load_all()}
    assert filed == {"Parts drill": True, "Other": False}
    assert sm.get_status()["fated"] is False                    # not carried into the next session


def test_the_history_card_and_text_say_fated(qtbot):
    import qt_ui.history_viewer as history_viewer
    session = {"source": "review", "reviewProblemName": "Parts drill", "reviewSubjectName": "Integrals",
               "startTime": "2026-11-13T19:00:00", "endTime": "2026-11-13T19:30:00", "lockMode": "soft",
               "fated": True, "violationLog": []}
    card = history_viewer._SessionCard(session)
    qtbot.addWidget(card)
    assert "Fated" in [w.text() for w in card.findChildren(history_viewer.QLabel)]
    plain_card = history_viewer._SessionCard(dict(session, fated=False))
    qtbot.addWidget(plain_card)
    assert "Fated" not in [w.text() for w in plain_card.findChildren(history_viewer.QLabel)]
    assert "Fated" in history_viewer._format_source_label(session)
    plain = dict(session, fated=False)
    assert "Fated" not in history_viewer._format_source_label(plain)


def test_finishing_through_the_review_tab_banner_frees_the_wheel(qtbot, isolate_review_db, isolate_state):
    tab = review_tab.ReviewTab()
    qtbot.addWidget(tab)
    topic = review_store.create_topic("Calc")
    sub = review_store.create_subject(topic["id"], "S", "#4A90D9")
    problem = review_store.create_problem(topic["id"], sub["id"], "Mine", stars=3, description_type="text", description_text="x")
    review_wheel.set_fate(problem["id"])
    review_wheel.mark_started(problem["id"])
    tab._banner_finished()
    assert review_wheel.fate() is None
