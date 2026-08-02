"""Qt port of calendar_gui.py's event editor (_build_event_editor) --
title/time/color/notes, recurrence, reminders, and the per-event Focus
Timer integration (lock mode + process block list/domain allow list via the
shared qt_ui/checklist.py component). Business logic untouched: calendar_store,
calendar_recurrence, config, installed_apps are called exactly as the Tk
version called them.

Non-modal (.show(), not .exec()), same as every other dialog in this app.
"""
import os
from datetime import date, datetime, timedelta

from PySide6.QtCore import QDate, QTimer, Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QColorDialog,
    QComboBox,
    QDateEdit,
    QFrame,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QApplication,
    QListWidget,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QSlider,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

import calendar_recurrence as recurrence
import calendar_store as store
import config
import installed_apps
import qt_ui.checklist as checklist

COLOR_PALETTE = [
    "#2d8cff", "#e53935", "#43a047", "#fb8c00", "#8e24aa",
    "#00acc1", "#f4511e", "#3949ab", "#6d4c41", "#546e7a",
]

REMINDER_PRESETS = [
    ("At start time", 0),
    ("10 minutes before", 10),
    ("30 minutes before", 30),
    ("1 hour before", 60),
    ("1 day before", 1440),
]

_CUSTOM_REMINDER_LABEL = "Custom minutes before…"

RECUR_LABELS = {
    "none": "Does not repeat", "daily": "Daily", "weekly": "Weekly",
    "weekly_days": "Weekly on selected days", "monthly": "Monthly",
    "yearly": "Yearly", "custom": "Custom (every N days/weeks)",
}

# This popup is a bare top-level QWidget (not part of the main window's
# #ContentArea), so it renders with the OS's native dark window background
# instead of the app's light theme -- the shared styles.qss rules only set
# *text* color (#1F2328, near-black) for #PopupBg descendants, which is
# invisible against that native dark background. Rather than fight the
# native theming, this instance stylesheet commits the dialog to a dark
# look on purpose: white text throughout, with the combo-box dropdowns kept
# an explicit light "white dropdown" so they still read as a distinct
# control against the dark surroundings.
_DIALOG_QSS = """
QWidget#PopupBg {
    background: #1e1e1e;
}
QWidget#PopupBg QLabel,
QWidget#PopupBg QCheckBox,
QWidget#PopupBg QRadioButton {
    color: #ffffff;
    font-size: 13px;
}
QWidget#PopupBg QLineEdit,
QWidget#PopupBg QTextEdit,
QWidget#PopupBg QDateEdit {
    background: #2a2a2a;
    color: #ffffff;
    border: 1px solid #444;
    border-radius: 8px;
    padding: 4px 6px;
}
QWidget#PopupBg QComboBox {
    background: #ffffff;
    color: #1F2328;
    border: 1px solid #999;
    border-radius: 8px;
    padding: 4px 8px;
}
QComboBox QAbstractItemView {
    background: #ffffff;
    color: #1F2328;
    selection-background-color: #5B8DEF;
    selection-color: #ffffff;
    outline: none;
}
QCalendarWidget {
    background: #ffffff;
    color: #1F2328;
}
QWidget#PopupBg QPushButton {
    background: #3a3a3a;
    color: #ffffff;
    border: none;
    border-radius: 8px;
    padding: 6px 14px;
}
QWidget#PopupBg QPushButton:hover {
    background: #4a4a4a;
}
QWidget#PopupBg QPushButton.AccentButton {
    background: #5B8DEF;
    color: white;
    font-weight: 600;
}
QWidget#PopupBg QPushButton.AccentButton:hover {
    background: #4A7CDE;
}
QWidget#PopupBg QPushButton.AddButton {
    background: #2e7d32;
    color: white;
    font-weight: 600;
}
QWidget#PopupBg QPushButton.AddButton:hover {
    background: #256428;
}
QWidget#PopupBg QPushButton.RemoveButton {
    background: #c62828;
    color: white;
    font-weight: 600;
}
QWidget#PopupBg QPushButton.RemoveButton:hover {
    background: #a82121;
}
"""

_open_windows = set()


def open_event_editor(event_id=None, initial_date=None):
    existing = store.get_event(event_id) if event_id else None
    win = _EventEditor(existing, initial_date)
    _open_windows.add(win)
    win.destroyed.connect(lambda: _open_windows.discard(win))
    win.show()


class _EventEditor(QWidget):
    def __init__(self, existing, initial_date):
        super().__init__(None, Qt.WindowStaysOnTopHint)
        self.setObjectName("PopupBg")
        self.setStyleSheet(_DIALOG_QSS)
        self.setWindowTitle("Edit Event" if existing else "New Event")
        self.resize(460, 720)
        self._existing = existing

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        scroll.setStyleSheet("background: transparent;")
        outer.addWidget(scroll)
        form = QWidget()
        # QSS descendant selectors (QWidget#PopupBg QLabel {...}) reach
        # through the widget tree fine, but a QScrollArea's own background
        # painting isn't transparent by default and sits between this form
        # and the styled PopupBg root -- setting the object name here too,
        # rather than relying on the ancestor rule alone, is what actually
        # makes the visible background (not just descendant text/inputs)
        # match the rest of the app instead of showing through as unstyled
        # dark/default Qt chrome.
        form.setObjectName("PopupBg")
        layout = QVBoxLayout(form)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(4)
        scroll.setWidget(form)

        default_start = datetime.combine(initial_date or date.today(), datetime.min.time()).replace(hour=9)
        default_end = default_start + timedelta(hours=1)
        if existing:
            default_start = datetime.fromisoformat(existing["start"])
            default_end = datetime.fromisoformat(existing["end"])

        layout.addWidget(_bold_label("Title"))
        self._title_edit = QLineEdit(existing["title"] if existing else "")
        layout.addWidget(self._title_edit)

        self._all_day_check = QCheckBox("All day")
        self._all_day_check.setChecked(bool(existing["allDay"]) if existing else False)
        layout.addWidget(self._all_day_check)

        self._start_date_edit, self._start_time_slider = _build_datetime_picker(
            layout, "Start", default_start
        )
        self._end_date_edit, self._end_time_slider = _build_datetime_picker(
            layout, "End", default_end
        )

        layout.addWidget(_bold_label("Color"))
        self._color = existing["color"] if existing else COLOR_PALETTE[0]
        palette_row = QHBoxLayout()
        self._swatch_buttons = {}
        for c in COLOR_PALETTE:
            btn = QPushButton()
            btn.setFixedSize(22, 22)
            btn.setStyleSheet(f"background: {c}; border-radius: 4px; border: 1px solid #0002;")
            btn.clicked.connect(lambda checked=False, c=c: self._set_color(c))
            palette_row.addWidget(btn)
            self._swatch_buttons[c] = btn
        custom_button = QPushButton("Custom…")
        custom_button.clicked.connect(self._pick_custom_color)
        palette_row.addWidget(custom_button)
        palette_row.addStretch(1)
        layout.addLayout(palette_row)
        self._set_color(self._color)

        layout.addWidget(_bold_label("Notes"))
        self._notes_edit = QTextEdit()
        self._notes_edit.setFixedHeight(60)
        if existing:
            self._notes_edit.setPlainText(existing.get("notes", ""))
        layout.addWidget(self._notes_edit)

        self._build_recurrence_section(layout, existing)
        self._build_reminders_section(layout, existing)
        self._build_focus_section(layout, existing)

        self._status_label = QLabel()
        self._status_label.setStyleSheet("color: #c62828;")
        self._status_label.setWordWrap(True)
        layout.addWidget(self._status_label)

        button_row = QHBoxLayout()
        save_button = QPushButton("Save")
        save_button.setProperty("class", "AccentButton")
        save_button.clicked.connect(self._save)
        button_row.addWidget(save_button)
        if existing:
            delete_button = QPushButton("Delete")
            delete_button.setStyleSheet("color: #c62828;")
            delete_button.setProperty("class", "SecondaryButton")
            delete_button.clicked.connect(self._delete)
            button_row.addWidget(delete_button)
        cancel_button = QPushButton("Cancel")
        cancel_button.setProperty("class", "SecondaryButton")
        cancel_button.clicked.connect(self.close)
        button_row.addWidget(cancel_button)
        button_row.addStretch(1)
        layout.addLayout(button_row)

        layout.addStretch(1)

    # --- color ---

    def _set_color(self, color_hex):
        self._color = color_hex
        for hexval, btn in self._swatch_buttons.items():
            border = "2px solid #1F2328" if hexval == color_hex else "1px solid #0002"
            btn.setStyleSheet(f"background: {hexval}; border-radius: 4px; border: {border};")

    def _pick_custom_color(self):
        color = QColorDialog.getColor(QColor(self._color), self, "Custom event color")
        if color.isValid():
            self._color = color.name()
            for btn in self._swatch_buttons.values():
                btn.setStyleSheet(btn.styleSheet().replace("2px solid #1F2328", "1px solid #0002"))

    # --- recurrence ---

    def _build_recurrence_section(self, layout, existing):
        layout.addWidget(_bold_label("Repeats"))

        self._recur_kind = "none"
        self._recur_interval = 1
        self._recur_unit = "weeks"
        self._weekday_checks = {code: QCheckBox(code) for code in recurrence.WEEKDAY_CODES}

        if existing and existing.get("rrule"):
            self._recur_kind, self._recur_interval, self._recur_unit = _prefill_recurrence_from_rrule(
                existing["rrule"], self._weekday_checks
            )

        self._recur_combo = QComboBox()
        self._recur_combo.addItems(list(RECUR_LABELS.values()))
        self._recur_combo.setCurrentText(RECUR_LABELS[self._recur_kind])
        self._recur_combo.currentTextChanged.connect(self._on_recur_change)
        layout.addWidget(self._recur_combo)

        self._weekday_row = QWidget()
        weekday_layout = QHBoxLayout(self._weekday_row)
        weekday_layout.setContentsMargins(0, 4, 0, 0)
        for code in recurrence.WEEKDAY_CODES:
            weekday_layout.addWidget(self._weekday_checks[code])
        layout.addWidget(self._weekday_row)

        self._interval_row = QWidget()
        interval_layout = QHBoxLayout(self._interval_row)
        interval_layout.setContentsMargins(0, 4, 0, 0)
        interval_layout.addWidget(QLabel("Every"))
        self._interval_edit = QLineEdit(str(self._recur_interval))
        self._interval_edit.setFixedWidth(40)
        interval_layout.addWidget(self._interval_edit)
        self._unit_combo = QComboBox()
        self._unit_combo.addItems(["days", "weeks"])
        self._unit_combo.setCurrentText(self._recur_unit)
        interval_layout.addWidget(self._unit_combo)
        interval_layout.addStretch(1)
        layout.addWidget(self._interval_row)

        self._on_recur_change(self._recur_combo.currentText())

    def _on_recur_change(self, label_text):
        label_to_kind = {v: k for k, v in RECUR_LABELS.items()}
        kind = label_to_kind[label_text]
        self._recur_kind = kind
        self._weekday_row.setVisible(kind == "weekly_days")
        self._interval_row.setVisible(kind == "custom")

    # --- reminders ---

    def _build_reminders_section(self, layout, existing):
        layout.addWidget(_bold_label("Reminders"))
        self._reminders_list = list(existing.get("reminderOffsets", [])) if existing else []

        self._reminders_widget = QListWidget()
        self._reminders_widget.setFixedHeight(70)
        layout.addWidget(self._reminders_widget)
        self._refresh_reminders_list()

        controls = QHBoxLayout()
        self._reminder_preset_combo = QComboBox()
        self._reminder_preset_combo.addItems([label for label, _ in REMINDER_PRESETS] + [_CUSTOM_REMINDER_LABEL])
        self._reminder_preset_combo.setCurrentIndex(1)
        controls.addWidget(self._reminder_preset_combo)
        add_button = QPushButton("Add")
        add_button.setProperty("class", "AddButton")
        add_button.clicked.connect(self._add_preset_reminder)
        controls.addWidget(add_button)
        remove_button = QPushButton("Remove selected")
        remove_button.setProperty("class", "RemoveButton")
        remove_button.clicked.connect(self._remove_selected_reminder)
        controls.addWidget(remove_button)
        layout.addLayout(controls)

    def _refresh_reminders_list(self):
        self._reminders_widget.clear()
        preset_by_offset = {off: lbl for lbl, off in REMINDER_PRESETS}
        for offset in self._reminders_list:
            label = preset_by_offset.get(offset, f"{offset} min before")
            self._reminders_widget.addItem(label)

    def _add_preset_reminder(self):
        label = self._reminder_preset_combo.currentText()
        presets = dict(REMINDER_PRESETS)
        if label in presets:
            offset = presets[label]
        else:
            offset, ok = QInputDialog.getInt(
                self, "Custom reminder", "Minutes before start:", 10, 0, 100000, 1
            )
            if not ok:
                return
        if offset not in self._reminders_list:
            self._reminders_list.append(offset)
            self._refresh_reminders_list()

    def _remove_selected_reminder(self):
        row = self._reminders_widget.currentRow()
        if row >= 0:
            del self._reminders_list[row]
            self._refresh_reminders_list()

    # --- focus integration ---

    def _build_focus_section(self, layout, existing):
        divider = QFrame()
        divider.setFrameShape(QFrame.HLine)
        divider.setStyleSheet("color: #ccc;")
        layout.addWidget(divider)

        existing_focus = existing.get("focusProfile") if existing else None
        self._focus_enabled_check = QCheckBox("Integrate with Focus Timer")
        self._focus_enabled_check.setStyleSheet("font-weight: bold;")
        self._focus_enabled_check.setChecked(bool(existing_focus and existing_focus.get("enabled")))
        self._focus_enabled_check.toggled.connect(self._toggle_focus_subscreen)
        layout.addWidget(self._focus_enabled_check)

        self._focus_subscreen = QWidget()
        self._focus_subscreen.setObjectName("PopupBg")
        self._focus_subscreen.setStyleSheet("border: 1px solid #444; background: #1e1e1e;")
        subscreen_layout = QVBoxLayout(self._focus_subscreen)

        lock_row = QHBoxLayout()
        lock_row.addWidget(QLabel("Lock mode:"))
        self._soft_radio = QRadioButton("Soft")
        self._hard_radio = QRadioButton("Hard")
        lock_group = QButtonGroup(self)
        lock_group.addButton(self._soft_radio)
        lock_group.addButton(self._hard_radio)
        if (existing_focus or {}).get("lockMode", "soft") == "hard":
            self._hard_radio.setChecked(True)
        else:
            self._soft_radio.setChecked(True)
        lock_row.addWidget(self._soft_radio)
        lock_row.addWidget(self._hard_radio)
        lock_row.addStretch(1)
        subscreen_layout.addLayout(lock_row)

        subscreen_layout.addWidget(_bold_label("Process blocklist (for this event)", size=8))
        apps = installed_apps.list_installed_apps()
        # A brand-new "Integrate with Focus Timer" toggle (no per-event
        # profile saved yet) defaults to the global blocklist, same as the
        # Tk version -- an event with its own saved profile keeps exactly
        # what was checked for it.
        if existing_focus:
            default_processes = existing_focus.get("processBlocklist", [])
        else:
            default_processes = config.load_config().get("processBlocklist", [])
        existing_process_set = {p.lower() for p in default_processes}
        process_widget, self._process_checks, process_add_row = checklist.build_checklist(
            apps, existing_process_set,
            key_fn=lambda a: a["process_name"], label_fn=lambda a: f"{a['display_name']} ({a['process_name']})",
        )
        subscreen_layout.addWidget(process_widget)

        # A previously saved process the installed-apps scan doesn't find
        # (manually typed, or since uninstalled) still needs its own row,
        # pre-checked, or get_checked() at Save time would silently drop it.
        scanned_lower = {a["process_name"].lower() for a in apps}
        for process_name in default_processes:
            if process_name.lower() not in scanned_lower:
                process_add_row(process_name, process_name, checked=True)

        process_manual_row = QHBoxLayout()
        self._process_manual_edit = QLineEdit()
        process_manual_row.addWidget(self._process_manual_edit)
        process_manual_add = QPushButton("Add")
        process_manual_add.clicked.connect(
            lambda: self._add_manual_process(process_add_row)
        )
        process_manual_row.addWidget(process_manual_add)
        subscreen_layout.addLayout(process_manual_row)

        subscreen_layout.addWidget(
            _bold_label("Domain allow list (for this event, sent to the browser extension)", size=8)
        )
        global_domains = config.load_config().get("domainWhitelist", [])
        existing_domains = list((existing_focus or {}).get("domainWhitelist", []))
        existing_domains_lower = {d.lower() for d in existing_domains}
        domain_items = list(existing_domains)
        for domain in global_domains:
            if domain.lower() not in existing_domains_lower:
                domain_items.append(domain)
        domain_checked = existing_domains_lower if existing_focus else {d.lower() for d in global_domains}
        domain_widget, self._domain_checks, domain_add_row = checklist.build_checklist(
            domain_items, domain_checked,
        )
        subscreen_layout.addWidget(domain_widget)

        domain_manual_row = QHBoxLayout()
        self._domain_manual_edit = QLineEdit()
        domain_manual_row.addWidget(self._domain_manual_edit)
        domain_manual_add = QPushButton("Add")
        domain_manual_add.clicked.connect(lambda: self._add_manual_domain(domain_add_row))
        domain_manual_row.addWidget(domain_manual_add)
        subscreen_layout.addLayout(domain_manual_row)

        warn_row = QHBoxLayout()
        self._warn_check = QCheckBox("Warn")
        self._warn_check.setChecked(
            (existing_focus or {}).get("warningMinutes") is not None if existing_focus else True
        )
        warn_row.addWidget(self._warn_check)
        self._warn_minutes_edit = QLineEdit(str((existing_focus or {}).get("warningMinutes", 5) if existing_focus else 5))
        self._warn_minutes_edit.setFixedWidth(40)
        warn_row.addWidget(self._warn_minutes_edit)
        warn_row.addWidget(QLabel("minute(s) before start"))
        warn_row.addStretch(1)
        subscreen_layout.addLayout(warn_row)

        layout.addWidget(self._focus_subscreen)
        self._toggle_focus_subscreen(self._focus_enabled_check.isChecked())

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

    def _toggle_focus_subscreen(self, checked):
        self._focus_subscreen.setVisible(checked)

    # --- save / delete ---

    def _save(self):
        title = self._title_edit.text().strip()
        if not title:
            self._status_label.setText("Title is required.")
            return
        start_dt = datetime.combine(
            self._start_date_edit.date().toPython(), datetime.min.time()
        ) + timedelta(minutes=self._start_time_slider.value())
        end_dt = datetime.combine(
            self._end_date_edit.date().toPython(), datetime.min.time()
        ) + timedelta(minutes=self._end_time_slider.value())
        if end_dt <= start_dt:
            self._status_label.setText("End must be after start.")
            return

        kind = self._recur_kind
        if kind == "custom":
            try:
                interval = int(self._interval_edit.text())
            except ValueError:
                interval = 1
            base_kind = "daily" if self._unit_combo.currentText() == "days" else "weekly"
            rrule_str = recurrence.build_rrule(base_kind, interval=interval)
        elif kind == "weekly_days":
            selected_days = [code for code, chk in self._weekday_checks.items() if chk.isChecked()]
            rrule_str = recurrence.build_rrule("weekly_days", interval=1, weekdays=selected_days)
        elif kind == "none":
            rrule_str = None
        else:
            rrule_str = recurrence.build_rrule(kind, interval=1)

        focus_profile = None
        if self._focus_enabled_check.isChecked():
            try:
                warn_minutes = int(self._warn_minutes_edit.text()) if self._warn_check.isChecked() else None
            except ValueError:
                warn_minutes = None
            focus_profile = {
                "enabled": True,
                "lockMode": "hard" if self._hard_radio.isChecked() else "soft",
                "processBlocklist": checklist.get_checked(self._process_checks),
                "domainWhitelist": checklist.get_checked(self._domain_checks),
                "warningMinutes": warn_minutes,
            }

        event = {
            "id": self._existing["id"] if self._existing else None,
            "title": title,
            "start": start_dt.isoformat(),
            "end": end_dt.isoformat(),
            "allDay": self._all_day_check.isChecked(),
            "color": self._color,
            "notes": self._notes_edit.toPlainText().strip(),
            "rrule": rrule_str,
            "reminderOffsets": list(self._reminders_list),
            "focusProfile": focus_profile,
        }
        saved_id = store.save_event(event)
        if saved_id is None:
            self._status_label.setText("Failed to save — see calendar_errors.log.")
            return
        self.close()
        _refresh_open_views()

    def _delete(self):
        if not self._existing:
            self.close()
            return
        reply = QMessageBox.question(
            self, "Delete event", f"Delete '{self._existing['title']}'?",
            QMessageBox.Yes | QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return
        store.soft_delete_event(self._existing["id"])
        event_id, title = self._existing["id"], self._existing["title"]
        self.close()
        _refresh_open_views()
        _show_undo_toast(event_id, title)


def _bold_label(text, size=9):
    label = QLabel(text)
    label.setStyleSheet(f"font-weight: bold; font-size: {size}pt;")
    return label


def _build_datetime_picker(layout, label_text, default_dt):
    """Adds a "<label_text>" row: a mini-calendar date picker plus a
    time-of-day slider (15-minute steps) with a live HH:MM readout.
    Returns (date_edit, time_slider) so the caller can read them back at
    save time."""
    layout.addWidget(_bold_label(label_text))

    date_edit = QDateEdit(QDate(default_dt.year, default_dt.month, default_dt.day))
    date_edit.setCalendarPopup(True)
    date_edit.setDisplayFormat("yyyy-MM-dd")
    layout.addWidget(date_edit)

    time_row = QHBoxLayout()
    time_slider = QSlider(Qt.Horizontal)
    time_slider.setRange(0, 23 * 60 + 45)
    time_slider.setSingleStep(15)
    time_slider.setPageStep(15)
    time_slider.setValue(default_dt.hour * 60 + (default_dt.minute // 15) * 15)
    time_row.addWidget(time_slider, 1)

    time_label = QLabel()
    time_label.setFixedWidth(48)

    def _update_label(minutes):
        time_label.setText(f"{minutes // 60:02d}:{minutes % 60:02d}")

    time_slider.valueChanged.connect(_update_label)
    _update_label(time_slider.value())
    time_row.addWidget(time_label)
    layout.addLayout(time_row)

    return date_edit, time_slider


def _prefill_recurrence_from_rrule(rrule_str, weekday_checks):
    """Returns (kind, interval, unit), ported verbatim from
    calendar_gui.py's _prefill_recurrence_from_rrule -- also checks the
    matching boxes in weekday_checks for a weekly_days rule as a side
    effect, same as the original taking Tk BooleanVars to mutate."""
    kind, interval, unit = "none", 1, "weeks"
    try:
        parts = dict(p.split("=") for p in rrule_str.split(";"))
        freq = parts.get("FREQ", "").lower()
        rule_interval = int(parts.get("INTERVAL", 1))
        if "BYDAY" in parts:
            kind = "weekly_days"
            for code in parts["BYDAY"].split(","):
                if code in weekday_checks:
                    weekday_checks[code].setChecked(True)
        elif rule_interval > 1 and freq in ("daily", "weekly"):
            kind = "custom"
            interval = rule_interval
            unit = "days" if freq == "daily" else "weeks"
        elif freq in ("daily", "weekly", "monthly", "yearly"):
            kind = freq
    except Exception:
        pass
    return kind, interval, unit


def _refresh_open_views():
    import qt_ui.main_window as main_window
    main_window.refresh_calendar_views()


def _show_undo_toast(event_id, title):
    toast = QWidget(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
    toast.setStyleSheet("background: #1e1e1e;")
    width, height = 320, 60
    screen = QApplication.primaryScreen().availableGeometry()
    toast.setGeometry(screen.x() + screen.width() - width - 24, screen.y() + 24, width, height)

    layout = QHBoxLayout(toast)
    label = QLabel(f'Deleted "{title}"')
    label.setStyleSheet("color: white;")
    layout.addWidget(label)

    def undo():
        store.undo_delete_event(event_id)
        _refresh_open_views()
        toast.close()

    undo_button = QPushButton("Undo")
    undo_button.setStyleSheet("background: #3a3a3a; color: white; border: none; padding: 4px 10px;")
    undo_button.clicked.connect(undo)
    layout.addWidget(undo_button)

    _open_windows.add(toast)
    toast.destroyed.connect(lambda: _open_windows.discard(toast))
    toast.show()
    QTimer.singleShot(10000, toast.close)
