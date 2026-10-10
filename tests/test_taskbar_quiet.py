"""Taskbar badges / flashing are switched off for the sessions that ask for it
and put back exactly as the user had them -- never touching the real registry."""
import pytest

import qt_ui.picker_dialogs as picker_dialogs
import qt_ui.task_editor as task_editor
import session_manager as sm
import taskbar_quiet
import tasks_store


@pytest.fixture
def registry(tmp_path, monkeypatch):
    values = {"TaskbarBadges": 1, "TaskbarFlashing": 1}
    log = []
    monkeypatch.setattr(taskbar_quiet, "SAVED_PATH", str(tmp_path / "saved.json"))
    monkeypatch.setattr(taskbar_quiet, "_read_value", lambda name: values.get(name))
    monkeypatch.setattr(taskbar_quiet, "_write_value", lambda name, v: (values.__setitem__(name, v), log.append((name, v))))
    monkeypatch.setattr(taskbar_quiet, "_broadcast_change", lambda: log.append("broadcast"))
    values["_log"] = log
    return values


def _status(**kw):
    base = {"isActive": True, "isPaused": False, "isBreak": False, "hideTaskbarBadges": False, "stopTaskbarFlashing": False}
    base.update(kw)
    return base


def test_nothing_is_touched_when_a_session_did_not_ask(registry):
    taskbar_quiet.reconcile(_status())
    assert registry["TaskbarBadges"] == 1 and registry["TaskbarFlashing"] == 1
    assert registry["_log"] == []


def test_each_choice_switches_off_only_its_own_setting(registry):
    taskbar_quiet.reconcile(_status(hideTaskbarBadges=True))
    assert (registry["TaskbarBadges"], registry["TaskbarFlashing"]) == (0, 1)
    taskbar_quiet.reconcile(_status(stopTaskbarFlashing=True))
    assert (registry["TaskbarBadges"], registry["TaskbarFlashing"]) == (1, 0)


def test_values_come_back_when_the_session_ends_pauses_or_breaks(registry):
    both = dict(hideTaskbarBadges=True, stopTaskbarFlashing=True)
    for end in ({"isActive": False}, {"isPaused": True}, {"isBreak": True}):
        taskbar_quiet.reconcile(_status(**both))
        assert (registry["TaskbarBadges"], registry["TaskbarFlashing"]) == (0, 0)
        taskbar_quiet.reconcile(_status(**both, **end))
        assert (registry["TaskbarBadges"], registry["TaskbarFlashing"]) == (1, 1)


def test_a_setting_the_user_already_had_off_stays_off(registry):
    registry["TaskbarBadges"] = 0
    taskbar_quiet.reconcile(_status(hideTaskbarBadges=True))
    taskbar_quiet.reconcile(_status(isActive=False))
    assert registry["TaskbarBadges"] == 0


def test_an_unset_value_is_restored_to_on(registry):
    del registry["TaskbarBadges"]
    taskbar_quiet.reconcile(_status(hideTaskbarBadges=True))
    taskbar_quiet.reconcile(_status(isActive=False))
    assert registry["TaskbarBadges"] == 1


def test_steady_state_does_not_rewrite_every_tick(registry):
    taskbar_quiet.reconcile(_status(hideTaskbarBadges=True))
    registry["_log"].clear()
    taskbar_quiet.reconcile(_status(hideTaskbarBadges=True))
    assert registry["_log"] == []


def test_restore_all_recovers_after_a_crash(registry):
    taskbar_quiet.reconcile(_status(hideTaskbarBadges=True, stopTaskbarFlashing=True))
    # a fresh process: nothing in memory, only the saved file
    taskbar_quiet.restore_all()
    assert (registry["TaskbarBadges"], registry["TaskbarFlashing"]) == (1, 1)


def test_session_carries_the_choices_and_clears_them_on_end(isolate_state):
    sm.start_session(25, "soft", [], [], hide_taskbar_badges=True, stop_taskbar_flashing=True)
    status = sm.get_status()
    assert status["hideTaskbarBadges"] and status["stopTaskbarFlashing"]
    sm.end_session()
    status = sm.get_status()
    assert not status["hideTaskbarBadges"] and not status["stopTaskbarFlashing"]


def test_choices_follow_a_session_that_waits_and_comes_back(isolate_state):
    sm.start_session(25, "soft", [], [], hide_taskbar_badges=True)
    sm.pause_session()
    sm.start_session(25, "soft", [], [])
    assert not sm.get_status()["hideTaskbarBadges"]
    sm.end_session()
    assert sm.get_status()["hideTaskbarBadges"]


def test_pomodoro_start_passes_the_choices(isolate_state):
    sm.start_pomodoro_session(25, 5, 2, "soft", [], [], stop_taskbar_flashing=True)
    assert sm.get_status()["stopTaskbarFlashing"]


def test_editing_a_running_task_applies_live(isolate_state):
    sm.start_session(25, "soft", [], [])
    sm.update_taskbar_quiet(True, False)
    status = sm.get_status()
    assert status["hideTaskbarBadges"] and not status["stopTaskbarFlashing"]


def test_new_tasks_default_to_off(tmp_path, monkeypatch):
    monkeypatch.setattr(tasks_store, "TASKS_PATH", str(tmp_path / "tasks.json"))
    task = tasks_store.create_task({"name": "x", "targetMinutes": 30})
    assert task["hideTaskbarBadges"] is False and task["stopTaskbarFlashing"] is False


def test_task_editor_saves_the_checkboxes(qtbot, tmp_path, monkeypatch, isolate_state):
    monkeypatch.setattr(tasks_store, "TASKS_PATH", str(tmp_path / "tasks.json"))
    task = tasks_store.create_task({"name": "x", "targetMinutes": 30})
    saved = []
    editor = task_editor._TaskEditor(task, on_saved=saved.append)
    qtbot.addWidget(editor)
    assert not editor._badges_check.isChecked()
    editor._badges_check.setChecked(True)
    editor._flashing_check.setChecked(True)
    editor._save()
    stored = tasks_store.get_task(task["id"])
    assert stored["hideTaskbarBadges"] and stored["stopTaskbarFlashing"]


def test_custom_session_dialog_starts_with_the_checked_boxes(qtbot, isolate_state, isolate_config):
    dialog = picker_dialogs._TimerDialog()
    qtbot.addWidget(dialog)
    dialog._badges_check.setChecked(True)
    dialog._start()
    assert sm.get_status()["hideTaskbarBadges"] and not sm.get_status()["stopTaskbarFlashing"]
