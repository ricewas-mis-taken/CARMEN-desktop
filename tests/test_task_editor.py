"""Widget-level tests for the task edit dialog (qt_ui/task_editor.py) --
specifically that editing a task's lock mode/blocklist/domains while that
exact task's own session is actively running applies the change to the live
session immediately, not just to the task's stored template for next time."""
import pytest

import session_manager
import tasks_store
import qt_ui.task_editor as task_editor


@pytest.fixture
def isolate_tasks(tmp_path, monkeypatch):
    monkeypatch.setattr(tasks_store, "TASKS_PATH", str(tmp_path / "tasks.json"))
    yield


def _make_task(**overrides):
    data = {"name": "School", "targetMinutes": 45, "lockMode": "soft", "processBlocklist": ["discord.exe"]}
    data.update(overrides)
    return tasks_store.create_task(data)


def test_editing_lock_mode_while_this_tasks_session_is_active_updates_it_live(qtbot, isolate_tasks, isolate_state):
    task = _make_task()
    session_manager.start_session(
        45, "soft", ["discord.exe"], [], source="task", event_id=task["id"], event_title=task["name"],
    )

    win = task_editor._TaskEditor(task, on_saved=None)
    qtbot.addWidget(win)
    win._hard_radio.setChecked(True)
    win._save()

    assert session_manager.get_status()["lockMode"] == "hard"
    # The task's own stored template must also reflect the change, not just
    # the live session -- unlike qt_ui/picker_dialogs.py's "Edit Session
    # Rules" dialog, which only ever touches the live session.
    saved = next(t for t in tasks_store.load_tasks() if t["id"] == task["id"])
    assert saved["lockMode"] == "hard"


def test_editing_a_different_tasks_session_does_not_touch_the_active_one(qtbot, isolate_tasks, isolate_state):
    running_task = _make_task(name="School")
    other_task = _make_task(name="Chores", color="#e53935")
    session_manager.start_session(
        45, "soft", ["discord.exe"], [], source="task", event_id=running_task["id"], event_title="School",
    )

    win = task_editor._TaskEditor(other_task, on_saved=None)
    qtbot.addWidget(win)
    win._hard_radio.setChecked(True)
    win._save()

    assert session_manager.get_status()["lockMode"] == "soft"


def test_editing_task_with_no_active_session_only_updates_the_template(qtbot, isolate_tasks, isolate_state):
    task = _make_task()

    win = task_editor._TaskEditor(task, on_saved=None)
    qtbot.addWidget(win)
    win._hard_radio.setChecked(True)
    win._save()

    assert not session_manager.is_active()
    saved = next(t for t in tasks_store.load_tasks() if t["id"] == task["id"])
    assert saved["lockMode"] == "hard"
