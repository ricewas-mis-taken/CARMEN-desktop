from datetime import date, timedelta

import review_store


def _make_topic_and_subject(name="Math", subject_name="Quadratics", color="#4A90D9"):
    topic = review_store.create_topic(name)
    subject = review_store.create_subject(topic["id"], subject_name, color)
    return topic, subject


def test_create_and_list_topics(isolate_review_db):
    assert review_store.list_topics() == []
    topic = review_store.create_topic("Math")
    assert topic["name"] == "Math"
    assert review_store.list_topics() == [topic]


def test_create_and_list_subjects(isolate_review_db):
    topic, subject = _make_topic_and_subject()
    assert subject["name"] == "Quadratics"
    assert subject["color"] == "#4A90D9"
    assert review_store.list_subjects(topic["id"]) == [subject]


def test_create_problem_sets_schedule_from_scheduler(isolate_review_db):
    topic, subject = _make_topic_and_subject()
    problem = review_store.create_problem(
        topic["id"], subject["id"], "Solve x^2-5x+6", stars=5,
        description_type="text", description_text="factor it",
    )
    assert problem["scheduleStage"] == 0
    # 5-star at stage 0 -> 1 day out (see review_scheduler tests)
    assert problem["nextReviewDate"] == (date.today() + timedelta(days=1)).isoformat()
    assert problem["reviewCount"] == 0
    assert problem["subjectName"] == "Quadratics"
    assert problem["subjectColor"] == "#4A90D9"


def test_list_problems_due_only_filters_future_dates(isolate_review_db):
    topic, subject = _make_topic_and_subject()
    due_soon = review_store.create_problem(
        topic["id"], subject["id"], "Due soon", stars=1, description_type="text", description_text="x",
    )
    not_due = review_store.create_problem(
        topic["id"], subject["id"], "Not due", stars=1, description_type="text", description_text="x",
    )

    all_problems = review_store.list_problems(topic["id"], due_only=False)
    assert {p["id"] for p in all_problems} == {due_soon["id"], not_due["id"]}

    # 1-star problems land 1 day out by default (not due today) -- pull one
    # back to today so due_only actually has something to filter between.
    conn = review_store._get_conn()
    conn.execute(
        "UPDATE review_problems SET next_review_date = ? WHERE id = ?",
        (date.today().isoformat(), due_soon["id"]),
    )
    conn.commit()

    due_problems = review_store.list_problems(topic["id"], due_only=True)
    assert [p["id"] for p in due_problems] == [due_soon["id"]]


def test_list_problems_sorted_by_next_review_date_then_stars_desc(isolate_review_db):
    topic, subject = _make_topic_and_subject()
    conn = review_store._get_conn()

    low = review_store.create_problem(
        topic["id"], subject["id"], "Low stars", stars=2, description_type="text", description_text="x",
    )
    high = review_store.create_problem(
        topic["id"], subject["id"], "High stars", stars=4, description_type="text", description_text="x",
    )
    same_date = date.today().isoformat()
    for p in (low, high):
        conn.execute("UPDATE review_problems SET next_review_date = ? WHERE id = ?", (same_date, p["id"]))
    conn.commit()

    ordered = review_store.list_problems(topic["id"], due_only=True)
    assert [p["id"] for p in ordered] == [high["id"], low["id"]]


def test_start_review_unknown_problem_returns_none(isolate_review_db):
    assert review_store.start_review(999999) is None


def test_finish_review_updates_counters_and_reschedules(isolate_review_db):
    topic, subject = _make_topic_and_subject()
    problem = review_store.create_problem(
        topic["id"], subject["id"], "Solve it", stars=3, description_type="text", description_text="x",
    )

    token = review_store.start_review(problem["id"])
    assert token is not None

    updated = review_store.finish_review(token)
    assert updated["reviewCount"] == 1
    assert updated["lastReviewedAt"] is not None
    assert updated["fastestTimeSeconds"] is not None
    assert updated["scheduleStage"] == 1
    # 3-star at stage 1 -> round(4 * 0.70) = 3 days out
    assert updated["nextReviewDate"] == (date.today() + timedelta(days=3)).isoformat()

    # Token is single-use.
    assert review_store.finish_review(token) is None


def test_finish_review_records_first_attempt_once(isolate_review_db):
    topic, subject = _make_topic_and_subject()
    problem = review_store.create_problem(
        topic["id"], subject["id"], "Solve it", stars=3, description_type="text", description_text="x",
    )
    assert problem["firstAttemptSeconds"] is None

    token = review_store.start_review(problem["id"])
    first = review_store.finish_review(token, self_solved=True, shakiness=4)
    assert first["firstAttemptSeconds"] is not None
    assert first["firstAttemptShakiness"] == 4
    assert first["firstAttemptSelfSolved"] is True

    # A second attempt must not overwrite the first attempt's stats.
    token2 = review_store.start_review(problem["id"])
    second = review_store.finish_review(token2, self_solved=True, shakiness=1)
    assert second["firstAttemptShakiness"] == 4


def test_fastest_time_only_updates_when_faster(isolate_review_db, monkeypatch):
    topic, subject = _make_topic_and_subject()
    problem = review_store.create_problem(
        topic["id"], subject["id"], "Solve it", stars=3, description_type="text", description_text="x",
    )

    import datetime as dt_module

    class _FakeDatetime(dt_module.datetime):
        _now = dt_module.datetime(2026, 1, 1, 12, 0, 0)

        @classmethod
        def now(cls, tz=None):
            return cls._now

    monkeypatch.setattr(review_store, "datetime", _FakeDatetime)

    _FakeDatetime._now = dt_module.datetime(2026, 1, 1, 12, 0, 0)
    token = review_store.start_review(problem["id"])
    _FakeDatetime._now = dt_module.datetime(2026, 1, 1, 12, 0, 30)
    first = review_store.finish_review(token)
    assert first["fastestTimeSeconds"] == 30

    _FakeDatetime._now = dt_module.datetime(2026, 1, 1, 13, 0, 0)
    token2 = review_store.start_review(problem["id"])
    _FakeDatetime._now = dt_module.datetime(2026, 1, 1, 13, 1, 0)
    second = review_store.finish_review(token2)
    # 60s is slower than the 30s fastest -- must not overwrite it.
    assert second["fastestTimeSeconds"] == 30


def test_fastest_time_stands_in_with_checked_answer_until_real_solve(isolate_review_db):
    topic, subject = _make_topic_and_subject()
    problem = review_store.create_problem(
        topic["id"], subject["id"], "Solve it", stars=3, description_type="text", description_text="x",
    )

    # No solve yet -- a checked-answer attempt should still populate
    # fastest_time_seconds instead of leaving it blank.
    token = review_store.start_review(problem["id"])
    first = review_store.finish_review(token, self_solved=False)
    assert first["fastestTimeSeconds"] is not None
    assert first["fastestTimeIsSolved"] is False

    # Another checked-answer attempt keeps the stand-in as a checked-answer
    # time (not flipped to "solved" just because it recorded a duration).
    token2 = review_store.start_review(problem["id"])
    second = review_store.finish_review(token2, self_solved=False)
    assert second["fastestTimeIsSolved"] is False

    # A genuine solve always takes over from the checked-answer estimate,
    # even if the estimate happened to be numerically "faster".
    token3 = review_store.start_review(problem["id"])
    third = review_store.finish_review(token3, self_solved=True, shakiness=2)
    assert third["fastestTimeIsSolved"] is True

    # Once a genuine solve is on record, further checked-answer attempts
    # must not overwrite it.
    token4 = review_store.start_review(problem["id"])
    fourth = review_store.finish_review(token4, self_solved=False)
    assert fourth["fastestTimeSeconds"] == third["fastestTimeSeconds"]
    assert fourth["fastestTimeIsSolved"] is True


def test_save_photo_bytes_copies_into_photos_dir(isolate_review_db):
    path = review_store.save_photo_bytes(b"fake-image-bytes", "original.PNG")
    assert path.startswith(review_store.PHOTOS_DIR)
    assert path.endswith(".png")
    with open(path, "rb") as f:
        assert f.read() == b"fake-image-bytes"
