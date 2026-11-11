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


def test_the_button_names_the_fated_review_and_reopens_it_instead_of_the_wheel(
        qtbot, isolate_review_db, isolate_state, monkeypatch):
    view, one, two = _topic_with_two(qtbot)
    assert view._wheel_button.text() == "Spin the wheel"
    review_wheel.set_fate(one["id"])
    view._show_wheel_button()
    assert "One" in view._wheel_button.text()
    started = []
    monkeypatch.setattr(review_tab, "_ReviewStartDialog", lambda problem, on_start: started.append(problem["name"]))
    view._open_wheel()
    assert started == ["One"]


def test_a_fate_whose_problem_was_deleted_is_dropped(qtbot, isolate_review_db, isolate_state):
    view, one, two = _topic_with_two(qtbot)
    review_wheel.set_fate("gone")
    assert view._active_fate() is None and review_wheel.fate() is None
