from datetime import date, timedelta

import review_store


def _problem(stars=3):
    topic = review_store.create_topic("Math")
    subject = review_store.create_subject(topic["id"], "Quadratics", "#4A90D9")
    p = review_store.create_problem(topic["id"], subject["id"], "P1", stars, "text", description_text="x")
    return topic, p


def _make_due(problem_id):
    conn = review_store._get_conn()
    conn.execute("UPDATE review_problems SET next_review_date = ? WHERE id = ?",
                 ((date.today() - timedelta(days=3)).isoformat(), problem_id))
    conn.commit()


def test_a_problem_set_aside_is_never_due(isolate_review_db):
    topic, p = _problem()
    _make_due(p["id"])
    assert [x["id"] for x in review_store.list_problems(topic["id"])] == [p["id"]]
    aside = review_store.set_out_of_rotation(p["id"], True)
    assert aside["outOfRotation"] is True
    assert review_store.list_problems(topic["id"]) == []
    assert [x["id"] for x in review_store.list_problems(topic["id"], due_only=False)] == [p["id"]]


def test_bringing_it_back_does_not_return_it_already_overdue(isolate_review_db):
    topic, p = _problem()
    _make_due(p["id"])
    review_store.set_out_of_rotation(p["id"], True)
    back = review_store.set_out_of_rotation(p["id"], False)
    assert back["outOfRotation"] is False
    assert back["nextReviewDate"] > date.today().isoformat()
    assert review_store.list_problems(topic["id"]) == []


def test_unknown_problem_is_ignored(isolate_review_db):
    assert review_store.set_out_of_rotation(9999, True) is None


def test_the_row_pill_says_set_aside():
    from qt_ui.review_tab import _next_review_pill
    assert _next_review_pill({"outOfRotation": True, "nextReviewDate": "2020-01-01"})[0] == "Set aside"
