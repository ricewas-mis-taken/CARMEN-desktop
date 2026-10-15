"""Taskbar badges / flashing are switched off for the sessions that ask for it
and put back exactly as the user had them -- never touching the real registry."""
import json

import pytest

import qt_ui.picker_dialogs as picker_dialogs
import qt_ui.task_editor as task_editor
import session_manager as sm
import taskbar_quiet
import tasks_store


@pytest.fixture
def registry(tmp_path, monkeypatch):
    """A fake Settings app: {"badges": on?, "flashing": on?} plus a log of script runs."""
    state = {"badges": True, "flashing": True}
    runs = []

    def fake_script(want, save_path=None):
        runs.append(dict(want))
        before = dict(state)
        if save_path:
            changing = {k: before[k] for k, v in want.items() if before[k] != v}
            if changing:
                with open(save_path, "w", encoding="utf-8-sig") as f:
                    json.dump(changing, f)
        state.update(want)
        return before

    monkeypatch.setattr(taskbar_quiet, "SAVED_PATH", str(tmp_path / "saved.json"))
    monkeypatch.setattr(taskbar_quiet, "PENDING_PATH", str(tmp_path / "pending.json"))
    monkeypatch.setattr(taskbar_quiet, "LEGACY_PATHS", [str(tmp_path / "old_saved.json"), str(tmp_path / "old_user.json")])
    monkeypatch.setattr(taskbar_quiet, "_run_script", fake_script)
    monkeypatch.setattr(taskbar_quiet, "_spawn", lambda fn: fn())
    monkeypatch.setattr(taskbar_quiet, "_next_attempt", 0.0)
    monkeypatch.setattr(taskbar_quiet, "_busy", False)
    monkeypatch.setattr(taskbar_quiet, "ENABLED", True)
    state["_runs"] = runs
    return state


def _status(**kw):
    base = {"isActive": True, "isPaused": False, "isBreak": False, "hideTaskbarBadges": False, "stopTaskbarFlashing": False}
    base.update(kw)
    return base


def test_nothing_is_touched_when_a_session_did_not_ask(registry):
    taskbar_quiet.reconcile(_status())
    assert registry["badges"] and registry["flashing"]
    assert registry["_runs"] == []


def test_each_choice_switches_off_only_its_own_setting(registry):
    taskbar_quiet.reconcile(_status(hideTaskbarBadges=True))
    assert (registry["badges"], registry["flashing"]) == (False, True)
    taskbar_quiet.reconcile(_status(stopTaskbarFlashing=True))
    assert (registry["badges"], registry["flashing"]) == (True, False)


def test_values_come_back_when_the_session_ends_pauses_or_breaks(registry):
    both = dict(hideTaskbarBadges=True, stopTaskbarFlashing=True)
    for end in ({"isActive": False}, {"isPaused": True}, {"isBreak": True}):
        taskbar_quiet.reconcile(_status(**both))
        assert (registry["badges"], registry["flashing"]) == (False, False)
        taskbar_quiet.reconcile(_status(**both, **end))
        assert (registry["badges"], registry["flashing"]) == (True, True)


def test_a_setting_the_user_already_had_off_stays_off(registry):
    registry["badges"] = False
    taskbar_quiet.reconcile(_status(hideTaskbarBadges=True))
    taskbar_quiet.reconcile(_status(isActive=False))
    assert registry["badges"] is False


def test_steady_state_does_not_reopen_settings_every_tick(registry):
    taskbar_quiet.reconcile(_status(hideTaskbarBadges=True))
    registry["_runs"].clear()
    taskbar_quiet.reconcile(_status(hideTaskbarBadges=True))
    taskbar_quiet.reconcile(_status(hideTaskbarBadges=True))
    assert registry["_runs"] == []


def test_restore_all_recovers_after_a_crash(registry):
    taskbar_quiet.reconcile(_status(hideTaskbarBadges=True, stopTaskbarFlashing=True))
    # a fresh process: nothing in memory, only the saved file
    taskbar_quiet.restore_all()
    assert (registry["badges"], registry["flashing"]) == (True, True)


def test_originals_found_by_a_run_that_died_are_still_restored(registry, tmp_path):
    registry["badges"] = False                    # the script switched it off, then the app died
    (tmp_path / "pending.json").write_text(json.dumps({"badges": True}), encoding="utf-8-sig")
    taskbar_quiet.restore_all()
    assert registry["badges"] is True


def test_a_leftover_off_from_the_registry_version_is_put_back_on(registry, tmp_path):
    registry["badges"] = False
    registry["flashing"] = False
    (tmp_path / "old_saved.json").write_text(json.dumps({"badges": 0, "flashing": 0}))   # the bad "original"
    taskbar_quiet.restore_all()
    assert registry["badges"] is True and registry["flashing"] is True
    assert not (tmp_path / "old_saved.json").exists()


def test_a_failed_run_is_retried_later_not_every_tick(registry, monkeypatch):
    calls = []
    monkeypatch.setattr(taskbar_quiet, "_run_script", lambda want, save_path=None: calls.append(want))   # returns None
    taskbar_quiet.reconcile(_status(hideTaskbarBadges=True))
    taskbar_quiet.reconcile(_status(hideTaskbarBadges=True))
    assert len(calls) == 1


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


def test_parked_feature_never_touches_settings(monkeypatch):
    calls = []
    monkeypatch.setattr(taskbar_quiet, "_run_script", lambda want, save_path=None: calls.append(want))
    taskbar_quiet.reconcile(_status(hideTaskbarBadges=True, stopTaskbarFlashing=True), wait=True)
    assert taskbar_quiet.ENABLED is False and calls == []
