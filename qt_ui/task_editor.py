"""Add/edit dialog for a Tasks-tab task -- name, color, daily target minutes,
recurrence (every day / specific weekdays), soft/hard lock mode, and the
process block list / domain allow list to apply while it's running. Mirrors
the shape of qt_ui/event_editor.py's focus-integration section (same
checklist component, same list defaulting from config.load_config()) but
without any of the calendar-event scheduling fields this doesn't need.

Non-modal (.show()), same convention as the rest of this app's dialogs.
"""
import os

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QVBoxLayout,
    QWidget,
)

import config
import installed_apps
import tasks_store
import qt_ui.checklist as checklist

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


def _bold_label(text):
    label = QLabel(text)
    label.setStyleSheet("font-weight: 700;")
    return label


class _TaskEditor(QWidget):
    def __init__(self, task, on_saved):
        super().__init__(None, Qt.WindowStaysOnTopHint)
        self.setObjectName("PopupBg")
        self._task = task
        self._on_saved = on_saved
        self._color = (task or {}).get("color", COLOR_PALETTE[0])

        self.setWindowTitle("Carmen Focus — Edit Task" if task else "Carmen Focus — New Task")
        self.resize(440, 620)

        layout = QVBoxLayout(self)

        layout.addWidget(_bold_label("Name"))
        self._name_edit = QLineEdit((task or {}).get("name", ""))
        layout.addWidget(self._name_edit)

        layout.addWidget(_bold_label("Color"))
        self._swatch_row = QHBoxLayout()
        self._swatch_buttons = {}
        for hexval in COLOR_PALETTE:
            btn = QPushButton()
            btn.setFixedSize(24, 24)
            btn.clicked.connect(lambda checked=False, h=hexval: self._pick_color(h))
            self._swatch_buttons[hexval] = btn
            self._swatch_row.addWidget(btn)
        self._swatch_row.addStretch(1)
        layout.addLayout(self._swatch_row)
        self._pick_color(self._color)

        layout.addWidget(_bold_label("Target minutes per active day"))
        self._target_edit = QLineEdit(str((task or {}).get("targetMinutes", 30)))
        layout.addWidget(self._target_edit)

        layout.addWidget(_bold_label("Repeats"))
        recur_row = QHBoxLayout()
        self._every_day_radio = QRadioButton("Every day")
        self._specific_days_radio = QRadioButton("Specific weekdays")
        recur_group = QButtonGroup(self)
        recur_group.addButton(self._every_day_radio)
        recur_group.addButton(self._specific_days_radio)
        recurrence = (task or {}).get("recurrence", "daily")
        if recurrence == "weekly_days":
            self._specific_days_radio.setChecked(True)
        else:
            self._every_day_radio.setChecked(True)
        self._every_day_radio.toggled.connect(self._toggle_weekday_row)
        recur_row.addWidget(self._every_day_radio)
        recur_row.addWidget(self._specific_days_radio)
        recur_row.addStretch(1)
        layout.addLayout(recur_row)

        self._weekday_row = QWidget()
        weekday_layout = QHBoxLayout(self._weekday_row)
        weekday_layout.setContentsMargins(0, 4, 0, 0)
        saved_weekdays = set((task or {}).get("weekdays", []))
        self._weekday_checks = {}
        for code in tasks_store.WEEKDAY_CODES:
            check = QCheckBox(code)
            check.setChecked(code in saved_weekdays)
            self._weekday_checks[code] = check
            weekday_layout.addWidget(check)
        layout.addWidget(self._weekday_row)
        self._toggle_weekday_row(self._every_day_radio.isChecked())

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
        apps = installed_apps.list_installed_apps()
        default_processes = (task or {}).get("processBlocklist") if task else config.load_config().get("processBlocklist", [])
        existing_process_set = {p.lower() for p in (default_processes or [])}
        process_widget, self._process_checks, process_add_row = checklist.build_checklist(
            apps, existing_process_set, height=160,
            key_fn=lambda a: a["process_name"], label_fn=lambda a: f"{a['display_name']} ({a['process_name']})",
        )
        layout.addWidget(process_widget)
        scanned_lower = {a["process_name"].lower() for a in apps}
        for process_name in (default_processes or []):
            if process_name.lower() not in scanned_lower:
                process_add_row(process_name, process_name, checked=True)

        process_manual_row = QHBoxLayout()
        self._process_manual_edit = QLineEdit()
        process_manual_row.addWidget(self._process_manual_edit)
        process_manual_add = QPushButton("Add")
        process_manual_add.clicked.connect(lambda: self._add_manual_process(process_add_row))
        process_manual_row.addWidget(process_manual_add)
        layout.addLayout(process_manual_row)

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

        self._status_label = QLabel()
        self._status_label.setStyleSheet("color: #c62828;")
        layout.addWidget(self._status_label)

        button_row = QHBoxLayout()
        if task:
            delete_button = QPushButton("Delete Task")
            delete_button.clicked.connect(self._delete)
            button_row.addWidget(delete_button)
        button_row.addStretch(1)
        cancel_button = QPushButton("Cancel")
        cancel_button.clicked.connect(self.close)
        button_row.addWidget(cancel_button)
        save_button = QPushButton("Save Task")
        save_button.setProperty("class", "AccentButton")
        save_button.clicked.connect(self._save)
        button_row.addWidget(save_button)
        layout.addLayout(button_row)

    def _toggle_weekday_row(self, every_day_checked):
        self._weekday_row.setVisible(not every_day_checked)

    def _pick_color(self, hexval):
        self._color = hexval
        for h, btn in self._swatch_buttons.items():
            border = "2px solid #1F2328" if h == hexval else "1px solid #0002"
            btn.setStyleSheet(f"background: {h}; border-radius: 4px; border: {border};")

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
        tasks_store.delete_task(self._task["id"])
        self.close()
        if self._on_saved is not None:
            self._on_saved(None)

    def _save(self):
        name = self._name_edit.text().strip()
        if not name:
            self._status_label.setText("Name is required.")
            return
        try:
            target_minutes = int(self._target_edit.text().strip())
            if target_minutes <= 0:
                raise ValueError
        except ValueError:
            self._status_label.setText("Target minutes must be a positive whole number.")
            return

        if self._specific_days_radio.isChecked():
            recurrence_kind = "weekly_days"
            weekdays = [code for code, chk in self._weekday_checks.items() if chk.isChecked()]
            if not weekdays:
                self._status_label.setText("Pick at least one weekday.")
                return
        else:
            recurrence_kind = "daily"
            weekdays = []

        data = {
            "name": name,
            "color": self._color,
            "targetMinutes": target_minutes,
            "recurrence": recurrence_kind,
            "weekdays": weekdays,
            "lockMode": "hard" if self._hard_radio.isChecked() else "soft",
            "processBlocklist": checklist.get_checked(self._process_checks),
            "domainWhitelist": checklist.get_checked(self._domain_checks),
        }

        if self._task:
            saved = tasks_store.update_task(self._task["id"], data)
        else:
            saved = tasks_store.create_task(data)

        self.close()
        if self._on_saved is not None:
            self._on_saved(saved)
