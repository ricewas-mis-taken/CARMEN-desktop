import importlib
import sys
import types

import pytest

import hot_update
import update_center


def test_module_names_come_from_paths():
    assert hot_update.module_for("qt_ui/home_tab.py") == "qt_ui.home_tab"
    assert hot_update.module_for("report_card.py") == "report_card"
    assert hot_update.module_for("styles.qss") is None


def test_reloading_points_other_modules_at_the_new_function(tmp_path, monkeypatch):
    (tmp_path / "hot_a.py").write_text("def f():\n    return 1\n")
    monkeypatch.syspath_prepend(str(tmp_path))
    a = importlib.import_module("hot_a")
    user = types.ModuleType("hot_user")
    user.f = a.f                                  # what `from hot_a import f` leaves behind
    sys.modules["hot_user"] = user
    try:
        (tmp_path / "hot_a.py").write_text("def f():\n    return 2\n")
        importlib.invalidate_caches()
        hot_update.reload_module("hot_a")
        assert user.f() == 2
    finally:
        sys.modules.pop("hot_user", None)
        sys.modules.pop("hot_a", None)


def test_code_that_runs_the_app_is_left_for_the_next_start():
    result = hot_update.swap(["main.py", "api_server.py", "tray.py", "tests/test_x.py", "styles.qss"])
    assert result["live"] == [] and set(result["later"]) == {"main.py", "api_server.py", "tray.py", "styles.qss"}


_V1 = """
import threading
_lock = threading.RLock()
_state = {'running': False}
LIMIT = 1
def run():
    _state['running'] = True
"""
_V2 = """
import threading
_lock = threading.RLock()
_state = {'running': False, 'paused': False}
LIMIT = 2
def run():
    _state['running'] = 'new code'
"""


def test_a_module_with_live_data_is_swapped_and_keeps_its_data(tmp_path, monkeypatch):
    (tmp_path / "hot_s.py").write_text(_V1)
    monkeypatch.syspath_prepend(str(tmp_path))
    mod = importlib.import_module("hot_s")
    monkeypatch.setattr(hot_update, "STATEFUL", hot_update.STATEFUL + ["hot_s"])
    try:
        mod.run()
        lock, state = mod._lock, mod._state
        (tmp_path / "hot_s.py").write_text(_V2)
        importlib.invalidate_caches()
        hot_update.reload_module("hot_s")
        assert mod._state is state and mod._lock is lock                # the very same objects
        assert mod._state == {"running": True, "paused": False}          # data kept, the new key given its default
        assert mod.LIMIT == 2                                            # constants come from the new code
        mod.run()
        assert mod._state["running"] == "new code"                       # and the new function runs
    finally:
        sys.modules.pop("hot_s", None)


def test_swapping_the_session_engine_leaves_a_running_session_running(isolate_state):
    import session_manager as sm
    sm.start_session(30, "soft", [], [], source="task", event_id="a", event_title="Alpha")
    result = hot_update.swap(["session_manager.py", "session_history.py"])
    assert "session_manager" in result["live"] and not result["later"]
    status = sm.get_status()
    assert status["isActive"] and status["eventTitle"] == "Alpha"
    sm.pause_session("break")
    assert sm.get_status()["isPaused"]


def test_safe_pure_modules_are_swapped_live():
    import report_card
    result = hot_update.swap(["report_card.py"])
    assert result["live"] == ["report_card"] and sys.modules["report_card"] is report_card


def test_pages_are_rebuilt_in_place_on_the_same_tab(qtbot, isolate_config):
    import qt_ui.main_window as mw
    win = mw._MainWindow()
    qtbot.addWidget(win)
    mw._win = win
    win._show_tab("tasks")
    old = win._pages["tasks"]
    try:
        result = hot_update.swap(["qt_ui/tasks_tab.py"])
        assert result["pages"] == ["tasks"]
        assert win._pages["tasks"] is not old and win._stack.currentWidget() is win._pages["tasks"]
    finally:
        mw._win = None
        win.close()
        win.deleteLater()


def test_update_center_reports_what_changed_since_start(monkeypatch):
    monkeypatch.setattr(update_center.code_watcher, "file_stamps", lambda: {"a.py": (1, 1), "b.py": (2, 2)})
    update_center.start_baseline()
    monkeypatch.setattr(update_center.code_watcher, "file_stamps", lambda: {"a.py": (1, 1), "b.py": (3, 3), "c.py": (1, 1)})
    assert update_center.changed_files() == ["b.py", "c.py"]


def test_relaunch_closes_carmen_through_its_normal_shutdown_and_remembers_the_window(isolate_config, monkeypatch):
    calls = []
    monkeypatch.setattr(update_center, "relaunch_hook", lambda: calls.append("quit"))
    monkeypatch.delenv("CARMEN_RESTARTED", raising=False)
    update_center._relaunch.clear()
    update_center.set_needs_relaunch(True)
    try:
        update_center.request_relaunch()
        assert calls == ["quit"] and update_center.relaunch_was_requested()
        import os
        assert os.environ.get("CARMEN_RESTARTED") == "1"
    finally:
        update_center._relaunch.clear()
        update_center.set_needs_relaunch(False)
        os.environ.pop("CARMEN_RESTARTED", None)
