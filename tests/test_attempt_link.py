"""Saving a session from the history as an attempt at a problem."""
import attempt_link
import review_store
import session_history


def _setup():
    topic = review_store.create_topic("USACO")
    subject = review_store.create_subject(topic["id"], "Complete Search", "#4A90D9")
    problem = review_store.create_problem(
        topic["id"], subject["id"], "Creating Strings", stars=1, description_type="text", description_text="x")
    return topic, subject, problem


def _first_attempt():
    return {"startTime": "2026-11-23T10:54:41", "endTime": "2026-11-23T11:20:41", "endType": "manual",
            "source": "review", "reviewProblemName": "First attempt", "sessionId": "abc", "violationLog": []}


def test_a_first_attempt_is_saved_onto_a_problem_and_marked(isolate_review_db):
    _topic, _subject, problem = _setup()
    entry = _first_attempt()
    session_history.append_entry(entry)
    assert attempt_link.linkable(entry)

    saved = attempt_link.link(entry, problem["id"], self_solved=True, shakiness=3)

    assert saved["reviewCount"] == 1
    assert review_store.list_sessions(problem["id"])[0]["durationSeconds"] == 26 * 60
    filed = session_history.load_all(include_beside=True)[0]
    assert filed["linkedProblemId"] == problem["id"] and filed["reviewProblemName"] == "Creating Strings"
    assert not attempt_link.linkable(filed)           # never saved twice
    assert attempt_link.link(filed, problem["id"]) is None


def test_a_plain_focus_session_can_be_saved_but_a_task_session_cannot(isolate_review_db):
    plain = {"startTime": "2026-11-23T09:00:00", "endTime": "2026-11-23T09:30:00", "source": None}
    assert attempt_link.linkable(plain)
    assert not attempt_link.linkable(dict(plain, source="task"))
    assert not attempt_link.linkable(dict(plain, source="review", reviewProblemName="Photoshoot"))


def test_a_missing_problem_saves_nothing(isolate_review_db):
    entry = _first_attempt()
    session_history.append_entry(entry)
    assert attempt_link.link(entry, 9999) is None
    assert "linkedProblemId" not in session_history.load_all(include_beside=True)[0]
