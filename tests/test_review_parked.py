"""review_store's set-aside ("parked") reviews: paused, local to this computer,
and kept out of the one-running-review slot the cross-computer sync deals in."""
import review_store


def _problem(name="Solve it"):
    topic = review_store.create_topic("Math")
    subject = review_store.create_subject(topic["id"], "Quadratics", "#4A90D9")
    return review_store.create_problem(
        topic["id"], subject["id"], name, stars=5, description_type="text", description_text="x")


def test_parking_frees_the_running_slot_and_keeps_elapsed(isolate_review_db, isolate_state):
    problem = _problem()
    token = review_store.start_review(problem["id"])
    assert review_store.start_review(problem["id"]) is None  # one running at a time
    assert review_store.park_active_review() == token
    assert review_store.get_active_review() is None
    assert review_store.start_review(problem["id"]) is not None  # slot is free again
    parked = review_store.list_parked_reviews()
    assert [p["token"] for p in parked] == [token] and parked[0]["problemName"] == "Solve it"


def test_unpark_needs_the_slot_to_be_free(isolate_review_db, isolate_state):
    problem = _problem()
    token = review_store.start_review(problem["id"])
    review_store.park_active_review()
    other = review_store.start_review(problem["id"])
    assert not review_store.unpark_review(token)
    review_store.abandon_review(other)
    assert review_store.unpark_review(token)
    active = review_store.get_active_review()
    assert active["token"] == token and not active["isPaused"]
    assert review_store.list_parked_reviews() == []


def test_parked_review_is_not_shared_with_the_other_computer(isolate_review_db, isolate_state):
    problem = _problem()
    review_store.start_review(problem["id"])
    review_store.park_active_review()
    assert review_store.export_active_review()["review"] is None
    review_store.apply_remote_review(None, None, "2999-01-01T00:00:00+00:00")
    assert len(review_store.list_parked_reviews()) == 1


def test_parked_reviews_survive_a_restart_and_can_be_abandoned(isolate_review_db, isolate_state, monkeypatch):
    problem = _problem()
    token = review_store.start_review(problem["id"])
    review_store.park_active_review()
    monkeypatch.setattr(review_store, "_parked_reviews", {})
    monkeypatch.setattr(review_store, "_parked_loaded", False)
    assert [p["token"] for p in review_store.list_parked_reviews()] == [token]
    review_store.abandon_parked_review(token)
    assert review_store.list_parked_reviews() == []


def test_deleting_the_topic_drops_its_parked_reviews(isolate_review_db, isolate_state):
    problem = _problem()
    review_store.start_review(problem["id"])
    review_store.park_active_review()
    review_store.delete_topic(problem["topicId"])
    assert review_store.list_parked_reviews() == []
