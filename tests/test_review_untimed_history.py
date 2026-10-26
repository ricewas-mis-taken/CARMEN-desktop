"""A review that ran with no session of its own (another session was running, so none could
start) still lands in Session History and counts toward its task."""
from datetime import date, datetime

import pytest

import qt_ui.review_tab as review_tab
import review_store
import session_history
import session_manager as sm
import tasks_store

pytestmark = pytest.mark.usefixtures("isolate_review_db")


@pytest.fixture
def setup(isolate_state, tmp_path, monkeypatch):
    monkeypatch.setattr(tasks_store, "TASKS_PATH", str(tmp_path / "tasks.json"))
    task = tasks_store.create_task({"name": "usaco", "color": "#112233", "targetMinutes": 60})
    topic = review_store.create_topic("USACO")["id"]
    review_store.update_topic_link(topic, task["id"])
    subject = review_store.create_subject(topic, "Sorting", "#445566")
    problem = review_store.create_problem(topic, subject["id"], "Kayaking", 3, "text", description_text="")
    return task, topic, problem


def test_an_untimed_review_is_filed_and_counts_for_the_task(setup):
    task, topic, problem = setup
    entry = review_tab._file_untimed_review(problem, 600)
    assert entry["source"] == "review" and entry["eventId"] == task["id"]
    assert entry["eventTitle"] == "usaco - Sorting review" and entry["reviewProblemName"] == "Kayaking"
    history = session_history.load_all()
    assert [h["sessionId"] for h in history] == [entry["sessionId"]]
    assert tasks_store.logged_seconds_for_date(task, date.today(), history) == pytest.approx(600, abs=1)


def test_a_first_attempt_is_filed_as_one(setup):
    task, topic, _ = setup
    entry = review_tab._file_untimed_review({"id": None, "name": "your new problem", "topicId": topic}, 300)
    assert entry["eventTitle"] == "usaco - first attempt"
    assert sm.is_first_attempt(entry | {"reviewProblemId": None})


def test_not_filed_when_the_same_tasks_session_already_counts_it(setup):
    task, topic, problem = setup
    sm.start_session(30, "soft", [], [], source="task", event_id=task["id"], event_title="usaco")
    assert review_tab._file_untimed_review(problem, 600) is None
    assert session_history.load_all() == []


def test_filed_when_some_other_session_was_running(setup):
    task, topic, problem = setup
    sm.start_session(30, "soft", [], [], source="manual")
    assert review_tab._file_untimed_review(problem, 600) is not None


def test_a_topic_with_no_linked_task_has_nothing_to_count_toward(isolate_state):
    topic = review_store.create_topic("Loose")["id"]
    subject = review_store.create_subject(topic, "S", "#778899")
    problem = review_store.create_problem(topic, subject["id"], "p", 3, "text", description_text="")
    assert review_tab._file_untimed_review(problem, 600) is None


def test_zero_seconds_is_not_filed(setup):
    assert review_tab._file_untimed_review(setup[2], 0) is None
