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


def test_card_shows_no_visible_trace_of_unlabeled_status(qtbot, isolate_board):
    """"Un-labeled" is a purely backend/filtering concept -- a tagless
    task's card must look completely ordinary, no pill and no styling
    change of any kind."""
    task = board_store.create_task("Bare task", importance=5)
    card = board_tab._BoardCard(task, on_changed=lambda: None)
    qtbot.addWidget(card)
    labels = [w.text() for w in card.findChildren(board_tab.QLabel)]
    assert "Un-labeled" not in labels
    assert card.styleSheet() == ""


def test_hiding_a_tag_removes_matching_tasks_from_refresh(qtbot, isolate_board):
    board_store.create_task("Shop for milk", importance=5, tags=["shopping"])
    board_store.create_task("Plan trip", importance=5, tags=["long-term"])

    tab = board_tab.BoardTab()
    qtbot.addWidget(tab)
    assert tab._list_layout.count() - 1 == 2

    tab._toggle_hidden_tag("shopping", True)
    assert tab._list_layout.count() - 1 == 1
    remaining = tab._list_layout.itemAt(0).widget()
    assert remaining._task["name"] == "Plan trip"

    tab._toggle_hidden_tag("shopping", False)
    assert tab._list_layout.count() - 1 == 2


def test_hiding_unlabeled_removes_tagless_tasks(qtbot, isolate_board):
    board_store.create_task("No tag task", importance=5)
    board_store.create_task("Tagged task", importance=5, tags=["quick"])

    tab = board_tab.BoardTab()
    qtbot.addWidget(tab)
    assert tab._list_layout.count() - 1 == 2

    tab._toggle_hidden_tag("unlabeled", True)
    assert tab._list_layout.count() - 1 == 1
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


def test_timer_tick_keeps_open_card_when_nothing_changed(qtbot, isolate_board):
    board_store.create_task("Open me", importance=5)
    tab = board_tab.BoardTab()
    qtbot.addWidget(tab)
    card = tab._list_layout.itemAt(0).widget()
    card._expand()
    assert card._expanded

    tab._refresh_if_changed()
    assert tab._list_layout.itemAt(0).widget() is card
    assert card._expanded


def test_timer_tick_rebuilds_when_a_task_appears(qtbot, isolate_board):
    board_store.create_task("First", importance=5)
    tab = board_tab.BoardTab()
    qtbot.addWidget(tab)
    board_store.create_task("Second", importance=6)

    tab._refresh_if_changed()
    assert tab._list_layout.count() - 1 == 2


def test_add_dialog_survives_photo_deleted_before_save(qtbot, isolate_board, tmp_path):
    photo = tmp_path / "gone.png"
    photo.write_bytes(b"x")
    dialog = board_tab._AddTaskDialog(on_added=lambda _t: None)
    qtbot.addWidget(dialog)
    dialog._name_edit.setText("Has photo")
    dialog._photo_path = str(photo)
    photo.unlink()

    dialog._submit()
    assert "Could not read" in dialog._status_label.text()
    assert board_store.list_active_tasks() == []


# ------------------------------------------------- redesigned header and tiles ----

def test_importance_colours_run_from_cool_to_hot():
    cool = board_tab.importance_colors(1)
    warm = board_tab.importance_colors(5)
    hot = board_tab.importance_colors(10)
    assert len({cool, warm, hot}) == 3
    assert board_tab.importance_colors(3) == cool and board_tab.importance_colors(4) == warm
    assert board_tab.importance_colors(9) == hot and board_tab.importance_colors(7) != hot


def test_the_tile_restyles_when_importance_changes(qtbot, isolate_board):
    task = board_store.create_task("Tile", importance=2)
    card = board_tab._BoardCard(task, on_changed=lambda: None)
    qtbot.addWidget(card)
    before = card._badge.styleSheet()
    card._badge.setText("9")
    assert card._badge.styleSheet() != before
    assert board_tab.importance_colors(9)[0] in card._badge.styleSheet()


def test_clicking_a_tag_chip_shows_only_that_tag_and_all_resets(qtbot, isolate_board):
    board_store.create_task("Milk", importance=5, tags=["shopping"])
    board_store.create_task("Trip", importance=5, tags=["long-term"])
    tab = board_tab.BoardTab()
    qtbot.addWidget(tab)
    assert tab._list_layout.count() - 1 == 2 and tab._tag_chips["all"].isChecked()
    tab._tag_chips["shopping"].click()
    assert tab._list_layout.count() - 1 == 1 and tab._list_layout.itemAt(0).widget()._task["name"] == "Milk"
    assert tab._tag_chips["shopping"].isChecked() and not tab._tag_chips["all"].isChecked()
    tab._tag_chips["all"].click()
    assert tab._list_layout.count() - 1 == 2


def test_the_subtitle_counts_what_is_shown(qtbot, isolate_board):
    tab = board_tab.BoardTab()
    qtbot.addWidget(tab)
    assert tab._subtitle.text().startswith("0 tasks")
    board_store.create_task("One", importance=5, tags=["quick"])
    board_store.create_task("Two", importance=5)
    tab.refresh()
    assert tab._subtitle.text().startswith("2 tasks")
    tab._tag_chips["quick"].click()
    assert tab._subtitle.text().startswith("1 of 2 tasks")


def test_details_button_toggles_between_details_and_hide(qtbot, isolate_board):
    task = board_store.create_task("Open", importance=5)
    card = board_tab._BoardCard(task, on_changed=lambda: None)
    qtbot.addWidget(card)
    assert card._details_button.text() == "Details"
    card._expand()
    assert card._details_button.text() == "Hide"
    card._collapse()
    assert card._details_button.text() == "Details"


def test_quicknote_on_the_board_adds_ticks_and_clears(qtbot, tmp_path, monkeypatch):
    import quicknote_store
    import qt_ui.board_tab as board_tab
    saved = []
    notes = ["one", "two"]
    monkeypatch.setattr(quicknote_store, "load", lambda: (list(notes), "x"))
    monkeypatch.setattr(quicknote_store, "save", lambda items: saved.append(list(items)) or notes.__setitem__(slice(None), items) or True)
    tab = board_tab.BoardTab()
    qtbot.addWidget(tab)
    tab._note_input.setText("three")
    tab._add_note()
    assert saved[-1] == ["one", "two", "three"]
    tab._note_rows[0][0].setChecked(True)
    tab._clear_ticked()
    assert saved[-1] == ["two", "three"]
