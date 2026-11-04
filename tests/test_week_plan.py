from datetime import date, timedelta

import review_store
import week_plan

TODAY = date(2026, 11, 4)


def _problem(name, due):
    topics = review_store.list_topics()
    topic = topics[0] if topics else review_store.create_topic("Math")
    subjects = review_store.list_subjects(topic["id"]) or [review_store.create_subject(topic["id"], "Q", "#4A90D9")]
    p = review_store.create_problem(topic["id"], subjects[0]["id"], name, 3, "text", description_text="x")
    conn = review_store._get_conn()
    conn.execute("UPDATE review_problems SET next_review_date = ? WHERE id = ?", (due.isoformat(), p["id"]))
    conn.commit()
    return p


def test_reviews_land_on_their_due_day_and_overdue_ones_on_today(isolate_review_db):
    a = _problem("a", TODAY - timedelta(days=5))
    b = _problem("b", TODAY + timedelta(days=2))
    plan = week_plan.build(TODAY, tasks=[])
    assert [p["id"] for p in plan[0]["reviews"]] == [a["id"]]
    assert [p["id"] for p in plan[2]["reviews"]] == [b["id"]]
    assert len(plan) == 7


def test_a_crowded_day_is_heavy_and_a_light_week_has_none(isolate_review_db):
    for i in range(9):
        _problem(f"p{i}", TODAY + timedelta(days=3))
    plan = week_plan.build(TODAY, tasks=[])
    assert plan[3]["heavy"] and not plan[0]["heavy"]
    assert week_plan.lightest_day(plan) == TODAY


def test_set_aside_problems_are_not_planned(isolate_review_db):
    p = _problem("a", TODAY)
    review_store.set_out_of_rotation(p["id"], True)
    assert week_plan.build(TODAY, tasks=[])[0]["reviews"] == []


def test_moving_later_costs_and_moving_earlier_is_free(isolate_review_db):
    p = _problem("a", TODAY)
    assert review_store.move_cost(TODAY, TODAY + timedelta(days=2)) == -6
    assert review_store.move_cost(TODAY, TODAY - timedelta(days=1)) == 0
    moved = review_store.move_review(p["id"], TODAY + timedelta(days=2), today=TODAY)
    assert moved["nextReviewDate"] == (TODAY + timedelta(days=2)).isoformat()
    assert moved["originalDueDate"] == TODAY.isoformat()
    again = review_store.move_review(p["id"], TODAY + timedelta(days=4), today=TODAY)
    assert again["originalDueDate"] == TODAY.isoformat()          # still judged against the first due date
    home = review_store.move_review(p["id"], TODAY, today=TODAY)
    assert home["originalDueDate"] is None                         # back on its day: nothing left to remember


def test_a_review_cannot_be_pushed_more_than_a_week(isolate_review_db):
    p = _problem("a", TODAY)
    assert review_store.move_review(p["id"], TODAY + timedelta(days=8), today=TODAY) is None
    assert review_store.move_review(p["id"], TODAY + timedelta(days=7), today=TODAY) is not None


def test_a_moved_review_is_scored_against_its_first_due_date(isolate_review_db):
    import score_store
    p = _problem("a", date.today() - timedelta(days=2))
    review_store.move_review(p["id"], date.today(), today=date.today())
    conn = review_store._get_conn()
    conn.execute("UPDATE review_problems SET review_count = 1 WHERE id = ?", (p["id"],))
    conn.commit()
    review_store.finish_review_for_problem(p["id"], 60, True, 3)
    events = [e for e in score_store.load()["events"] if e["kind"] == "review"]
    assert len(events) == 1 and events[0]["points"] == 6 - 6      # two days after the original due date


def test_the_week_page_snaps_a_dropped_review_to_its_new_day(isolate_review_db, qtbot, monkeypatch):
    from qt_ui.week_dialog import WeekDialog
    p = _problem("a", date.today())
    tab = WeekDialog()
    qtbot.addWidget(tab)
    day = date.today() + timedelta(days=2)
    tab._moved(p, day)
    qtbot.waitUntil(lambda: review_store.get_problem(p["id"])["nextReviewDate"] == day.isoformat(), timeout=2000)
    assert "1 review" in tab._note.text() or "review" in tab._note.text()


def test_the_page_says_when_a_move_is_too_far(isolate_review_db, qtbot):
    from qt_ui.week_dialog import WeekDialog
    p = _problem("a", date.today())
    tab = WeekDialog()
    qtbot.addWidget(tab)
    tab._moved(p, date.today() + timedelta(days=9))
    assert "week" in tab._hint.text()
