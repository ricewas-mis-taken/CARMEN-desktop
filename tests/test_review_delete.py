import pytest

import review_store


@pytest.fixture
def topic(isolate_review_db):
    return review_store.create_topic("Math")["id"]


def _problem(topic, subject_name="Algebra", color="#112233", name="p1"):
    subject = next((s for s in review_store.list_subjects(topic) if s["name"] == subject_name), None) \
        or review_store.create_subject(topic, subject_name, color)
    return review_store.create_problem(topic, subject["id"], name, 3, "text", description_text="")


def test_deleting_a_problem_removes_it_from_every_list(topic):
    problem = _problem(topic)
    assert review_store.delete_problem(problem["id"]) is True
    assert review_store.list_problems(topic, due_only=False) == []
    assert review_store.get_problem(problem["id"]) is None
    assert review_store.delete_problem(problem["id"]) is False       # already gone


def test_a_deleted_problems_name_can_be_used_again(topic):
    problem = _problem(topic)
    review_store.delete_problem(problem["id"])
    assert _problem(topic)["name"] == "p1"


def test_deleting_a_category_takes_its_problems_and_frees_its_name_and_colour(topic):
    _problem(topic, name="a")
    _problem(topic, name="b")
    subject = review_store.list_subjects(topic)[0]
    assert review_store.delete_subject(subject["id"]) == 2
    assert review_store.list_subjects(topic) == []
    assert review_store.list_problems(topic, due_only=False) == []
    review_store.create_subject(topic, "Algebra", "#112233")          # same name and colour: fine now
    assert review_store.delete_subject(999) is None


def test_deleting_a_problem_stops_a_review_of_it(topic):
    problem = _problem(topic)
    token = review_store.start_review(problem["id"])
    assert token
    review_store.delete_problem(problem["id"])
    assert review_store.get_active_review() is None


def test_deletions_are_sent_to_other_computers(topic):
    import sync_client
    problem = _problem(topic)
    review_store.delete_problem(problem["id"])
    conn = review_store._get_conn()
    sent = sync_client._gather_review_problems(conn, None)
    assert [r["is_deleted"] for r in sent] == [True]
    sent_subjects = sync_client._gather_review_subjects(conn, None)
    assert len(sent_subjects) == 1
