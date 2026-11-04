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


def test_code_that_holds_live_state_is_left_for_the_next_start():
    result = hot_update.swap(["session_manager.py", "main.py", "tests/test_x.py", "styles.qss"])
    assert result["live"] == [] and set(result["later"]) == {"session_manager.py", "main.py", "styles.qss"}


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
