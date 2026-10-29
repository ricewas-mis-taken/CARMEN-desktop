"""A task's history: only its own sessions, with honest totals."""
import session_history
from qt_ui import task_history


def _session(task_id, start, end, end_type="manual", violations=0, session_id=None):
    return {"sessionId": session_id or f"{task_id}-{start}", "eventId": task_id, "eventTitle": "Study",
            "source": "task", "startTime": start, "endTime": end, "endType": end_type, "lockMode": "soft",
            "violationCount": violations, "violationLog": []}


def test_only_this_tasks_sessions_are_listed():
    sessions = [_session("a", "2026-10-01T10:00:00", "2026-10-01T10:30:00"),
                _session("b", "2026-10-01T11:00:00", "2026-10-01T11:10:00")]
    assert [s["eventId"] for s in task_history.task_sessions("a", sessions)] == ["a"]
    assert task_history.task_sessions(None, sessions) == []


def test_summary_totals():
    sessions = [_session("a", "2026-10-01T10:00:00", "2026-10-01T10:30:00"),
                _session("a", "2026-10-01T15:00:00", "2026-10-01T15:10:00", end_type="nuclear", violations=2),
                _session("a", "2026-10-03T09:00:00", "2026-10-03T09:20:00")]
    summary = task_history.task_summary(sessions)
    assert summary["sessions"] == 3
    assert summary["seconds"] == 60 * 60
    assert summary["longest"] == 30 * 60
    assert summary["ended_early"] == 1 and summary["ran_to_the_end"] == 2
    assert summary["active_days"] == 2
    assert summary["violations"] == 2 and summary["clean"] == 2
    assert str(summary["first"]) == "2026-10-01" and str(summary["last"]) == "2026-10-03"


def test_summary_of_a_task_with_no_sessions_is_empty():
    summary = task_history.task_summary([])
    assert summary["sessions"] == 0 and summary["seconds"] == 0 and summary["first"] is None


def test_the_window_lists_this_tasks_sessions(qtbot, isolate_state):
    session_history.append_entry(_session("a", "2026-10-01T10:00:00", "2026-10-01T10:30:00"))
    session_history.append_entry(_session("b", "2026-10-01T11:00:00", "2026-10-01T11:10:00"))
    dialog = task_history.TaskHistoryDialog({"id": "a", "name": "Calculus", "color": "#5B8DEF"})
    qtbot.addWidget(dialog)
    assert len(dialog.sessions) == 1
    assert dialog.tiles[0].value.text() == "30m"
    assert dialog.isVisible()


def test_the_window_for_an_untouched_task_says_so(qtbot, isolate_state):
    dialog = task_history.TaskHistoryDialog({"id": "zzz", "name": "New", "color": "#5B8DEF"})
    qtbot.addWidget(dialog)
    assert dialog.sessions == []
    assert dialog.tiles[1].value.text() == "0"
