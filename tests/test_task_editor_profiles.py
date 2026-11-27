"""The task editor opens at once (the installed-app scan runs in the background) and lets a task block one
Chrome/Edge profile; a started task passes those profiles on to its session."""
import time

import pytest

import session_manager
import tasks_store
from qt_ui import task_editor


@pytest.fixture
def tasks_file(tmp_path, monkeypatch, isolate_state):
    monkeypatch.setattr(tasks_store, "TASKS_PATH", str(tmp_path / "tasks.json"))
    return tmp_path


def test_the_editor_does_not_wait_for_the_app_scan(qtbot, tasks_file, monkeypatch):
    import installed_apps
    release = []

    def slow():
        while not release:
            time.sleep(0.01)
        return [{"process_name": "Game.exe", "display_name": "Game"}]
    monkeypatch.setattr(installed_apps, "list_installed_apps", slow)
    task = tasks_store.create_task({"name": "T", "targetMinutes": 30, "processBlocklist": ["Saved.exe"]})
    started = time.monotonic()
    win = task_editor._TaskEditor(task, on_saved=None)
    qtbot.addWidget(win)
    assert time.monotonic() - started < 1.0
    assert "saved.exe" in win._process_checks            # what the task blocks is there straight away
    release.append(True)
    qtbot.waitUntil(lambda: "game.exe" in win._process_checks, timeout=3000)


def test_saving_keeps_the_ticked_profiles(qtbot, tasks_file, monkeypatch):
    import window_tracker
    monkeypatch.setattr(window_tracker, "list_known_browser_profiles", lambda: [
        {"aumi": "Chrome.UserData.Profile4", "label": "Chrome - School", "is_running": True},
        {"aumi": "Chrome.UserData.Default", "label": "Chrome - Me", "is_running": False}])
    task = tasks_store.create_task({"name": "T", "targetMinutes": 30})
    win = task_editor._TaskEditor(task, on_saved=None)
    qtbot.addWidget(win)
    win._profile_checks["chrome.userdata.profile4"][0].setChecked(True)
    win._save()
    assert tasks_store.get_task(task["id"])["browserProfileBlocklist"] == ["Chrome.UserData.Profile4"]


def test_an_old_task_has_no_profiles(tasks_file):
    task = tasks_store.create_task({"name": "T", "targetMinutes": 30})
    assert task["browserProfileBlocklist"] == []


def test_update_blocklist_can_change_the_profiles_of_a_live_session(isolate_state):
    session_manager.start_session(25, "hard", [], [], blocked_browser_profiles=["A"])
    session_manager.update_blocklist([], [], blocked_browser_profiles=["B"])
    assert session_manager.get_status()["blockedBrowserProfiles"] == ["B"]
    session_manager.update_blocklist([], [])
    assert session_manager.get_status()["blockedBrowserProfiles"] == ["B"]


def test_apps_found_last_time_are_listed_before_the_new_scan_ends(qtbot, tasks_file, monkeypatch):
    import installed_apps
    task_editor._write_disk_cache([{"process_name": "Old.exe", "display_name": "Old"}])
    task_editor._app_scan_cache.update(apps=None, at=0.0, scanner=None)
    release = []

    def slow():
        while not release:
            time.sleep(0.01)
        return [{"process_name": "New.exe", "display_name": "New"}]
    monkeypatch.setattr(installed_apps, "list_installed_apps", slow)
    win = task_editor._TaskEditor(None, on_saved=None)
    qtbot.addWidget(win)
    qtbot.waitUntil(lambda: "old.exe" in win._process_checks, timeout=2000)     # no wait for the slow scan
    assert "new.exe" not in win._process_checks
    release.append(True)
    qtbot.waitUntil(lambda: "new.exe" in win._process_checks, timeout=3000)
    assert task_editor._read_disk_cache()[0]["process_name"] == "New.exe"
