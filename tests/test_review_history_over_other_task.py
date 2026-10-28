import pytest
import qt_ui.review_tab as review_tab
import session_history
import session_manager as sm
import tasks_store

pytestmark = pytest.mark.usefixtures("isolate_review_db")


@pytest.fixture
def isolate_tasks(tmp_path, monkeypatch):
    monkeypatch.setattr(tasks_store, "TASKS_PATH", str(tmp_path / "tasks.json"))
    yield


@pytest.mark.parametrize("pomodoro", [False, True])
def test_review_over_another_tasks_running_session_lands_in_history(isolate_tasks, isolate_state, pomodoro):
    chinese = tasks_store.create_task({"name": "Chinese", "targetMinutes": 30, "color": "#112233"})
    usaco = tasks_store.create_task({"name": "usaco", "targetMinutes": 30, "color": "#445566"})
    if pomodoro:
        sm.start_pomodoro_session(25, 5, 4, "soft", [], [], source="task", event_id=chinese["id"], event_title="Chinese")
    else:
        sm.start_session(30, "soft", [], [], source="task", event_id=chinese["id"], event_title="Chinese")
    assert review_tab._make_room_for(usaco)
    sm.start_session(480, "soft", [], [], source="review", event_id=usaco["id"], event_title="usaco - x review",
                     review_problem_name="p", review_subject_name="s", review_problem_id=1)
    sm.end_session()
    titles = [s["eventTitle"] for s in session_history.load_all()]
    print("HISTORY", titles, sm.get_status()["eventTitle"])
    assert "usaco - x review" in titles
