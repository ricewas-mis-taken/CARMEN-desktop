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


def test_streak_counts_back_from_today_or_yesterday():
    d = date(2026, 10, 14)
    days = [(d - __import__("datetime").timedelta(days=i), m) for i, m in reversed(list(enumerate([30, 20, 10, 0, 40])))]
    # oldest first: 40, 0, 10, 20, 30 -> today (30), 20, 10 then a gap
    assert home.streak_days(days) == 3
    empty_today = days[:-1] + [(d, 0)]
    assert home.streak_days(empty_today) == 2      # today still empty: count back from yesterday
    assert home.streak_days([(d, 0)]) == 0


def test_grid_is_eight_weeks_ending_this_week():
    today = date(2026, 10, 14)                       # a Wednesday
    columns = home.grid_cells([], today)
    assert len(columns) == 8 and all(len(c) == 7 for c in columns)
    assert columns[-1][2][0] == today                # Mon, Tue, then Wed
    assert columns[-1][3][0] is None                 # tomorrow is not drawn
    assert columns[0][0][0] == date(2026, 8, 24)


def test_home_shows_a_running_session_with_pause_and_resume(qtbot, isolate_state, tmp_path, monkeypatch):
    import session_manager as sm
    monkeypatch.setattr(tasks_store, "TASKS_PATH", str(tmp_path / "tasks.json"))
    sm.start_session(25, "soft", [], [], source="task", event_id="a", event_title="Maths")
    tab = home.HomeTab()
    qtbot.addWidget(tab)
    tab.show()
    labels = {b.text() for b in tab.findChildren(home.QPushButton)}
    assert "Pause" in labels and "Open Focus" in labels and "Start a session" not in labels
    next(b for b in tab.findChildren(home.QPushButton) if b.text() == "Pause").click()
    assert sm.get_status()["isPaused"]
    assert "Resume" in {b.text() for b in tab.findChildren(home.QPushButton)}


def test_quick_capture_adds_a_quicknote_bullet(qtbot, isolate_state, tmp_path, monkeypatch):
    import quicknote_store
    saved = []
    monkeypatch.setattr(quicknote_store, "load", lambda: (["old", ""], "x"))
    monkeypatch.setattr(quicknote_store, "save", lambda items: saved.append(items) or True)
    tab = home.HomeTab()
    qtbot.addWidget(tab)
    edit = tab.findChild(home.WrapEdit)
    edit.setText("buy milk")
    edit.returnPressed.emit()
    assert saved == [["old", "buy milk"]]
    assert edit.text() == ""
