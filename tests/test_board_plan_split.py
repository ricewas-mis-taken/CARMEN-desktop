from datetime import date

import pytest
from PySide6.QtWidgets import QLabel

import board_store
import plan_ml
import plan_store
import quicknote_store
from qt_ui import board_tab


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(plan_ml, "EXAMPLES_PATH", str(tmp_path / "ex.json"))
    monkeypatch.setattr(plan_ml, "REPORTS_PATH", str(tmp_path / "rep.jsonl"))
    monkeypatch.setattr(plan_ml, "_model", None)
    monkeypatch.setattr(plan_store, "PATH", str(tmp_path / "plans.json"))
    monkeypatch.setattr(plan_store, "NOTE_META_PATH", str(tmp_path / "plan_notes.json"))
    monkeypatch.setattr(board_store, "BOARD_PATH", str(tmp_path / "board.json"))
    monkeypatch.setattr(quicknote_store, "PATH", str(tmp_path / "quicknote.json"))


def _texts(tab):
    return [label.text() for label in tab.findChildren(QLabel)]


def test_the_board_has_no_split_until_some_cards_come_from_a_plan(qtbot):
    board_store.create_task("renew passport", 8)
    tab = board_tab.BoardTab()
    qtbot.addWidget(tab)
    assert not [t for t in _texts(tab) if t.startswith("From your plan")]


def test_planned_cards_are_set_apart_from_the_rest(qtbot):
    board_store.create_task("renew passport", 8)
    plan_store.save_plan(date.today(), [{"text": "portfolio website", "kind": "board", "tags": ["creative"]}])
    tab = board_tab.BoardTab()
    qtbot.addWidget(tab)
    texts = _texts(tab)
    assert "From your plan  ·  1" in texts and "Everything else  ·  1" in texts
    assert texts.index("portfolio website") < texts.index("renew passport")


def test_every_card_without_a_tag_gets_one(qtbot):
    task = board_store.create_task("study for chemistry exam", 6)
    tab = board_tab.BoardTab()
    qtbot.addWidget(tab)
    assert "study" in board_store.get_task(task["id"])["tags"]


def test_quicknote_groups_planned_lines_and_tags_them(qtbot):
    plan_store.save_plan(date.today(), [{"text": "buy stamps", "kind": "note", "tags": ["shopping"]}])
    quicknote_store.save(["buy stamps", "call the dentist"])
    tab = board_tab.BoardTab()
    qtbot.addWidget(tab)
    texts = _texts(tab)
    assert "From your plan  ·  1" in texts and "Loose notes  ·  1" in texts
    assert "Shopping" in texts and "Admin" in texts           # the plan's tag, and one guessed for the loose line


def test_clearing_a_ticked_planned_note_marks_its_plan_line_done(qtbot):
    items = plan_store.save_plan(date.today(), [{"text": "buy stamps", "kind": "note", "tags": []}])
    tab = board_tab.BoardTab()
    qtbot.addWidget(tab)
    tab._note_rows[0][0].setChecked(True)
    tab._clear_ticked()
    assert plan_store.load_day(date.today())["items"][0]["done"] and "buy stamps" not in quicknote_store.load()[0]
