from datetime import date, timedelta

import pytest

import board_store
import plan_ml
import plan_store
import quicknote_store
from qt_ui import plan_dialog

DAY = date.today() + timedelta(days=1)


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(plan_ml, "EXAMPLES_PATH", str(tmp_path / "ex.json"))
    monkeypatch.setattr(plan_ml, "REPORTS_PATH", str(tmp_path / "rep.jsonl"))
    monkeypatch.setattr(plan_ml, "_model", None)
    monkeypatch.setattr(plan_store, "PATH", str(tmp_path / "plans.json"))
    monkeypatch.setattr(plan_store, "NOTE_META_PATH", str(tmp_path / "plan_notes.json"))
    monkeypatch.setattr(board_store, "BOARD_PATH", str(tmp_path / "board.json"))
    monkeypatch.setattr(quicknote_store, "PATH", str(tmp_path / "quicknote.json"))


def _dialog(qtbot):
    dialog = plan_dialog.PlanDialog(DAY)
    qtbot.addWidget(dialog)
    return dialog


def _type(dialog, row, text):
    row.edit.setText(text)
    row.reclassify()


def test_a_line_gets_a_guess_and_minutes(qtbot):
    dialog = _dialog(qtbot)
    row = dialog._rows[-1]
    _type(dialog, row, "write essay 45 min")
    assert row.kind() == "task" and row.minutes() == 45
    assert not row.chip.isHidden() and "45 min" in row.chip.text()


def test_enter_moves_to_the_next_line_and_makes_one_when_needed(qtbot):
    dialog = _dialog(qtbot)
    first = dialog._rows[-1]
    _type(dialog, first, "write essay 45 min")
    dialog._on_enter(first)
    assert len(dialog._rows) == 2 and dialog._rows[1].text() == ""


def test_the_person_can_change_the_kind_minutes_and_tags(qtbot):
    dialog = _dialog(qtbot)
    row = dialog._rows[-1]
    _type(dialog, row, "garden redesign")
    row.set_kind("board")
    row.toggle_tag("creative")
    assert row.entry()["kind"] == "board" and row.entry()["tags"] == ["creative"]
    row.set_kind("task")
    row.set_minutes(90)
    assert row.entry()["minutes"] == 90 and row.sure()


def test_saving_files_everything_and_reports_back(qtbot):
    saved = []
    dialog = plan_dialog.PlanDialog(DAY, on_saved=saved.extend)
    qtbot.addWidget(dialog)
    _type(dialog, dialog._rows[-1], "write essay 45 min")
    second = dialog.add_row()
    _type(dialog, second, "is the library open?")
    dialog.save()
    assert [i["kind"] for i in saved] == ["task", "note"]
    assert plan_store.has_plan(DAY) and "is the library open?" in quicknote_store.load()[0]


def test_an_empty_plan_is_not_saved(qtbot):
    dialog = _dialog(qtbot)
    dialog.save()
    assert "at least one" in dialog._status.text() and not plan_store.has_plan(DAY)


def test_what_was_not_done_yesterday_can_be_brought_over(qtbot):
    yesterday = DAY - timedelta(days=1)
    plan_store.save_plan(yesterday, [{"text": "old job 30 min", "kind": "task", "minutes": 30, "tags": []}])
    dialog = _dialog(qtbot)
    buttons = [dialog._carry.itemAt(i).widget() for i in range(dialog._carry.count()) if dialog._carry.itemAt(i).widget()]
    bring = next(b for b in buttons if b.text().startswith("+"))
    bring.click()
    assert any(r.text().startswith("old job") for r in dialog._rows)


def test_reporting_a_wrong_guess_teaches_it_at_once(qtbot, monkeypatch):
    monkeypatch.setattr(plan_dialog.QInputDialog, "getText", staticmethod(lambda *a, **k: ("only a reminder", True)))
    dialog = _dialog(qtbot)
    row = dialog._rows[-1]
    _type(dialog, row, "fix bike")
    row.set_kind("note")
    row.report_wrong_guess()
    assert plan_ml.reports()[0]["wanted"] == "note" and plan_ml.predict("fix bike")["label"] == "note"


def test_reopening_a_saved_plan_shows_its_lines(qtbot):
    plan_store.save_plan(DAY, [{"text": "write essay 45 min", "kind": "task", "minutes": 45, "tags": ["study"]}])
    dialog = _dialog(qtbot)
    assert dialog._rows[0].text().startswith("write essay") and dialog._rows[0].kind() == "task"


def test_only_one_plan_window_opens(qtbot):
    first = plan_dialog.open_plan(DAY)
    qtbot.addWidget(first)
    assert plan_dialog.open_plan(DAY) is first
    first.close()
    plan_dialog._open.discard(first)
