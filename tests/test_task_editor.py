"""Widget-level tests for the task edit dialog (qt_ui/task_editor.py) --
specifically that editing a task's lock mode/blocklist/domains while that
exact task's own session is actively running applies the change to the live
session immediately, not just to the task's stored template for next time."""
import pytest
from PySide6.QtWidgets import QMessageBox

import session_manager
import tasks_store
import qt_ui.task_editor as task_editor


@pytest.fixture
def isolate_tasks(tmp_path, monkeypatch):
    monkeypatch.setattr(tasks_store, "TASKS_PATH", str(tmp_path / "tasks.json"))
    yield


def _make_task(**overrides):
    data = {"name": "School", "targetMinutes": 45, "lockMode": "soft", "processBlocklist": ["discord.exe"]}
    data.update(overrides)
    return tasks_store.create_task(data)


def test_editing_lock_mode_while_this_tasks_session_is_active_updates_it_live(qtbot, isolate_tasks, isolate_state):
    task = _make_task()
    session_manager.start_session(
        45, "soft", ["discord.exe"], [], source="task", event_id=task["id"], event_title=task["name"],
    )

    win = task_editor._TaskEditor(task, on_saved=None)
    qtbot.addWidget(win)
    win._hard_radio.setChecked(True)
    win._save()

    assert session_manager.get_status()["lockMode"] == "hard"
    # The task's own stored template must also reflect the change, not just
    # the live session -- unlike qt_ui/picker_dialogs.py's "Edit Session
    # Rules" dialog, which only ever touches the live session.
    saved = next(t for t in tasks_store.load_tasks() if t["id"] == task["id"])
    assert saved["lockMode"] == "hard"


def test_editing_a_different_tasks_session_does_not_touch_the_active_one(qtbot, isolate_tasks, isolate_state):
    running_task = _make_task(name="School")
    other_task = _make_task(name="Chores", color="#e53935")
    session_manager.start_session(
        45, "soft", ["discord.exe"], [], source="task", event_id=running_task["id"], event_title="School",
    )

    win = task_editor._TaskEditor(other_task, on_saved=None)
    qtbot.addWidget(win)
    win._hard_radio.setChecked(True)
    win._save()

    assert session_manager.get_status()["lockMode"] == "soft"


def test_editing_task_with_no_active_session_only_updates_the_template(qtbot, isolate_tasks, isolate_state):
    task = _make_task()

    win = task_editor._TaskEditor(task, on_saved=None)
    qtbot.addWidget(win)
    win._hard_radio.setChecked(True)
    win._save()

    assert not session_manager.is_active()
    saved = next(t for t in tasks_store.load_tasks() if t["id"] == task["id"])
    assert saved["lockMode"] == "hard"


def test_editing_a_review_sourced_session_for_this_task_updates_it_live(qtbot, isolate_tasks, isolate_state):
    task = _make_task()
    session_manager.start_session(
        45, "soft", ["discord.exe"], [], source="review", event_id=task["id"], event_title=task["name"],
    )

    win = task_editor._TaskEditor(task, on_saved=None)
    qtbot.addWidget(win)
    win._hard_radio.setChecked(True)
    win._save()

    assert session_manager.get_status()["lockMode"] == "hard"


def test_deleting_this_tasks_active_session_ends_it(qtbot, isolate_tasks, isolate_state, monkeypatch):
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.Yes))
    task = _make_task()
    session_manager.start_session(
        45, "soft", ["discord.exe"], [], source="task", event_id=task["id"], event_title=task["name"],
    )

    win = task_editor._TaskEditor(task, on_saved=None)
    qtbot.addWidget(win)
    win._delete()

    assert not session_manager.is_active()
    assert tasks_store.get_task(task["id"]) is None


def test_deleting_a_different_tasks_session_does_not_touch_the_active_one(qtbot, isolate_tasks, isolate_state, monkeypatch):
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.Yes))
    running_task = _make_task(name="School")
    other_task = _make_task(name="Chores", color="#e53935")
    session_manager.start_session(
        45, "soft", ["discord.exe"], [], source="task", event_id=running_task["id"], event_title="School",
    )

    win = task_editor._TaskEditor(other_task, on_saved=None)
    qtbot.addWidget(win)
    win._delete()

    assert session_manager.is_active()


# ---------------------------------------------------------------- repeats ----

def _editor(qtbot, task=None):
    win = task_editor._TaskEditor(task, on_saved=None)
    qtbot.addWidget(win)
    win._name_edit.setText("Read")
    return win


def _pick(win, kind):
    win._repeat_combo.setCurrentIndex(win._repeat_combo.findData(kind))


def test_the_repeat_menu_offers_every_kind_of_goal(qtbot, isolate_tasks):
    win = _editor(qtbot)
    kinds = [win._repeat_combo.itemData(i) for i in range(win._repeat_combo.count())]
    assert kinds == ["daily", "weekly_days", "weekly_total", "biweekly_total", "monthly_total", "custom_total"]


def test_a_weekly_total_saves_hours_as_minutes(qtbot, isolate_tasks):
    win = _editor(qtbot)
    _pick(win, "weekly_total")
    win._target_edit.setText("10h")
    win._save()
    saved = tasks_store.load_tasks()[0]
    assert (saved["recurrence"], saved["targetMinutes"], saved["weekdays"]) == ("weekly_total", 600, [])


@pytest.mark.parametrize("kind", ["biweekly_total", "monthly_total"])
def test_longer_totals_save_too(qtbot, isolate_tasks, kind):
    win = _editor(qtbot)
    _pick(win, kind)
    win._target_edit.setText("1h 30m")
    win._save()
    saved = tasks_store.load_tasks()[0]
    assert (saved["recurrence"], saved["targetMinutes"]) == (kind, 90)


def test_the_weekday_boxes_only_show_for_specific_days(qtbot, isolate_tasks):
    win = _editor(qtbot)
    win.show()
    assert win._weekday_row.isHidden()
    _pick(win, "weekly_days")
    assert not win._weekday_row.isHidden()
    _pick(win, "weekly_total")
    assert win._weekday_row.isHidden()


def test_specific_days_still_need_a_day_ticked(qtbot, isolate_tasks):
    win = _editor(qtbot)
    _pick(win, "weekly_days")
    win._target_edit.setText("30")
    win._save()
    assert win._status_label.text() == "Pick at least one weekday."
    assert tasks_store.load_tasks() == []
    win._weekday_checks["MO"].setChecked(True)
    win._save()
    saved = tasks_store.load_tasks()[0]
    assert (saved["recurrence"], saved["weekdays"], saved["targetMinutes"]) == ("weekly_days", ["MO"], 30)


def test_the_label_and_hint_follow_the_choice(qtbot, isolate_tasks):
    win = _editor(qtbot)
    assert win._target_label.text() == "Target per day"
    _pick(win, "weekly_total")
    assert win._target_label.text() == "Target per week"
    assert "Sunday" in win._repeat_hint.text()


def test_editing_a_weekly_task_shows_its_target_in_hours(qtbot, isolate_tasks):
    task = _make_task(recurrence="weekly_total", targetMinutes=600)
    win = task_editor._TaskEditor(task, on_saved=None)
    qtbot.addWidget(win)
    assert win._repeat_kind() == "weekly_total" and win._target_edit.text() == "10h"


def test_a_bad_target_is_refused_with_the_accepted_forms(qtbot, isolate_tasks):
    win = _editor(qtbot)
    for bad in ("", "0", "soon", "-5"):
        win._target_edit.setText(bad)
        win._save()
        assert "positive time" in win._status_label.text()
    assert tasks_store.load_tasks() == []


@pytest.mark.parametrize("text,minutes", [("45", 45), ("90m", 90), ("10h", 600), ("1.5h", 90), ("1h 30m", 90), ("1h30", 90)])
def test_target_forms(text, minutes):
    assert task_editor.parse_minutes(text) == minutes


def test_a_box_appears_under_each_ticked_day_and_copies_the_others(qtbot, isolate_tasks):
    win = _editor(qtbot)
    win.show()
    _pick(win, "weekly_days")
    assert win._target_edit.isHidden() and win._day_edits["MO"].isHidden()
    win._weekday_checks["MO"].setChecked(True)
    assert not win._day_edits["MO"].isHidden() and win._day_edits["TU"].isHidden()
    win._day_edits["MO"].setText("20")
    win._weekday_checks["WE"].setChecked(True)
    assert win._day_edits["WE"].text() == "20"            # nothing typed there: it follows the others
    win._day_edits["WE"].setText("1h 30m")
    win._day_typed("WE")
    win._save()
    saved = tasks_store.load_tasks()[0]
    assert saved["dayMinutes"] == {"MO": 20, "WE": 90} and saved["weekdays"] == ["MO", "WE"]


def test_enter_twice_offers_the_new_time_to_days_that_matched(qtbot, isolate_tasks, monkeypatch):
    from PySide6.QtWidgets import QMessageBox
    win = _editor(qtbot)
    _pick(win, "weekly_days")
    for code in ("MO", "TU", "FR"):
        win._weekday_checks[code].setChecked(True)
    win._day_edits["MO"].setText("30")
    win._committed["MO"] = "30"
    win._day_edits["TU"].setText("30")
    win._day_edits["FR"].setText("45")
    asked = []
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: asked.append(a[2]) or QMessageBox.Yes)
    win._day_edits["MO"].setText("60")
    win._day_typed("MO")
    win._day_enter("MO")
    assert win._day_edits["TU"].text() == "30" and not asked        # one Enter: just this day
    win._day_enter("MO")
    assert asked and win._day_edits["TU"].text() == "60" and win._day_edits["FR"].text() == "45"


def test_a_ticked_day_left_blank_is_asked_for(qtbot, isolate_tasks):
    win = _editor(qtbot)
    _pick(win, "weekly_days")
    win._weekday_checks["MO"].setChecked(True)
    win._weekday_checks["WE"].setChecked(True)
    win._day_edits["MO"].setText("20")
    win._day_edits["WE"].setText("")
    win._day_typed("WE")
    win._save()
    assert "WE" in win._status_label.text()
    assert tasks_store.load_tasks() == []


def test_same_time_each_day_saves_no_per_day_times(qtbot, isolate_tasks):
    win = _editor(qtbot)
    _pick(win, "weekly_days")
    win._weekday_checks["MO"].setChecked(True)
    win._day_edits["MO"].setText("30")
    win._save()
    assert tasks_store.load_tasks()[0]["dayMinutes"] == {}


def test_a_custom_stretch_saves_its_length(qtbot, isolate_tasks):
    win = _editor(qtbot)
    win.show()
    _pick(win, "custom_total")
    assert not win._length_row.isHidden()
    win._length_spin.setValue(10)
    win._target_edit.setText("6h")
    win._save()
    saved = tasks_store.load_tasks()[0]
    assert (saved["recurrence"], saved["periodDays"], saved["targetMinutes"]) == ("custom_total", 10, 360)
    assert saved["periodAnchor"]


def test_editing_a_task_with_per_day_times_shows_them(qtbot, isolate_tasks):
    task = tasks_store.create_task({
        "name": "P", "color": "#123456", "recurrence": "weekly_days", "weekdays": ["MO", "FR"],
        "targetMinutes": 20, "dayMinutes": {"MO": 20, "FR": 45}})
    win = task_editor._TaskEditor(task, on_saved=None)
    qtbot.addWidget(win)
    assert win._day_edits["FR"].text() == "45"
