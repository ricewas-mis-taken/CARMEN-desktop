from datetime import date, datetime, timedelta

import pytest
from PySide6.QtWidgets import QPushButton

import board_store
import plan_ml
import plan_store
import quicknote_store
import shutdown_model
from qt_ui import today_tab

TODAY = date.today()


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(plan_ml, "EXAMPLES_PATH", str(tmp_path / "ex.json"))
    monkeypatch.setattr(plan_ml, "REPORTS_PATH", str(tmp_path / "rep.jsonl"))
    monkeypatch.setattr(plan_ml, "_model", None)
    monkeypatch.setattr(plan_store, "PATH", str(tmp_path / "plans.json"))
    monkeypatch.setattr(plan_store, "NOTE_META_PATH", str(tmp_path / "plan_notes.json"))
    monkeypatch.setattr(board_store, "BOARD_PATH", str(tmp_path / "board.json"))
    monkeypatch.setattr(quicknote_store, "PATH", str(tmp_path / "quicknote.json"))
    monkeypatch.setattr(shutdown_model, "PATH", str(tmp_path / "shutdown.json"))


def _plan(day=TODAY):
    return plan_store.save_plan(day, [
        {"text": "write essay 45 min", "kind": "task", "minutes": 45, "tags": ["study"]},
        {"text": "gym 1h", "kind": "task", "minutes": 60, "tags": ["health"]},
        {"text": "portfolio website", "kind": "board", "tags": []},
        {"text": "buy stamps", "kind": "note", "tags": []}])


def _tab(qtbot):
    tab = today_tab.TodayTab()
    qtbot.addWidget(tab)
    return tab


def _texts(tab):
    from PySide6.QtWidgets import QLabel
    return [label.text() for label in tab.findChildren(QLabel)]


def test_an_empty_day_invites_planning(qtbot):
    tab = _tab(qtbot)
    assert any("No plan for today" in t for t in _texts(tab))
    assert tab._plan_button.text() == "Plan this day"


def test_a_plan_shows_its_tasks_and_the_other_lines(qtbot):
    _plan()
    tab = _tab(qtbot)
    texts = _texts(tab)
    assert "write essay" in texts and "gym" in texts and "portfolio website" in texts and "buy stamps" in texts
    assert "Also on the list" in texts and tab._sub.text() == "0 of 4 done"
    assert tab._plan_button.text() == "Edit plan"


def test_ticking_a_line_marks_it_done_and_the_header_follows(qtbot):
    items = _plan()
    tab = _tab(qtbot)
    tab._toggle(items[0], True)
    qtbot.waitUntil(lambda: tab._sub.text() == "1 of 4 done", timeout=2000)
    assert plan_store.load_day(TODAY)["items"][0]["done"]


def test_tomorrow_shows_the_plan_made_for_it(qtbot):
    _plan(TODAY + timedelta(days=1))
    tab = _tab(qtbot)
    assert any("No plan for today" in t for t in _texts(tab))
    tab._day_buttons.button(1).click()
    assert "write essay" in _texts(tab) and tab._sub.text() == "4 planned"
    assert not [b for b in tab.findChildren(QPushButton) if b.text() == "Start"]      # nothing to start yet


def test_a_task_past_the_usual_stop_is_flagged(qtbot):
    plan_store.save_plan(TODAY, [{"text": "huge job 8 h", "kind": "task", "minutes": 480, "tags": []},
                                 {"text": "huge job two 8 h", "kind": "task", "minutes": 480, "tags": []}])
    tab = _tab(qtbot)
    assert "Past the time you usually stop" in _texts(tab)


def test_the_menu_can_turn_a_line_into_another_kind(qtbot):
    items = _plan()
    tab = _tab(qtbot)
    tab._reclassify(items[1], "note")
    assert plan_store.load_day(TODAY)["items"][1]["kind"] == "note"
    assert plan_ml.reports()[-1]["wanted"] == "note"


def test_the_start_button_starts_a_session_of_that_length(qtbot, monkeypatch):
    calls = []
    from qt_ui import picker_dialogs
    monkeypatch.setattr(picker_dialogs, "start_manual_session", lambda minutes, lock, parent=None: calls.append((minutes, lock)))
    items = _plan()
    tab = _tab(qtbot)
    tab._start(items[0])
    assert calls and calls[0][0] == 45


def test_removing_a_line_takes_it_off_the_page(qtbot):
    items = _plan()
    tab = _tab(qtbot)
    tab._remove(items[1])
    qtbot.waitUntil(lambda: "gym" not in _texts(tab), timeout=2000)
