"""Widget-level tests for the unified checklist component
(qt_ui/checklist.py) -- used by both the blocklist picker and the event
editor's process/domain blocklist sections."""
import qt_ui.checklist as checklist


def test_initial_items_checked_state_from_checked_keys(qtbot):
    items = ["a.exe", "b.exe", "c.exe"]
    widget, checkboxes_by_key, add_row = checklist.build_checklist(items, {"b.exe"})
    qtbot.addWidget(widget)

    assert checkboxes_by_key["a.exe"][0].isChecked() is False
    assert checkboxes_by_key["b.exe"][0].isChecked() is True
    assert checkboxes_by_key["c.exe"][0].isChecked() is False


def test_checked_keys_is_case_insensitive(qtbot):
    widget, checkboxes_by_key, add_row = checklist.build_checklist(["App.EXE"], {"app.exe"})
    qtbot.addWidget(widget)
    assert checkboxes_by_key["app.exe"][0].isChecked() is True


def test_get_checked_returns_original_case_keys_from_custom_key_fn(qtbot):
    # With a custom key_fn (as the process/domain blocklist checklists use),
    # the original casing passed through key_fn is preserved. With the
    # *default* key_fn (str(item).lower()), the "original" key is already
    # lowercase by the time it reaches add_row -- that's inherited as-is
    # from the Tk version's identical default, not a bug here.
    apps = [{"process_name": "MyApp.exe"}]
    widget, checkboxes_by_key, add_row = checklist.build_checklist(
        apps, {"myapp.exe"}, key_fn=lambda a: a["process_name"], label_fn=lambda a: a["process_name"],
    )
    qtbot.addWidget(widget)
    assert checklist.get_checked(checkboxes_by_key) == ["MyApp.exe"]


def test_add_row_is_idempotent_for_duplicate_keys(qtbot):
    widget, checkboxes_by_key, add_row = checklist.build_checklist([], set())
    qtbot.addWidget(widget)
    add_row("dupe.exe", "Dupe", checked=True)
    add_row("dupe.exe", "Dupe again", checked=False)  # should be a no-op
    assert len(checkboxes_by_key) == 1
    assert checkboxes_by_key["dupe.exe"][0].isChecked() is True


def test_key_fn_and_label_fn_applied_to_dict_items(qtbot):
    apps = [{"process_name": "a.exe", "display_name": "App A"}]
    widget, checkboxes_by_key, add_row = checklist.build_checklist(
        apps, set(), key_fn=lambda a: a["process_name"], label_fn=lambda a: a["display_name"],
    )
    qtbot.addWidget(widget)
    checkbox, original_key = checkboxes_by_key["a.exe"]
    assert original_key == "a.exe"
    assert checkbox.text() == "App A"


def test_build_read_after_caller_save_action_reflects_manual_toggle(qtbot):
    # The documented contract: checkboxes_by_key is live, not a snapshot --
    # a checkbox toggled by the user after build_checklist() returns must
    # still be reflected in get_checked() when the caller reads it later.
    widget, checkboxes_by_key, add_row = checklist.build_checklist(["a.exe"], set())
    qtbot.addWidget(widget)
    assert checklist.get_checked(checkboxes_by_key) == []
    checkboxes_by_key["a.exe"][0].setChecked(True)
    assert checklist.get_checked(checkboxes_by_key) == ["a.exe"]
