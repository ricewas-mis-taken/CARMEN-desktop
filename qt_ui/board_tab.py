"""The Board: a flat, importance-ranked task list distinct from the Tasks
tab's recurring-with-minutes tracker. A board task is just a name + a 1-10
importance rank (list always sorted highest-first) + optional info (any mix
of text/photo/link, shown together in the detail popup) + an optional set of
weekdays it recurs on.

Detail popup layout borrows from qt_ui/review_tab.py's problem popup, but a
board task has no "review count" -- the popup shows the date it was first
opened instead of a review history.

Both "finishing" a fresh Add Task form and marking an existing task done
trigger qt_ui/confetti.py's screen-wide confetti fall.
"""
import os
from datetime import datetime

from PySide6.QtCore import Qt, QTimer, QUrl
from PySide6.QtGui import QDesktopServices, QPixmap
from PySide6.QtWidgets import (
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

import board_store
from qt_ui.confetti import show_confetti
from tasks_store import WEEKDAY_CODES

REACTIVATE_CHECK_MS = 60_000

_DAY_LABELS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def _format_date(iso_string):
    if not iso_string:
        return None
    try:
        parsed = datetime.fromisoformat(iso_string)
    except ValueError:
        return iso_string
    # %-d (no leading zero) is a Linux/macOS-only strftime flag -- Windows
    # (this app's target platform) raises ValueError on it, so the day is
    # formatted by hand instead.
    return f"{parsed.strftime('%b')} {parsed.day}, {parsed.year}"


def _recurring_text(recurring_days):
    if not recurring_days:
        return None
    ordered = [label for code, label in zip(WEEKDAY_CODES, _DAY_LABELS) if code in recurring_days]
    return "Repeats: " + ", ".join(ordered)


def _build_info_content(layout, task):
    """Unlike review_tab's single-description-type popup, a board task's
    text/photo/link fields are all independently optional -- render
    whichever ones are actually filled in, in order."""
    any_content = False

    text = (task.get("descriptionText") or "").strip()
    if text:
        any_content = True
        text_view = QTextEdit()
        text_view.setReadOnly(True)
        text_view.setPlainText(text)
        text_view.setMaximumHeight(160)
        layout.addWidget(text_view)

    photo_path = task.get("descriptionPhotoPath")
    if photo_path:
        pixmap = QPixmap(photo_path) if os.path.exists(photo_path) else None
        if pixmap and not pixmap.isNull():
            any_content = True
            image_label = QLabel()
            image_label.setAlignment(Qt.AlignCenter)
            image_label.setPixmap(pixmap.scaled(420, 300, Qt.KeepAspectRatio, Qt.SmoothTransformation))
            layout.addWidget(image_label)

    link = (task.get("descriptionLink") or "").strip()
    if link:
        any_content = True
        link_label = QLabel(f'<a style="color: #1F2328;" href="{link}">{link}</a>')
        link_label.setOpenExternalLinks(False)
        link_label.linkActivated.connect(lambda url: QDesktopServices.openUrl(QUrl(url)))
        link_label.setWordWrap(True)
        layout.addWidget(link_label)

    if not any_content:
        empty_label = QLabel("No extra info added.")
        empty_label.setStyleSheet("color: #8A8F98;")
        layout.addWidget(empty_label)


class BoardTab(QWidget):
    def __init__(self):
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 20)
        layout.setSpacing(12)

        layout.addLayout(self._build_header())

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        scroll.setStyleSheet("background: #FFFFFF; border: none;")
        self._list_container = QWidget()
        self._list_layout = QVBoxLayout(self._list_container)
        self._list_layout.setSpacing(12)
        self._list_layout.setAlignment(Qt.AlignTop)
        scroll.setWidget(self._list_container)
        layout.addWidget(scroll, 1)

        self.refresh()

        # Recurring board tasks reactivate on their next scheduled weekday --
        # this periodic refresh is what notices that without requiring the
        # user to switch tabs and back.
        self._reactivate_timer = QTimer(self)
        self._reactivate_timer.timeout.connect(self.refresh)
        self._reactivate_timer.start(REACTIVATE_CHECK_MS)

    def _build_header(self):
        header = QHBoxLayout()
        title = QLabel("The Board")
        title.setStyleSheet("font-size: 20px; font-weight: 700;")
        header.addWidget(title)
        header.addStretch(1)

        self._finished_button = QPushButton("Finished")
        self._finished_button.setProperty("class", "SecondaryButton")
        self._finished_button.clicked.connect(self._open_finished_dialog)
        header.addWidget(self._finished_button)

        add_button = QPushButton("+ Add Task")
        add_button.setProperty("class", "AccentButton")
        add_button.clicked.connect(self._open_add_dialog)
        header.addWidget(add_button)
        return header

    def refresh(self):
        while self._list_layout.count():
            item = self._list_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

        tasks = board_store.list_active_tasks()
        if not tasks:
            empty_label = QLabel("No tasks yet -- click “+ Add Task” to create one.")
            empty_label.setStyleSheet("color: #8A8F98; font-size: 14px;")
            self._list_layout.addWidget(empty_label)
        for task in tasks:
            self._list_layout.addWidget(_BoardCard(task, on_changed=self.refresh))

        finished_count = len(board_store.list_finished_tasks())
        self._finished_button.setText(f"Finished ({finished_count})" if finished_count else "Finished")

    def _open_add_dialog(self):
        _AddTaskDialog(on_added=lambda _t: self.refresh())

    def _open_finished_dialog(self):
        _FinishedListDialog(on_changed=self.refresh)


class _BoardCard(QFrame):
    def __init__(self, task, on_changed):
        super().__init__()
        self._task = task
        self._on_changed = on_changed
        self.setProperty("class", "BoardCard")
        self.setAttribute(Qt.WA_StyledBackground, True)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(18, 14, 18, 14)
        layout.setSpacing(14)

        badge = QLabel(str(task["importance"]))
        badge.setFixedSize(40, 40)
        badge.setAlignment(Qt.AlignCenter)
        badge.setStyleSheet(
            "background: #5B8DEF; color: white; border-radius: 20px; "
            "font-size: 16px; font-weight: 700;"
        )
        layout.addWidget(badge)

        text_col = QVBoxLayout()
        text_col.setSpacing(2)
        name_label = QLabel(task["name"])
        name_label.setStyleSheet("font-size: 16px; font-weight: 600; color: #1F2328;")
        text_col.addWidget(name_label)

        recurring_text = _recurring_text(task.get("recurringDays"))
        if recurring_text:
            recur_label = QLabel(f"🔁 {recurring_text}")
            recur_label.setStyleSheet("font-size: 12px; color: #5A6070;")
            text_col.addWidget(recur_label)
        layout.addLayout(text_col, 1)

        details_button = QPushButton("View Details")
        details_button.setProperty("class", "SecondaryButton")
        details_button.clicked.connect(self._open_details)
        layout.addWidget(details_button)

    def _open_details(self):
        _DetailPopup(self._task["id"], on_changed=self._on_changed)


class _DetailPopup(QWidget):
    def __init__(self, task_id, on_changed=None, read_only=False):
        super().__init__(None, Qt.WindowStaysOnTopHint)
        self.setObjectName("PopupBg")
        self._task_id = task_id
        self._on_changed = on_changed

        task = board_store.get_task(task_id) if read_only else board_store.mark_opened(task_id)
        if task is None:
            # Never shown, so nothing to close -- the task vanished (deleted
            # elsewhere) between the card being drawn and this click.
            return

        self.setWindowTitle(task["name"])
        self.resize(460, 420)

        layout = QVBoxLayout(self)

        header_row = QHBoxLayout()
        name_label = QLabel(task["name"])
        name_label.setStyleSheet("font-size: 18px; font-weight: 700;")
        header_row.addWidget(name_label, 1)
        importance_label = QLabel(f"Importance: {task['importance']}/10")
        importance_label.setStyleSheet("color: #5B8DEF; font-size: 13px; font-weight: 600;")
        header_row.addWidget(importance_label)
        layout.addLayout(header_row)

        opened_text = _format_date(task.get("firstOpenedAt")) or "Just now"
        opened_label = QLabel(f"Opened: {opened_text}")
        opened_label.setStyleSheet("color: #5A6070; font-size: 12px;")
        layout.addWidget(opened_label)

        recurring_text = _recurring_text(task.get("recurringDays"))
        if recurring_text:
            recur_label = QLabel(recurring_text)
            recur_label.setStyleSheet("color: #5A6070; font-size: 12px;")
            layout.addWidget(recur_label)

        if task.get("finished"):
            finished_label = QLabel(f"Completed: {_format_date(task.get('finishedAt'))}")
            finished_label.setStyleSheet("color: #43a047; font-size: 12px; font-weight: 600;")
            layout.addWidget(finished_label)
            next_due = _format_date(task.get("nextDueDate"))
            if next_due:
                next_label = QLabel(f"Next due: {next_due}")
                next_label.setStyleSheet("color: #5A6070; font-size: 12px;")
                layout.addWidget(next_label)

        _build_info_content(layout, task)

        if not read_only and not task.get("finished"):
            done_button = QPushButton("Mark Done")
            done_button.setStyleSheet(
                "background: #28a745; color: white; font-weight: 600; "
                "border-radius: 8px; padding: 8px 20px; font-size: 13px;"
            )
            done_button.clicked.connect(self._mark_done)
            layout.addWidget(done_button)

        self.show()
        _register_popup(self)

    def _mark_done(self):
        board_store.finish_task(self._task_id)
        self.close()
        show_confetti()
        if self._on_changed:
            self._on_changed()


class _FinishedListDialog(QWidget):
    def __init__(self, on_changed=None):
        super().__init__(None, Qt.WindowStaysOnTopHint)
        self.setObjectName("PopupBg")
        self.setWindowTitle("Finished Tasks")
        self.resize(420, 480)

        layout = QVBoxLayout(self)
        layout.addWidget(_bold_label("Finished Tasks"))

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        list_container = QWidget()
        list_layout = QVBoxLayout(list_container)
        list_layout.setSpacing(8)
        list_layout.setAlignment(Qt.AlignTop)

        finished = board_store.list_finished_tasks()
        if not finished:
            empty_label = QLabel("Nothing finished yet.")
            empty_label.setStyleSheet("color: #8A8F98;")
            list_layout.addWidget(empty_label)
        for task in finished:
            row_button = QPushButton(task["name"])
            row_button.setProperty("class", "SecondaryButton")
            row_button.clicked.connect(lambda checked=False, t=task: self._open_detail(t["id"]))
            list_layout.addWidget(row_button)

        scroll.setWidget(list_container)
        layout.addWidget(scroll, 1)

        close_button = QPushButton("Close")
        close_button.clicked.connect(self.close)
        layout.addWidget(close_button)

        self.show()
        _register_popup(self)

    def _open_detail(self, task_id):
        # Read-only: no Mark Done button, so nothing here can change the
        # underlying data -- no on_changed callback needed.
        _DetailPopup(task_id, read_only=True)


class _AddTaskDialog(QWidget):
    def __init__(self, on_added):
        super().__init__(None, Qt.WindowStaysOnTopHint)
        self.setObjectName("PopupBg")
        self.setWindowTitle("Carmen Focus — Add Board Task")
        self.resize(440, 620)
        self._on_added = on_added
        self._photo_path = None
        self._importance = 5

        layout = QVBoxLayout(self)

        layout.addWidget(_bold_label("Name"))
        self._name_edit = QLineEdit()
        layout.addWidget(self._name_edit)

        layout.addWidget(_bold_label("Importance"))
        self._importance_row, self._importance_buttons = self._build_importance_picker()
        layout.addLayout(self._importance_row)

        layout.addWidget(_bold_label("Repeats on (optional)"))
        day_row = QHBoxLayout()
        self._day_buttons = {}
        for code, label in zip(WEEKDAY_CODES, _DAY_LABELS):
            btn = QPushButton(label)
            btn.setCheckable(True)
            btn.setProperty("class", "SecondaryButton")
            day_row.addWidget(btn)
            self._day_buttons[code] = btn
        layout.addLayout(day_row)

        layout.addWidget(_bold_label("Info (any mix of text, photo, link)"))
        self._text_edit = QTextEdit()
        self._text_edit.setPlaceholderText("Text (optional)")
        self._text_edit.setMaximumHeight(120)
        layout.addWidget(self._text_edit)

        photo_row = QHBoxLayout()
        choose_button = QPushButton("Choose Photo")
        choose_button.clicked.connect(self._choose_photo)
        photo_row.addWidget(choose_button)
        self._photo_preview = QLabel("No photo selected.")
        self._photo_preview.setAlignment(Qt.AlignCenter)
        self._photo_preview.setFixedHeight(120)
        photo_row.addWidget(self._photo_preview, 1)
        layout.addLayout(photo_row)

        self._link_edit = QLineEdit()
        self._link_edit.setPlaceholderText("Link (optional)")
        layout.addWidget(self._link_edit)

        self._status_label = QLabel()
        self._status_label.setStyleSheet("color: #c62828;")
        layout.addWidget(self._status_label)

        button_row = QHBoxLayout()
        button_row.addStretch(1)
        cancel_button = QPushButton("Cancel")
        cancel_button.clicked.connect(self.close)
        button_row.addWidget(cancel_button)
        finish_button = QPushButton("Finish")
        finish_button.setProperty("class", "AccentButton")
        finish_button.clicked.connect(self._submit)
        button_row.addWidget(finish_button)
        layout.addLayout(button_row)

        self.show()
        _register_popup(self)

    def _build_importance_picker(self):
        row = QHBoxLayout()
        row.setSpacing(4)
        buttons = {}
        for n in range(1, 11):
            btn = QPushButton(str(n))
            btn.setFixedSize(30, 30)
            btn.setCheckable(True)
            btn.setStyleSheet(
                "QPushButton { border-radius: 15px; font-size: 12px; font-weight: 600; "
                "background: #E5E8EF; color: #1F2328; border: none; padding: 0; }"
                "QPushButton:checked { background: #5B8DEF; color: white; }"
            )
            btn.clicked.connect(lambda checked=False, n=n: self._set_importance(n))
            row.addWidget(btn)
            buttons[n] = btn
        buttons[5].setChecked(True)
        return row, buttons

    def _set_importance(self, n):
        self._importance = n
        for value, btn in self._importance_buttons.items():
            btn.setChecked(value == n)

    def _choose_photo(self):
        path, _ = QFileDialog.getOpenFileName(self, "Choose Image", "", "Images (*.png *.jpg *.jpeg *.gif *.bmp)")
        if not path:
            return
        self._photo_path = path
        pixmap = QPixmap(path)
        if not pixmap.isNull():
            self._photo_preview.setPixmap(pixmap.scaled(200, 120, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        else:
            self._photo_preview.setText("Could not preview this image.")

    def _submit(self):
        name = self._name_edit.text().strip()
        if not name:
            self._status_label.setText("Name is required.")
            return

        recurring_days = [code for code, btn in self._day_buttons.items() if btn.isChecked()]

        photo_bytes = None
        photo_filename = None
        if self._photo_path:
            with open(self._photo_path, "rb") as f:
                photo_bytes = f.read()
            photo_filename = os.path.basename(self._photo_path)

        task = board_store.create_task(
            name,
            self._importance,
            recurring_days=recurring_days,
            description_text=self._text_edit.toPlainText().strip(),
            description_link=self._link_edit.text().strip(),
            photo_bytes=photo_bytes,
            photo_filename=photo_filename,
        )

        self.close()
        show_confetti()
        self._on_added(task)


_popup_refs = set()


def _register_popup(popup):
    _popup_refs.add(popup)
    popup.destroyed.connect(lambda: _popup_refs.discard(popup))
    return popup


def _bold_label(text):
    label = QLabel(text)
    label.setStyleSheet("font-weight: 700;")
    return label
