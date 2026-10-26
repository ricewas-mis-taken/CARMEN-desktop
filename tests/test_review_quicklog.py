import pytest

import review_quicklog as q
import review_store


@pytest.fixture
def topic(isolate_review_db):
    return review_store.create_topic("Math")["id"]


def test_parse_miss_bangs_raise_stars():
    assert q.parse_miss("Ch7 #14") == ("Ch7 #14", 3)
    assert q.parse_miss("Ch7 #14!") == ("Ch7 #14", 4)
    assert q.parse_miss("Ch7 #14!!!") == ("Ch7 #14", 5)


def test_log_miss_creates_in_mistakes_subject(topic):
    result = q.log_miss(topic, "Ch7 #14!")
    assert result["repeat"] is False
    assert result["problem"]["subjectName"] == "Mistakes"
    assert result["problem"]["stars"] == 4


def test_log_miss_twice_bumps_stars_not_duplicates(topic):
    q.log_miss(topic, "Ch7 #14")
    again = q.log_miss(topic, "ch7 #14")
    assert again["repeat"] is True
    assert again["problem"]["stars"] == 4
    assert len(review_store.list_problems(topic, due_only=False)) == 1


def test_log_miss_empty_is_ignored(topic):
    assert q.log_miss(topic, "  !! ") is None


def test_parse_batch_lines_ranges_and_csv():
    text = "Ch 7 problems 1-3\nProof A, hard\n\nproof a\nSet 5\t12"
    assert q.parse_batch(text) == ["Ch 7 problems 1", "Ch 7 problems 2", "Ch 7 problems 3", "Proof A", "Set 5"]


def test_parse_batch_caps_and_ignores_backwards_range():
    assert len(q.parse_batch("#1-500")) == 1  # too wide to expand: kept as one line
    assert len(q.parse_batch("\n".join(f"p{i}" for i in range(300)))) == q.MAX_BATCH
    assert q.parse_batch("5-2") == ["5-2"]


def test_add_batch_skips_existing(topic):
    subject = review_store.create_subject(topic, "Algebra", "#112233")
    assert q.add_batch(topic, subject["id"], ["a", "b"]) == (2, 0)
    assert q.add_batch(topic, subject["id"], ["b", "c"]) == (1, 1)


def test_score_outcomes():
    assert q.outcome_for_score(9, 10) == (True, 4)
    assert q.outcome_for_score(7, 10) == (True, 2)
    assert q.outcome_for_score(5, 10) == (False, None)


def test_score_set_reschedules_and_rejects_nonsense(topic):
    subject = review_store.create_subject(topic, "Sets", "#334455")
    problem = review_store.create_problem(topic, subject["id"], "Set 1", 3, "text", description_text="")
    assert q.score_set(problem["id"], 11, 10) is None
    solid = q.score_set(problem["id"], 10, 10)
    assert solid["reviewCount"] == 1
    assert solid["scheduleStage"] == 1
    failed = q.score_set(problem["id"], 2, 10)
    assert failed["scheduleStage"] == 0
