"""Widget-level tests for the Board tab's tag filter/hide controls
(qt_ui/board_tab.py) -- the "Hide Tags" menu and the virtual "Un-labeled"
tag for tasks with no tags."""
import pytest

import board_store
import qt_ui.board_tab as board_tab


@pytest.fixture
def isolate_board(tmp_path, monkeypatch):
    monkeypatch.setattr(board_store, "BOARD_PATH", str(tmp_path / "board.json"))
    yield


def test_shopping_is_a_preset_tag():
    tag = board_store.PRESET_TAGS_BY_ID.get("shopping")
    assert tag is not None
    assert tag["label"] == "Shopping"


def test_unlabeled_tag_is_not_assignable():
    assert "unlabeled" not in board_store.PRESET_TAGS_BY_ID


def test_card_marks_unlabeled_tasks_via_background_not_a_pill(qtbot, isolate_board):
    task = board_store.create_task("Bare task", importance=5)
    card = board_tab._BoardCard(task, on_changed=lambda: None)
    qtbot.addWidget(card)
    labels = [w.text() for w in card.findChildren(board_tab.QLabel)]
    assert "Un-labeled" not in labels
    assert board_tab.UNLABELED_TAG["bg"] in card.styleSheet()


def test_card_with_tags_gets_no_unlabeled_background_tint(qtbot, isolate_board):
    task = board_store.create_task("Tagged task", importance=5, tags=["quick"])
    card = board_tab._BoardCard(task, on_changed=lambda: None)
    qtbot.addWidget(card)
    labels = [w.text() for w in card.findChildren(board_tab.QLabel)]
    assert "Un-labeled" not in labels
    assert "Quick" in labels
    assert card.styleSheet() == ""


def test_hiding_a_tag_removes_matching_tasks_from_refresh(qtbot, isolate_board):
    board_store.create_task("Shop for milk", importance=5, tags=["shopping"])
    board_store.create_task("Plan trip", importance=5, tags=["long-term"])

    tab = board_tab.BoardTab()
    qtbot.addWidget(tab)
    assert tab._list_layout.count() == 2

    tab._toggle_hidden_tag("shopping", True)
    assert tab._list_layout.count() == 1
    remaining = tab._list_layout.itemAt(0).widget()
    assert remaining._task["name"] == "Plan trip"

    tab._toggle_hidden_tag("shopping", False)
    assert tab._list_layout.count() == 2


def test_hiding_unlabeled_removes_tagless_tasks(qtbot, isolate_board):
    board_store.create_task("No tag task", importance=5)
    board_store.create_task("Tagged task", importance=5, tags=["quick"])

    tab = board_tab.BoardTab()
    qtbot.addWidget(tab)
    assert tab._list_layout.count() == 2

    tab._toggle_hidden_tag("unlabeled", True)
    assert tab._list_layout.count() == 1
    remaining = tab._list_layout.itemAt(0).widget()
    assert remaining._task["name"] == "Tagged task"


def test_hide_tags_button_label_reflects_active_count(qtbot, isolate_board):
    tab = board_tab.BoardTab()
    qtbot.addWidget(tab)
    assert tab._hide_tags_button.text() == "Hide Tags"

    tab._toggle_hidden_tag("quick", True)
    assert tab._hide_tags_button.text() == "Hide Tags (1)"

    tab._toggle_hidden_tag("shopping", True)
    assert tab._hide_tags_button.text() == "Hide Tags (2)"
