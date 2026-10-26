from datetime import date, datetime

import pytest

import review_store
import tasks_store
import theme
import qt_ui.home_tab as home

pytestmark = pytest.mark.usefixtures("isolate_review_db")


def test_greeting_by_hour():
    assert home.greeting(datetime(2026, 1, 1, 3)) == "Still up"
    assert home.greeting(datetime(2026, 1, 1, 9)) == "Good morning"
    assert home.greeting(datetime(2026, 1, 1, 14)) == "Good afternoon"
    assert home.greeting(datetime(2026, 1, 1, 21)) == "Good evening"


def test_summary_orders_open_tasks_by_time_left(isolate_state, tmp_path, monkeypatch):
    monkeypatch.setattr(tasks_store, "TASKS_PATH", str(tmp_path / "tasks.json"))
    tasks_store.create_task({"name": "Small", "targetMinutes": 20, "color": "#e53935"})
    tasks_store.create_task({"name": "Big", "targetMinutes": 90, "color": "#43a047"})
    data = home.summary(tasks_store.load_tasks(), [], 2, date.today())
    assert [i["task"]["name"] for i in data["open"]] == ["Big", "Small"]
    assert data["due_reviews"] == 2 and len(data["week"]) == 7


def test_home_tab_renders_and_navigates(qtbot, isolate_state, tmp_path, monkeypatch):
    monkeypatch.setattr(tasks_store, "TASKS_PATH", str(tmp_path / "tasks.json"))
    monkeypatch.setattr(theme, "load_setting", lambda: {"name": "Paper"})
    tasks_store.create_task({"name": "Chinese", "targetMinutes": 45, "color": "#e53935"})
    went = []
    tab = home.HomeTab(on_navigate=went.append)
    qtbot.addWidget(tab)
    tab.show()
    buttons = [b for b in tab.findChildren(home.QPushButton) if b.text() == "Start a session"]
    assert buttons
    buttons[0].click()
    assert went == ["focus"]


def test_options_dialog_saves_a_theme(qtbot, monkeypatch):
    import qt_ui.options_dialog as options
    saved = []
    monkeypatch.setattr(theme, "load_setting", lambda: saved[-1] if saved else {"name": "Paper"})
    monkeypatch.setattr(theme, "save_setting", saved.append)
    monkeypatch.setattr(options, "reapply_stylesheet", lambda: None)
    dialog = options.OptionsDialog()
    qtbot.addWidget(dialog)
    dialog._choose("Moss")
    assert saved == [{"name": "Moss"}]


def test_home_survives_every_window_width(qtbot, isolate_state, tmp_path, monkeypatch):
    monkeypatch.setattr(tasks_store, "TASKS_PATH", str(tmp_path / "tasks.json"))
    tasks_store.create_task({"name": "A very long task name " * 6, "targetMinutes": 45, "color": "#e53935"})
    tab = home.HomeTab()
    qtbot.addWidget(tab)
    tab.show()
    for width in (1400, 880, 900, 520, 1000):
        tab.resize(width, 600)
        qtbot.wait(20)
        assert tab.minimumSizeHint().width() == 0
        assert tab._body.width() <= width   # never wider than the window: no sideways scrolling
