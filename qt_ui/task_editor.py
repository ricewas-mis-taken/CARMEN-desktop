"""Add/edit dialog for a Tasks-tab task -- name, color, target, how it repeats
(every day / specific weekdays / a total per week, 2 weeks or month),
soft/hard lock mode, and the
process block list / domain allow list to apply while it's running. Mirrors
the shape of qt_ui/event_editor.py's focus-integration section (same
checklist component, same list defaulting from config.load_config()) but
without any of the calendar-event scheduling fields this doesn't need.

Non-modal (.show()), same convention as the rest of this app's dialogs.
"""
import os
import re

from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QRadioButton,
    QVBoxLayout,
    QWidget,
)
from qt_ui.wrap_edit import WrapEdit

import config
import installed_apps
import session_manager
import tasks_store
import qt_ui.checklist as checklist
from qt_ui.ui_kit import accent, danger, dialog_title, field_label, role_label

COLOR_PALETTE = [
    "#5B8DEF", "#e53935", "#43a047", "#fb8c00", "#8e24aa",
    "#00acc1", "#f4511e", "#3949ab", "#6d4c41", "#546e7a",
]

_open_windows = set()


def _track(win):
    _open_windows.add(win)
    win.destroyed.connect(lambda: _open_windows.discard(win))
    return win


def open_task_editor(task=None, on_saved=None):
    """task=None opens a blank "add task" form; pass an existing task dict
    to edit it in place. on_saved(task_dict) is called after a successful
    save/delete so the caller (qt_ui/tasks_tab.py) can refresh its cards."""
    _track(_TaskEditor(task, on_saved)).show()


# How a task repeats: (recurrence stored on the task, menu text, what the target is, hint).
REPEAT_CHOICES = [
    ("daily", "Every day", "Target per day", "Counts each day on its own."),
    ("weekly_days", "On specific days", "Target on each of those days",
     "Only the days you tick have a target; the other days are free."),
    ("weekly_total", "A total each week", "Target per week",
     "Work any day, Monday to Sunday. It is only due by the end of Sunday."),
    ("biweekly_total", "A total every 2 weeks", "Target per 2 weeks",
     "Work any day. It is due at the end of every second week."),
    ("monthly_total", "A total each month", "Target per month",
     "Work any day. It is due on the last day of the month."),
    ("custom_total", "A total over a number of days", "Target for the whole stretch",
     "Work any day. It is due at the end of every stretch, counted from the day you save it."),
]

_DURATION = re.compile(
    r"^\s*(?:(?P<h>\d+(?:\.\d+)?)\s*(?:h|hr|hrs|hour|hours)\s*(?:(?P<hm>\d+)\s*(?:m|min|mins|minute|minutes)?)?"
    r"|(?P<m>\d+)\s*(?:m|min|mins|minute|minutes)?)\s*$", re.IGNORECASE)


def parse_minutes(text):
    """Whole minutes from "90", "90m", "10h", "1.5h" or "1h 30m"; None if it isn't one
    or it isn't positive."""
    match = _DURATION.match(text or "")
    if not match:
        return None
    if match.group("h") is not None:
        minutes = float(match.group("h")) * 60 + int(match.group("hm") or 0)
    else:
        minutes = int(match.group("m"))
    minutes = int(round(minutes))
    return minutes if minutes > 0 else None


def format_target(minutes, long_period):
    """What the target box starts with: hours for a week/month total ("10h"), plain
    minutes for a per-day target as it always was."""
    minutes = int(minutes)
    if long_period and minutes >= 60:
        hours, rest = divmod(minutes, 60)
        return f"{hours}h" if not rest else f"{hours}h {rest}m"
    return str(minutes)


def _bold_label(text):
    """The small grey label above a field (the name is older than the look)."""
    return field_label(text)


_app_scan_cache = {"at": 0.0, "apps": None, "scanner": None}
_APP_SCAN_FRESH_SECONDS = 600


def _cache_path():
    import paths
    return paths.private_path("installed_apps_cache.json")


def _read_disk_cache():
    """The apps found last time on this computer ([] if none): what the editor lists while it looks again."""
    import json
    try:
        with open(_cache_path(), "r", encoding="utf-8") as f:
            data = json.load(f)
        return [a for a in data if isinstance(a, dict) and a.get("process_name") and a.get("display_name")]
    except (OSError, ValueError, TypeError):
        return []


def _write_disk_cache(apps):
    import json
    try:
        with open(_cache_path(), "w", encoding="utf-8") as f:
            json.dump(apps, f)
    except OSError:
        pass


class _AppScan(QObject):
    """Lists the installed apps without making the editor wait: the ones found last time arrive at once, then a
    fresh scan (run off the screen's thread, and skipped if one finished in the last few minutes) adds any new
    ones. `done` can fire twice; the editor adds only rows it does not have."""
    done = Signal(list)

    def start(self):
        import threading
        import time
        cache = _app_scan_cache
        scanner = installed_apps.list_installed_apps
        if cache["apps"] is not None and cache["scanner"] is scanner and time.monotonic() - cache["at"] < _APP_SCAN_FRESH_SECONDS:
            QTimer.singleShot(0, lambda: self.done.emit(cache["apps"]))
            return
        remembered = _read_disk_cache() if cache["apps"] is None else cache["apps"]
        if remembered:
            QTimer.singleShot(0, lambda: self.done.emit(remembered))

        def work():
            try:
                apps = scanner()
            except Exception:
                apps = []
            cache["apps"], cache["at"], cache["scanner"] = apps, time.monotonic(), scanner
            if apps:
                _write_disk_cache(apps)
            try:
                self.done.emit(apps)
            except RuntimeError:
                pass                              # the window was closed meanwhile
        threading.Thread(target=work, daemon=True).start()


class _TaskEditor(QWidget):
    def __init__(self, task, on_saved):
        super().__init__(None, Qt.WindowStaysOnTopHint)
        self.setObjectName("PopupBg")
        self._task = task
        self._on_saved = on_saved
        self._color = (task or {}).get("color", COLOR_PALETTE[0])

        self.setWindowTitle("Carmen Focus — Edit Task" if task else "Carmen Focus — New Task")
        self.resize(480, 840)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 20)
        layout.setSpacing(8)
        layout.addWidget(dialog_title(
            "Edit task" if task else "New task",
            "How much time it asks for, how it repeats, and what it blocks while it runs."))

        layout.addWidget(_bold_label("Name"))
        self._name_edit = WrapEdit((task or {}).get("name", ""))
        layout.addWidget(self._name_edit)

        layout.addWidget(_bold_label("Color"))
        self._swatch_row = QHBoxLayout()
        self._swatch_buttons = {}
        for hexval in COLOR_PALETTE:
            btn = QPushButton()
            btn.setFixedSize(28, 28)
            btn.setCursor(Qt.PointingHandCursor)
            btn.clicked.connect(lambda checked=False, h=hexval: self._pick_color(h))
            self._swatch_buttons[hexval] = btn
            self._swatch_row.addWidget(btn)
        self._swatch_row.addStretch(1)
        layout.addLayout(self._swatch_row)
        self._pick_color(self._color)

        saved_kind = (task or {}).get("recurrence", "daily")
        if saved_kind not in {c[0] for c in REPEAT_CHOICES}:
            saved_kind = "daily"

        layout.addWidget(_bold_label("Repeats"))
        self._repeat_combo = QComboBox()
        for kind, text, _label, _hint in REPEAT_CHOICES:
            self._repeat_combo.addItem(text, kind)
        self._repeat_combo.setCurrentIndex(self._repeat_combo.findData(saved_kind))
        layout.addWidget(self._repeat_combo)

        # "On specific days": tick a day and a small minutes box appears under it. A box nobody
        # has typed in follows the other days' time; Enter twice in a box offers to change
        # every other day that had the same time.
        self._weekday_row = QWidget()
        weekday_layout = QHBoxLayout(self._weekday_row)
        weekday_layout.setContentsMargins(0, 4, 0, 0)
        weekday_layout.setSpacing(8)
        saved_weekdays = set((task or {}).get("weekdays", []))
        saved_day_minutes = (task or {}).get("dayMinutes") or {}
        default_minutes = (task or {}).get("targetMinutes")
        self._weekday_checks = {}
        self._day_edits = {}
        self._touched = set()
        self._enter_at = {}
        self._committed = {}
        self._previous = {}
        for code in tasks_store.WEEKDAY_CODES:
            column = QVBoxLayout()
            column.setSpacing(2)
            check = QCheckBox(code)
            check.setChecked(code in saved_weekdays)
            self._weekday_checks[code] = check
            column.addWidget(check)
            edit = QLineEdit()
            edit.setPlaceholderText("min")
            edit.setFixedWidth(58)
            minutes = saved_day_minutes.get(code, default_minutes if code in saved_weekdays else None)
            if minutes:
                edit.setText(format_target(minutes, False))
            if code in saved_day_minutes:
                self._touched.add(code)
            self._committed[code] = edit.text().strip()
            edit.textEdited.connect(lambda _t, c=code: self._day_typed(c))
            edit.returnPressed.connect(lambda c=code: self._day_enter(c))
            self._day_edits[code] = edit
            column.addWidget(edit)
            weekday_layout.addLayout(column)
        layout.addWidget(self._weekday_row)

        # "A total over a number of days": how many.
        self._length_row = QWidget()
        length_layout = QHBoxLayout(self._length_row)
        length_layout.setContentsMargins(0, 0, 0, 0)
        length_layout.addWidget(role_label("Every", "hint"))
        self._length_spin = QSpinBox()
        self._length_spin.setRange(tasks_store.MIN_PERIOD_DAYS, tasks_store.MAX_PERIOD_DAYS)
        self._length_spin.setValue((task or {}).get("periodDays") or 14)
        length_layout.addWidget(self._length_spin)
        length_layout.addWidget(role_label("days", "hint"))
        length_layout.addStretch(1)
        layout.addWidget(self._length_row)

        self._target_label = _bold_label("Target")
        layout.addWidget(self._target_label)
        self._target_edit = QLineEdit(
            format_target((task or {}).get("targetMinutes", 30), saved_kind in tasks_store.PERIOD_RECURRENCES))
        self._target_edit.setPlaceholderText("minutes, or 10h / 1h 30m")
        layout.addWidget(self._target_edit)
        self._repeat_hint = role_label("", "hint", wrap=True)
        layout.addWidget(self._repeat_hint)
        self._repeat_combo.currentIndexChanged.connect(self._on_repeat_changed)
        for chk in self._weekday_checks.values():
            chk.toggled.connect(self._on_repeat_changed)
        self._on_repeat_changed()

        layout.addWidget(_bold_label("Lock mode"))
        lock_row = QHBoxLayout()
        self._soft_radio = QRadioButton("Soft")
        self._hard_radio = QRadioButton("Hard")
        lock_group = QButtonGroup(self)
        lock_group.addButton(self._soft_radio)
        lock_group.addButton(self._hard_radio)
        if (task or {}).get("lockMode", "soft") == "hard":
            self._hard_radio.setChecked(True)
        else:
            self._soft_radio.setChecked(True)
        lock_row.addWidget(self._soft_radio)
        lock_row.addWidget(self._hard_radio)
        lock_row.addStretch(1)
        layout.addLayout(lock_row)

        layout.addWidget(_bold_label("Blocklisted apps while this task runs"))
        default_processes = (task or {}).get("processBlocklist") if task else config.load_config().get("processBlocklist", [])
        existing_process_set = {p.lower() for p in (default_processes or [])}
        # The window opens at once with the apps this task already blocks; the rest of the installed apps
        # (a scan of the Start Menu and Store packages, slow) are added when it finishes.
        process_widget, self._process_checks, process_add_row = checklist.build_checklist(
            [], existing_process_set, height=160)
        layout.addWidget(process_widget)
        for process_name in (default_processes or []):
            process_add_row(process_name, process_name, checked=True)
        self._process_add_row = process_add_row
        self._existing_process_set = existing_process_set
        self._scan_note = role_label("Looking for installed apps...", "hint")
        layout.addWidget(self._scan_note)
        self._scan = _AppScan()
        self._scan.done.connect(self._apps_found)
        self._scan.start()

        process_manual_row = QHBoxLayout()
        self._process_manual_edit = QLineEdit()
        process_manual_row.addWidget(self._process_manual_edit)
        process_manual_add = QPushButton("Add")
        process_manual_add.clicked.connect(lambda: self._add_manual_process(process_add_row))
        process_manual_row.addWidget(process_manual_add)
        layout.addLayout(process_manual_row)

        # One Chrome/Edge profile instead of the whole browser (see picker_dialogs' "Browser profiles").
        self._profile_checks = {}
        try:
            import window_tracker
            known_profiles = window_tracker.list_known_browser_profiles()
        except Exception:
            known_profiles = []
        saved_profiles = (task or {}).get("browserProfileBlocklist") if task else config.load_config().get("browserProfileBlocklist", [])
        saved_profiles = list(saved_profiles or [])
        if known_profiles or saved_profiles:
            layout.addWidget(_bold_label("Browser profiles blocked while this task runs"))
            profile_widget, self._profile_checks, profile_add_row = checklist.build_checklist(
                [], saved_profiles, height=80)
            layout.addWidget(profile_widget)
            for profile in known_profiles:
                label = profile["label"] if profile["is_running"] else f"{profile['label']} (not open right now)"
                profile_add_row(profile["aumi"], label, profile["aumi"] in saved_profiles)
            for aumi in saved_profiles:           # one Chrome no longer lists still stays until it is unticked
                profile_add_row(aumi, aumi, True)

        layout.addWidget(_bold_label("Allowed domains while this task runs"))
        default_domains = (task or {}).get("domainWhitelist") if task else config.load_config().get("domainWhitelist", [])
        domain_widget, self._domain_checks, domain_add_row = checklist.build_checklist(
            list(default_domains or []), {d.lower() for d in (default_domains or [])}, height=100,
        )
        layout.addWidget(domain_widget)

        domain_manual_row = QHBoxLayout()
        self._domain_manual_edit = QLineEdit()
        domain_manual_row.addWidget(self._domain_manual_edit)
        domain_manual_add = QPushButton("Add")
        domain_manual_add.clicked.connect(lambda: self._add_manual_domain(domain_add_row))
        domain_manual_row.addWidget(domain_manual_add)
        layout.addLayout(domain_manual_row)

        self._status_label = role_label("", "error")
        layout.addWidget(self._status_label)

        button_row = QHBoxLayout()
        if task:
            delete_button = danger(QPushButton("Delete task"))
            delete_button.clicked.connect(self._delete)
            button_row.addWidget(delete_button)
        button_row.addStretch(1)
        cancel_button = QPushButton("Cancel")
        cancel_button.clicked.connect(self.close)
        button_row.addWidget(cancel_button)
        save_button = accent(QPushButton("Save task"))
        save_button.clicked.connect(self._save)
        button_row.addWidget(save_button)
        layout.addLayout(button_row)

    _closed = False

    def _apps_found(self, apps):
        if self._closed:
            return
        for app in apps:
            name = app["process_name"]
            self._process_add_row(name, f"{app['display_name']} ({name})", name.lower() in self._existing_process_set)
        self._scan_note.setVisible(False)

    def closeEvent(self, event):
        self._closed = True                    # a scan that finishes after the window is gone has nobody to tell
        super().closeEvent(event)

    def _repeat_kind(self):
        return self._repeat_combo.currentData()

    def _on_repeat_changed(self, _index=None):
        kind = self._repeat_kind()
        _kind, _text, label, hint = next(c for c in REPEAT_CHOICES if c[0] == kind)
        self._target_label.setText(label)
        self._repeat_hint.setText(hint)
        # "isHidden", not isVisible: the editor may not be shown yet.
        per_day = kind == "weekly_days"
        self._weekday_row.setVisible(per_day)
        self._length_row.setVisible(kind == "custom_total")
        # With a box under each day the single target box has nothing to say.
        self._target_label.setVisible(not per_day)
        self._target_edit.setVisible(not per_day)
        for code, edit in self._day_edits.items():
            ticked = self._weekday_checks[code].isChecked()
            if ticked and not edit.text().strip() and code not in self._touched:
                edit.setText(self._shared_minutes(code))
            edit.setVisible(per_day and ticked)

    def _shared_minutes(self, except_code):
        """The time the other ticked days have (or the target box's), for a day just ticked."""
        for code, edit in self._day_edits.items():
            if code != except_code and self._weekday_checks[code].isChecked() and edit.text().strip():
                return edit.text().strip()
        return self._target_edit.text().strip() or "45"

    def _day_typed(self, code):
        self._touched.add(code)

    def _day_enter(self, code):
        """One Enter keeps the time for this day. A second Enter right after offers it to every
        other ticked day that still has the time this one had."""
        import time
        now = time.monotonic()
        edit = self._day_edits[code]
        text = edit.text().strip()
        if text != self._committed.get(code, ""):
            self._previous[code] = self._committed.get(code, "")
            self._committed[code] = text
        double = now - self._enter_at.get(code, 0) < 0.6
        self._enter_at[code] = now
        if not double:
            return
        old = self._previous.get(code)
        new = text
        others = [c for c, e in self._day_edits.items()
                  if c != code and self._weekday_checks[c].isChecked() and old and e.text().strip() == old]
        if not others or not new or new == old:
            return
        from PySide6.QtWidgets import QMessageBox
        names = ", ".join(others)
        if QMessageBox.question(
                self, "Change other days",
                f"{names} also {'has' if len(others) == 1 else 'have'} {old}. Change to {new}?") == QMessageBox.Yes:
            for other in others:
                self._day_edits[other].setText(new)

    def _pick_color(self, hexval):
        self._color = hexval
        for h, btn in self._swatch_buttons.items():
            border = "3px solid #1B2230" if h == hexval else "1px solid #0002"
            btn.setStyleSheet(f"background: {h}; border-radius: 14px; border: {border}; padding: 0;")

    def _add_manual_process(self, add_row_fn):
        name = os.path.basename(self._process_manual_edit.text().strip())
        if name.lower().endswith(".exe"):
            add_row_fn(name, name, checked=True)
            self._process_manual_edit.setText("")

    def _add_manual_domain(self, add_row_fn):
        domain = self._domain_manual_edit.text().strip()
        if domain:
            add_row_fn(domain, domain, checked=True)
            self._domain_manual_edit.setText("")

    def _delete(self):
        confirm = QMessageBox.question(
            self, "Delete Task", f"Delete \"{self._task['name']}\"? This can't be undone.",
        )
        if confirm != QMessageBox.Yes:
            return
        status = session_manager.get_status()
        if status["isActive"] and status.get("source") in ("task", "review") and status.get("eventId") == self._task["id"]:
            session_manager.end_session(end_type="manual", reason="task deleted")
        tasks_store.delete_task(self._task["id"])
        self.close()
        if self._on_saved is not None:
            self._on_saved(None)

    def _save(self):
        name = self._name_edit.text().strip()
        if not name:
            self._status_label.setText("Name is required.")
            return
        recurrence_kind = self._repeat_kind()
        weekdays = []
        day_minutes = {}
        if recurrence_kind == "weekly_days":
            weekdays = [code for code, chk in self._weekday_checks.items() if chk.isChecked()]
            if not weekdays:
                self._status_label.setText("Pick at least one weekday.")
                return
            for code in weekdays:
                minutes = parse_minutes(self._day_edits[code].text())
                if minutes is None:
                    self._status_label.setText(f"Give {code} a time, like 45, 90m or 1h 30m.")
                    return
                day_minutes[code] = minutes
            if len(set(day_minutes.values())) == 1:
                target_minutes = day_minutes[weekdays[0]]      # the same time every day: no per-day times
                day_minutes = {}
        if day_minutes:
            target_minutes = day_minutes[weekdays[0]]      # what a day without its own time would use
        elif recurrence_kind == "weekly_days":
            pass
        else:
            target_minutes = parse_minutes(self._target_edit.text())
            if target_minutes is None:
                self._status_label.setText('Target must be a positive time, like 45, 90m, 10h or 1h 30m.')
                return

        data = {
            "name": name,
            "color": self._color,
            "targetMinutes": target_minutes,
            "recurrence": recurrence_kind,
            "weekdays": weekdays,
            "dayMinutes": day_minutes,
            "periodDays": self._length_spin.value() if recurrence_kind == "custom_total" else None,
            "lockMode": "hard" if self._hard_radio.isChecked() else "soft",
            "processBlocklist": checklist.get_checked(self._process_checks),
            "browserProfileBlocklist": checklist.get_checked(self._profile_checks),
            "domainWhitelist": checklist.get_checked(self._domain_checks),
            "hideTaskbarBadges": False,
            "stopTaskbarFlashing": False,
        }

        try:
            if self._task:
                saved = tasks_store.update_task(self._task["id"], data)
            else:
                saved = tasks_store.create_task(data)
        except tasks_store.DuplicateColorError as e:
            self._status_label.setText(str(e))
            return

        # Saving here always updates the task's own stored template (used
        # for every future start of this task) -- but if this exact task is
        # what's actively running right now (qt_ui/tasks_tab.py starts
        # sessions with source="task" and event_id=<task id>), the change
        # should also take effect immediately on that live session, not just
        # the next time it's started. Mirrors qt_ui/picker_dialogs.py's
        # "Edit Session Rules" dialog's own update_blocklist() call, except
        # that one only ever touches the live session, never anything
        # persistent -- this one does both at once.
        if self._task:
            status = session_manager.get_status()
            if status["isActive"] and status.get("source") in ("task", "review") and status.get("eventId") == self._task["id"]:
                session_manager.update_blocklist(
                    data["processBlocklist"], data["domainWhitelist"], lock_mode=data["lockMode"],
                    blocked_browser_profiles=data["browserProfileBlocklist"],
                )
                session_manager.update_taskbar_quiet(data["hideTaskbarBadges"], data["stopTaskbarFlashing"])

        self.close()
        if self._on_saved is not None:
            self._on_saved(saved)
