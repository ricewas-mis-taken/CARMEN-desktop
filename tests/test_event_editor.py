"""Widget-level tests for the ported event editor (qt_ui/event_editor.py).
Uses isolate_calendar_db so these tests never touch the real calendar.db.
"""
from datetime import date

from PySide6.QtCore import QDate

import calendar_store as store
import qt_ui.event_editor as event_editor


def _set_datetime(date_edit, time_slider, iso_date, hh_mm):
    y, m, d = (int(p) for p in iso_date.split("-"))
    date_edit.setDate(QDate(y, m, d))
    hh, mm = (int(p) for p in hh_mm.split(":"))
    time_slider.setValue(hh * 60 + mm)


def test_new_event_requires_title(qtbot, isolate_calendar_db):
    win = event_editor._EventEditor(existing=None, initial_date=date(2026, 1, 15))
    qtbot.addWidget(win)
    win._title_edit.setText("")
    win._save()
    assert "title is required" in win._status_label.text().lower()


def test_new_event_rejects_end_before_start(qtbot, isolate_calendar_db):
    win = event_editor._EventEditor(existing=None, initial_date=date(2026, 1, 15))
    qtbot.addWidget(win)
    win._title_edit.setText("Test Event")
    _set_datetime(win._start_date_edit, win._start_time_slider, "2026-01-15", "10:00")
    _set_datetime(win._end_date_edit, win._end_time_slider, "2026-01-15", "09:00")
    win._save()
    assert "end must be after start" in win._status_label.text().lower()


def test_save_creates_event_in_store(qtbot, isolate_calendar_db):
    win = event_editor._EventEditor(existing=None, initial_date=date(2026, 1, 15))
    qtbot.addWidget(win)
    win._title_edit.setText("Standup")
    _set_datetime(win._start_date_edit, win._start_time_slider, "2026-01-15", "09:00")
    _set_datetime(win._end_date_edit, win._end_time_slider, "2026-01-15", "09:30")
    win._save()

    events = store.list_events()
    assert len(events) == 1
    assert events[0]["title"] == "Standup"
    assert events[0]["start"] == "2026-01-15T09:00:00"


def test_save_with_focus_integration_writes_focus_profile(qtbot, isolate_calendar_db, monkeypatch):
    import installed_apps
    monkeypatch.setattr(installed_apps, "list_installed_apps", lambda: [])

    win = event_editor._EventEditor(existing=None, initial_date=date(2026, 1, 15))
    qtbot.addWidget(win)
    win._title_edit.setText("Deep Work")
    _set_datetime(win._start_date_edit, win._start_time_slider, "2026-01-15", "09:00")
    _set_datetime(win._end_date_edit, win._end_time_slider, "2026-01-15", "11:00")
    win._focus_enabled_check.setChecked(True)
    win._hard_radio.setChecked(True)
    win._save()

    events = store.list_events()
    assert len(events) == 1
    profile = events[0]["focusProfile"]
    assert profile is not None
    assert profile["enabled"] is True
    assert profile["lockMode"] == "hard"


def test_editing_existing_event_prefills_fields(qtbot, isolate_calendar_db, monkeypatch):
    import installed_apps
    monkeypatch.setattr(installed_apps, "list_installed_apps", lambda: [])

    event_id = store.save_event({
        "title": "Existing Event", "start": "2026-02-01T10:00:00", "end": "2026-02-01T11:00:00",
        "color": "#e53935", "notes": "some notes", "rrule": None, "reminderOffsets": [10],
    })
    existing = store.get_event(event_id)

    win = event_editor._EventEditor(existing=existing, initial_date=None)
    qtbot.addWidget(win)

    assert win._title_edit.text() == "Existing Event"
    assert win._color == "#e53935"
    assert win._notes_edit.toPlainText() == "some notes"
    assert win._reminders_list == [10]


def test_delete_soft_deletes_event(qtbot, isolate_calendar_db, monkeypatch):
    import installed_apps
    monkeypatch.setattr(installed_apps, "list_installed_apps", lambda: [])
    from PySide6.QtWidgets import QMessageBox
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.Yes))

    event_id = store.save_event({
        "title": "To Delete", "start": "2026-03-01T10:00:00", "end": "2026-03-01T11:00:00",
        "color": "#2d8cff", "notes": "", "rrule": None, "reminderOffsets": [],
    })
    existing = store.get_event(event_id)

    win = event_editor._EventEditor(existing=existing, initial_date=None)
    qtbot.addWidget(win)
    win._delete()

    assert store.list_events() == []  # soft-deleted, excluded from default list_events()
    assert store.get_event(event_id) is not None  # still recoverable


def test_recurrence_prefill_weekly_days_round_trip(qtbot):
    from PySide6.QtWidgets import QCheckBox
    checks = {"MO": QCheckBox(), "WE": QCheckBox(), "FR": QCheckBox()}
    kind, interval, unit = event_editor._prefill_recurrence_from_rrule(
        "FREQ=WEEKLY;BYDAY=MO,WE,FR", checks
    )
    assert kind == "weekly_days"
    assert checks["MO"].isChecked()
    assert checks["WE"].isChecked()
    assert checks["FR"].isChecked()
