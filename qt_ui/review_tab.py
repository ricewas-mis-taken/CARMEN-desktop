"""Review tab: a spaced-repetition tracker for missed homework/practice
problems. Topics (review_store's review_topics) are chrome-style tabs across
the top -- built with a real QTabWidget rather than qt_ui's own hand-rolled
sidebar nav, since "browser-style tabs with a trailing + tab" is exactly what
QTabWidget already models. Inside each topic, problems are grouped by
Subject (a finer, color-coded tag) and shown in a table sourced from
review_store.list_problems(), the same "presentation here, persistence/
scheduling in a plain store module" split qt_ui/tasks_tab.py uses for
tasks_store.py.

Talks directly to review_store.py (no HTTP) -- same convention every other
tab in this app follows; api_server.py's /review/* routes exist for the
browser extension or other external callers, not for this UI.
"""
import os
import re
from datetime import date, datetime

from PySide6.QtCore import Qt, QTimer, QUrl
from PySide6.QtGui import QColor, QDesktopServices, QPixmap
from PySide6.QtWidgets import (
    QButtonGroup,
    QComboBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QPushButton,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

import review_store
import session_manager
import tasks_store

COLOR_PALETTE = [
    "#5B8DEF", "#e53935", "#43a047", "#fb8c00", "#8e24aa",
    "#00acc1", "#f4511e", "#3949ab", "#6d4c41", "#546e7a",
]

COLUMN_NAME, COLUMN_SUBJECT, COLUMN_STARS, COLUMN_REVIEWS, \
    COLUMN_LAST_REVIEWED, COLUMN_FIRST_SOLVED, COLUMN_FASTEST, COLUMN_START = range(8)
COLUMN_HEADERS = [
    "Problem Name", "Subject", "Stars", "Reviews",
    "Last Reviewed", "First Solved", "Fastest Time", "",
]

_URL_RE = re.compile(r"^https?://[^\s]+\.[^\s]+$", re.IGNORECASE)


def _lighten(hex_color, mix=0.78):
    """Blends a subject's saturated color most of the way to white, so a
    row's background reads as a soft tint (subject identity at a glance)
    without fighting the row's own text -- much heavier mix than
    tasks_tab.py's card pastelization since a full table row of solid color
    would be far louder than one big card."""
    hex_color = (hex_color or "#5B8DEF").lstrip("#")
    if len(hex_color) != 6:
        return "#FFFFFF"
    r, g, b = (int(hex_color[i:i + 2], 16) for i in (0, 2, 4))
    r = round(r + (255 - r) * mix)
    g = round(g + (255 - g) * mix)
    b = round(b + (255 - b) * mix)
    return f"#{r:02X}{g:02X}{b:02X}"


def _star_text(stars):
    return "★" * stars + "☆" * (5 - stars)


def _format_mmss(total_seconds):
    if total_seconds is None:
        return "--:--"
    minutes, seconds = divmod(int(total_seconds), 60)
    return f"{minutes:02d}:{seconds:02d}"


def _format_dmy(iso_date_string):
    d = date.fromisoformat(iso_date_string)
    return f"{d.day}/{d.month}/{d.year}"


def _relative_time(iso_datetime_string):
    if not iso_datetime_string:
        return "Never"
    then = datetime.fromisoformat(iso_datetime_string)
    delta = datetime.now() - then
    seconds = delta.total_seconds()
    if seconds < 60:
        return "Just now"
    if seconds < 3600:
        minutes = int(seconds // 60)
        return f"{minutes} minute{'s' if minutes != 1 else ''} ago"
    if seconds < 86400:
        hours = int(seconds // 3600)
        return f"{hours} hour{'s' if hours != 1 else ''} ago"
    days = int(seconds // 86400)
    return f"{days} day{'s' if days != 1 else ''} ago"


def _build_description_content(layout, problem):
    """Adds the problem's description widget(s) to layout -- shared between
    _DescriptionPopup (read-only view) and _ReviewStartDialog (pre-start
    preview)."""
    description_type = problem["descriptionType"]
    if description_type == "text":
        text_view = QTextEdit()
        text_view.setReadOnly(True)
        text_view.setPlainText(problem.get("descriptionText") or "")
        layout.addWidget(text_view, 1)
    elif description_type == "photo":
        image_label = QLabel()
        image_label.setAlignment(Qt.AlignCenter)
        path = problem.get("descriptionPhotoPath")
        pixmap = QPixmap(path) if path and os.path.exists(path) else None
        if pixmap and not pixmap.isNull():
            image_label.setPixmap(pixmap.scaled(440, 320, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        else:
            image_label.setText("Image not found.")
        layout.addWidget(image_label, 1)
    else:  # link
        link = problem.get("descriptionLink") or ""
        link_label = QLabel(f'<a href="{link}">{link}</a>')
        link_label.setOpenExternalLinks(False)
        link_label.linkActivated.connect(lambda url: QDesktopServices.openUrl(QUrl(url)))
        link_label.setWordWrap(True)
        layout.addWidget(link_label, 1)


class ReviewTab(QWidget):
    def __init__(self):
        super().__init__()
        self._is_reviewing = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        title = QLabel("Review")
        title.setStyleSheet("font-size: 20px; font-weight: 700;")
        title.setContentsMargins(24, 22, 24, 8)
        layout.addWidget(title)

        self._tabs = QTabWidget()
        self._tabs.setDocumentMode(True)
        # tabBarClicked (not currentChanged) -- with zero topics the "+" tab
        # is index 0 and already current the moment it's added, so a click
        # on it wouldn't change the current index and currentChanged would
        # never fire. tabBarClicked fires on every click regardless of
        # whether the index actually changed.
        self._tabs.tabBarClicked.connect(self._on_tab_bar_clicked)
        self._tabs.tabBar().setContextMenuPolicy(Qt.CustomContextMenu)
        self._tabs.tabBar().customContextMenuRequested.connect(self._on_tab_context_menu)
        layout.addWidget(self._tabs, 1)

        self._topic_views = {}
        self._topic_names = {}
        self._plus_index = None
        self.refresh()

    def refresh(self):
        previous_topic_id = self._current_topic_id()

        self._tabs.blockSignals(True)
        while self._tabs.count():
            self._tabs.removeTab(0)
        self._topic_views = {}
        self._topic_names = {}

        topics = review_store.list_topics()
        for topic in topics:
            view = _TopicView(topic["id"], review_tab=self)
            self._topic_views[topic["id"]] = view
            self._topic_names[topic["id"]] = topic["name"]
            label = topic["name"] + (" 🔗" if topic.get("linkedTaskId") else "")
            self._tabs.addTab(view, label)

        self._plus_index = self._tabs.addTab(QWidget(), "+")
        self._tabs.blockSignals(False)

        if previous_topic_id in self._topic_views:
            self._select_topic(previous_topic_id)
        elif topics:
            self._tabs.setCurrentIndex(0)

    def _current_topic_id(self):
        widget = self._tabs.currentWidget()
        for topic_id, view in self._topic_views.items():
            if view is widget:
                return topic_id
        return None

    def _select_topic(self, topic_id):
        view = self._topic_views.get(topic_id)
        if view is not None:
            self._tabs.setCurrentWidget(view)

    def _on_tab_bar_clicked(self, index):
        if index != self._plus_index:
            return
        # The "+" tab is a trigger, not a real destination -- switch back to
        # a real tab (if any) right away rather than actually landing on it,
        # then open the dialog non-modally. _on_topic_added selects the newly
        # created topic once it exists; if the user cancels, whatever real tab
        # was showing just stays showing.
        if self._topic_views:
            self._tabs.setCurrentIndex(0)
        _register_popup(_AddTopicDialog(on_added=self._on_topic_added))

    def _on_topic_added(self, topic):
        if topic is None:
            return
        # Incremental add: insert a new tab before "+" instead of rebuilding
        # everything, so an in-progress review banner isn't orphaned when the
        # user creates a topic mid-session.
        view = _TopicView(topic["id"], review_tab=self)
        self._topic_views[topic["id"]] = view
        self._topic_names[topic["id"]] = topic["name"]
        self._tabs.insertTab(self._plus_index, view, topic["name"])
        self._plus_index = self._tabs.count() - 1
        self._select_topic(topic["id"])

    def _refresh_tab_labels(self):
        """Update tab text 🔗 indicators without a full rebuild."""
        for topic_id, view in self._topic_views.items():
            idx = self._tabs.indexOf(view)
            if idx < 0:
                continue
            topic = review_store.get_topic(topic_id)
            name = self._topic_names.get(topic_id, "")
            has_link = topic and topic.get("linkedTaskId")
            self._tabs.setTabText(idx, name + (" 🔗" if has_link else ""))

    def _topic_id_at_tab(self, index):
        widget = self._tabs.widget(index)
        for topic_id, view in self._topic_views.items():
            if view is widget:
                return topic_id
        return None

    def _on_tab_context_menu(self, pos):
        idx = self._tabs.tabBar().tabAt(pos)
        if idx < 0 or idx == self._plus_index:
            return
        topic_id = self._topic_id_at_tab(idx)
        if topic_id is None:
            return

        menu = QMenu(self)
        rename_action = menu.addAction("Rename tab")
        link_action = menu.addAction("Link to task")
        menu.addSeparator()
        delete_action = menu.addAction("Delete tab")

        # Block destructive/disruptive actions during an active review
        if self._is_reviewing:
            rename_action.setEnabled(False)
            delete_action.setEnabled(False)

        action = menu.exec(self._tabs.tabBar().mapToGlobal(pos))
        if action == rename_action:
            self._rename_topic(topic_id)
        elif action == link_action:
            self._link_topic_to_task(topic_id)
        elif action == delete_action:
            self._delete_topic(topic_id)

    def _rename_topic(self, topic_id):
        current_name = self._topic_names.get(topic_id, "")
        _register_popup(_RenameTopicDialog(topic_id, current_name, on_renamed=self._on_topic_renamed))

    def _on_topic_renamed(self, topic_id, new_name):
        self._topic_names[topic_id] = new_name
        self._refresh_tab_labels()

    def _link_topic_to_task(self, topic_id):
        topic = review_store.get_topic(topic_id)
        if topic:
            _register_popup(_TopicTaskLinkDialog(topic, on_saved=self._refresh_tab_labels))

    def _delete_topic(self, topic_id):
        name = self._topic_names.get(topic_id, "this topic")
        confirm = QMessageBox.question(
            self,
            "Delete topic",
            f'Delete "{name}" and all its subjects and problems? This cannot be undone.',
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if confirm != QMessageBox.Yes:
            return
        review_store.delete_topic(topic_id)
        self.refresh()

    def can_start_review(self):
        return not self._is_reviewing

    def on_review_started(self):
        self._is_reviewing = True

    def on_review_finished(self):
        self._is_reviewing = False


class _AddTopicDialog(QWidget):
    def __init__(self, on_added):
        super().__init__(None, Qt.WindowStaysOnTopHint)
        self.setObjectName("PopupBg")
        self.setWindowTitle("Carmen Focus — New Review Topic")
        self._on_added = on_added
        self.resize(320, 130)

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Topic name"))
        self._name_edit = QLineEdit()
        self._name_edit.setPlaceholderText("e.g. Math")
        self._name_edit.returnPressed.connect(self._create)
        layout.addWidget(self._name_edit)

        self._status_label = QLabel()
        self._status_label.setStyleSheet("color: #c62828;")
        layout.addWidget(self._status_label)

        button_row = QHBoxLayout()
        button_row.addStretch(1)
        cancel_button = QPushButton("Cancel")
        cancel_button.clicked.connect(self.close)
        button_row.addWidget(cancel_button)
        create_button = QPushButton("Create")
        create_button.setProperty("class", "AccentButton")
        create_button.clicked.connect(self._create)
        button_row.addWidget(create_button)
        layout.addLayout(button_row)

        self.show()

    def _create(self):
        name = self._name_edit.text().strip()
        if not name:
            self._status_label.setText("Name is required.")
            return
        topic = review_store.create_topic(name)
        if topic is None:
            self._status_label.setText("Could not create topic.")
            return
        self.close()
        self._on_added(topic)


class _TopicView(QWidget):
    def __init__(self, topic_id, review_tab=None):
        super().__init__()
        self._topic_id = topic_id
        self._due_only = True
        self._problems = []
        self._review_tab = review_tab

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 16, 24, 20)
        layout.setSpacing(10)

        layout.addLayout(self._build_header())

        self._review_banner = _ReviewBanner(on_finished=self._banner_finished)
        layout.addWidget(self._review_banner)

        self._table = QTableWidget(0, len(COLUMN_HEADERS))
        self._table.setHorizontalHeaderLabels(COLUMN_HEADERS)
        self._table.verticalHeader().setVisible(False)
        self._table.setEditTriggers(QTableWidget.NoEditTriggers)
        self._table.setSelectionMode(QTableWidget.NoSelection)
        self._table.horizontalHeader().setStretchLastSection(False)
        self._table.setColumnWidth(COLUMN_NAME, 220)
        self._table.setColumnWidth(COLUMN_SUBJECT, 120)
        self._table.setColumnWidth(COLUMN_STARS, 90)
        self._table.setColumnWidth(COLUMN_REVIEWS, 70)
        self._table.setColumnWidth(COLUMN_LAST_REVIEWED, 120)
        self._table.setColumnWidth(COLUMN_FIRST_SOLVED, 100)
        self._table.setColumnWidth(COLUMN_FASTEST, 100)
        self._table.setColumnWidth(COLUMN_START, 90)
        self._table.cellClicked.connect(self._on_cell_clicked)
        layout.addWidget(self._table, 1)

        self.refresh()

    def _build_header(self):
        header = QHBoxLayout()

        self._due_button = QPushButton("Due")
        self._due_button.setCheckable(True)
        self._due_button.setChecked(True)
        self._due_button.setProperty("class", "SecondaryButton")
        self._all_button = QPushButton("All")
        self._all_button.setCheckable(True)
        self._all_button.setProperty("class", "SecondaryButton")
        toggle_group = QButtonGroup(self)
        toggle_group.setExclusive(True)
        toggle_group.addButton(self._due_button)
        toggle_group.addButton(self._all_button)
        self._due_button.toggled.connect(lambda checked: checked and self._set_due_only(True))
        self._all_button.toggled.connect(lambda checked: checked and self._set_due_only(False))
        header.addWidget(self._due_button)
        header.addWidget(self._all_button)

        header.addStretch(1)

        add_button = QPushButton("+ Add Problem")
        add_button.setProperty("class", "AccentButton")
        add_button.clicked.connect(self._open_add_problem)
        header.addWidget(add_button)

        return header

    def _set_due_only(self, due_only):
        self._due_only = due_only
        self.refresh()

    def refresh(self):
        self._problems = review_store.list_problems(self._topic_id, due_only=self._due_only)
        self._render_table()

    def _render_table(self):
        self._table.setRowCount(len(self._problems))
        for row, problem in enumerate(self._problems):
            tint = _lighten(problem["subjectColor"])

            name_item = QTableWidgetItem(problem["name"])
            self._table.setItem(row, COLUMN_NAME, name_item)
            # QTableWidgetItem text color comes from the app/OS palette, not
            # the #ContentArea QLabel QSS rule (that only targets QLabel) --
            # against a light pastel row tint a light default palette color
            # is unreadable, so every text item gets an explicit dark color.

            reviews_item = QTableWidgetItem(str(problem["reviewCount"]))
            reviews_item.setTextAlignment(Qt.AlignCenter)
            self._table.setItem(row, COLUMN_REVIEWS, reviews_item)

            last_item = QTableWidgetItem(_relative_time(problem["lastReviewedAt"]))
            self._table.setItem(row, COLUMN_LAST_REVIEWED, last_item)

            first_item = QTableWidgetItem(_format_dmy(problem["dateAdded"]))
            self._table.setItem(row, COLUMN_FIRST_SOLVED, first_item)

            fastest_item = QTableWidgetItem(_format_mmss(problem["fastestTimeSeconds"]))
            fastest_item.setTextAlignment(Qt.AlignCenter)
            self._table.setItem(row, COLUMN_FASTEST, fastest_item)

            stars_item = QTableWidgetItem(_star_text(problem["stars"]))
            stars_item.setTextAlignment(Qt.AlignCenter)
            stars_item.setForeground(QColor("#F5A623"))
            self._table.setItem(row, COLUMN_STARS, stars_item)

            subject_item = QTableWidgetItem(problem["subjectName"])
            subject_item.setForeground(QColor("#1F2328"))
            subject_item.setBackground(QColor(tint))
            self._table.setItem(row, COLUMN_SUBJECT, subject_item)

            for col in (COLUMN_NAME, COLUMN_REVIEWS, COLUMN_LAST_REVIEWED, COLUMN_FIRST_SOLVED, COLUMN_FASTEST):
                self._table.item(row, col).setForeground(QColor("#1F2328"))
            for col in (COLUMN_NAME, COLUMN_REVIEWS, COLUMN_LAST_REVIEWED, COLUMN_FIRST_SOLVED, COLUMN_FASTEST, COLUMN_STARS):
                self._table.item(row, col).setBackground(QColor(tint))

            self._table.setCellWidget(row, COLUMN_START, self._build_start_cell(problem, tint))

        self._table.resizeRowsToContents()

    def _build_start_cell(self, problem, tint):
        container = QWidget()
        container.setStyleSheet(f"background: {tint};")
        layout = QHBoxLayout(container)
        layout.setContentsMargins(6, 4, 6, 4)
        start_button = QPushButton("Start")
        start_button.setObjectName("reviewStartButton")
        start_button.clicked.connect(lambda: self._start_problem(problem))
        layout.addWidget(start_button)
        return container

    def _on_cell_clicked(self, row, column):
        if column == COLUMN_START:
            return
        _DescriptionPopup(self._problems[row])

    def _start_problem(self, problem):
        if self._review_tab and not self._review_tab.can_start_review():
            return
        _ReviewStartDialog(problem, on_start=self._begin_review)

    def _begin_review(self, problem):
        token = review_store.start_review(problem["id"])
        if token is None:
            QMessageBox.warning(self, "Carmen Focus", "That problem no longer exists.")
            self.refresh()
            return

        end_session_on_finish = False
        topic = review_store.get_topic(self._topic_id)
        if topic and topic.get("linkedTaskId") and not session_manager.is_active():
            task = tasks_store.get_task(topic["linkedTaskId"])
            if task:
                session_manager.start_session(
                    duration_minutes=tasks_store.BURNOUT_MINUTES,
                    lock_mode=task["lockMode"],
                    process_whitelist=task.get("processWhitelist", []),
                    domain_whitelist=task.get("domainWhitelist", []),
                    source="task",
                    event_id=task["id"],
                    event_title=f"{task['name']} - {problem['subjectName']} review",
                )
                end_session_on_finish = True

        if self._review_tab:
            self._review_tab.on_review_started()
        self._review_banner.start(problem, token, end_session_on_finish=end_session_on_finish)

    def _banner_finished(self):
        if self._review_tab:
            self._review_tab.on_review_finished()
        self.refresh()

    def _open_add_problem(self):
        _register_popup(_AddProblemDialog(self._topic_id, on_added=lambda _p: self.refresh()))


class _ReviewBanner(QWidget):
    """Inline timer shown at the top of _TopicView while a review is active.
    Hidden by default; call start() to activate."""
    def __init__(self, on_finished):
        super().__init__()
        self._on_finished = on_finished
        self._session_token = None
        self._end_session_on_finish = False
        self._elapsed_seconds = 0

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        card = QFrame()
        card.setStyleSheet(
            "QFrame { background: #EAF2FF; border: 1px solid #BDD4F7; border-radius: 10px; }"
        )
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(12, 10, 12, 10)
        card_layout.setSpacing(4)

        self._problem_label = QLabel()
        self._problem_label.setAlignment(Qt.AlignCenter)
        self._problem_label.setStyleSheet(
            "font-size: 13px; font-weight: 600; color: #1F2328; background: transparent; border: none;"
        )
        card_layout.addWidget(self._problem_label)

        self._timer_label = QLabel("00:00")
        self._timer_label.setAlignment(Qt.AlignCenter)
        self._timer_label.setStyleSheet(
            "font-size: 48px; font-weight: 700; color: #1F2328; "
            "letter-spacing: 4px; background: transparent; border: none;"
        )
        card_layout.addWidget(self._timer_label)

        finish_btn = QPushButton("Finish")
        finish_btn.setProperty("class", "AccentButton")
        finish_btn.setFixedWidth(120)
        btn_row = QHBoxLayout()
        btn_row.addStretch(1)
        btn_row.addWidget(finish_btn)
        btn_row.addStretch(1)
        card_layout.addLayout(btn_row)
        finish_btn.clicked.connect(self._finish)

        outer.addWidget(card)

        self._tick_timer = QTimer(self)
        self._tick_timer.timeout.connect(self._tick)

        self.hide()

    def start(self, problem, token, end_session_on_finish=False):
        self._session_token = token
        self._end_session_on_finish = end_session_on_finish
        self._elapsed_seconds = 0
        self._problem_label.setText(f"Reviewing: {problem['name']}")
        self._timer_label.setText("00:00")
        self._tick_timer.start(1000)
        self.show()

    def _tick(self):
        self._elapsed_seconds += 1
        self._timer_label.setText(_format_mmss(self._elapsed_seconds))

    def _finish(self):
        self._tick_timer.stop()
        review_store.finish_review(self._session_token)
        if self._end_session_on_finish:
            session_manager.end_session()
        self._session_token = None
        self._end_session_on_finish = False
        self.hide()
        self._on_finished()


class _ReviewStartDialog(QWidget):
    """Shows a problem's description with a Start button -- replaces the old
    QMessageBox.question so the user sees what they're about to review before
    the inline timer starts."""
    def __init__(self, problem, on_start):
        super().__init__(None, Qt.WindowStaysOnTopHint)
        self.setObjectName("PopupBg")
        self.setWindowTitle(problem["name"])
        self.resize(480, 440)
        self._problem = problem
        self._on_start = on_start

        layout = QVBoxLayout(self)

        name_label = QLabel(problem["name"])
        name_label.setStyleSheet("font-size: 16px; font-weight: 700;")
        layout.addWidget(name_label)

        stars_label = QLabel(_star_text(problem["stars"]))
        stars_label.setStyleSheet("color: #F5A623; font-size: 14px;")
        layout.addWidget(stars_label)

        _build_description_content(layout, problem)

        start_button = QPushButton("Start")
        start_button.setProperty("class", "AccentButton")
        start_button.clicked.connect(self._do_start)
        layout.addWidget(start_button)

        self.show()
        _register_popup(self)

    def _do_start(self):
        self.close()
        self._on_start(self._problem)


class _TopicTaskLinkDialog(QWidget):
    """Right-click context menu → Link to task: links/unlinks the whole topic
    tab to a task so every review under it counts toward that task's session."""
    def __init__(self, topic, on_saved):
        super().__init__(None, Qt.WindowStaysOnTopHint)
        self.setObjectName("PopupBg")
        self.setWindowTitle(f"Link tab — {topic['name']}")
        self.resize(320, 180)
        self._topic = topic
        self._on_saved = on_saved

        layout = QVBoxLayout(self)
        layout.addWidget(_bold_label(f"Link \"{topic['name']}\" to a task"))

        self._task_combo = QComboBox()
        self._task_combo.addItem("No link", None)
        for task in tasks_store.load_tasks():
            if not task.get("archived"):
                self._task_combo.addItem(task["name"], task["id"])
        current_id = topic.get("linkedTaskId")
        if current_id:
            idx = self._task_combo.findData(current_id)
            if idx >= 0:
                self._task_combo.setCurrentIndex(idx)
        layout.addWidget(self._task_combo)

        hint = QLabel("Review time will count toward the linked task's session.")
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #5A6070; font-size: 12px;")
        layout.addWidget(hint)

        btn_row = QHBoxLayout()
        btn_row.addStretch(1)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.close)
        btn_row.addWidget(cancel)
        save = QPushButton("Save")
        save.setProperty("class", "AccentButton")
        save.clicked.connect(self._save)
        btn_row.addWidget(save)
        layout.addLayout(btn_row)

        self.show()
        _register_popup(self)

    def _save(self):
        task_id = self._task_combo.currentData()
        review_store.update_topic_link(self._topic["id"], task_id)
        self.close()
        self._on_saved()


class _RenameTopicDialog(QWidget):
    def __init__(self, topic_id, current_name, on_renamed):
        super().__init__(None, Qt.WindowStaysOnTopHint)
        self.setObjectName("PopupBg")
        self.setWindowTitle("Rename tab")
        self.resize(300, 120)
        self._topic_id = topic_id
        self._on_renamed = on_renamed

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("New name"))
        self._name_edit = QLineEdit(current_name)
        self._name_edit.selectAll()
        self._name_edit.returnPressed.connect(self._save)
        layout.addWidget(self._name_edit)

        btn_row = QHBoxLayout()
        btn_row.addStretch(1)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.close)
        btn_row.addWidget(cancel)
        save = QPushButton("Rename")
        save.setProperty("class", "AccentButton")
        save.clicked.connect(self._save)
        btn_row.addWidget(save)
        layout.addLayout(btn_row)

        self.show()

    def _save(self):
        name = self._name_edit.text().strip()
        if not name:
            return
        review_store.rename_topic(self._topic_id, name)
        self.close()
        self._on_renamed(self._topic_id, name)


class _DescriptionPopup(QWidget):
    def __init__(self, problem):
        super().__init__(None, Qt.WindowStaysOnTopHint)
        self.setObjectName("PopupBg")
        self.setWindowTitle(problem["name"])
        self.resize(480, 400)

        layout = QVBoxLayout(self)

        name_label = QLabel(problem["name"])
        name_label.setStyleSheet("font-size: 16px; font-weight: 700;")
        layout.addWidget(name_label)

        stars_label = QLabel(_star_text(problem["stars"]))
        stars_label.setStyleSheet("color: #F5A623; font-size: 14px;")
        layout.addWidget(stars_label)

        _build_description_content(layout, problem)

        self.show()
        _register_popup(self)


_popup_refs = set()


def _register_popup(popup):
    _popup_refs.add(popup)
    popup.destroyed.connect(lambda: _popup_refs.discard(popup))
    return popup


class _StarPicker(QWidget):
    def __init__(self, initial=3):
        super().__init__()
        self._value = initial
        self._buttons = []
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)
        for n in range(1, 6):
            button = QPushButton()
            button.setFixedSize(28, 28)
            button.setFlat(True)
            # padding: 0 is load-bearing -- the #PopupBg QPushButton global
            # rule (styles.qss) sets 6px/14px padding by default, which on a
            # button this small (28x28) squeezes the star glyph out of the
            # visible area entirely.
            button.setStyleSheet("font-size: 18px; border: none; background: transparent; padding: 0;")
            button.clicked.connect(lambda checked=False, n=n: self._set_value(n))
            layout.addWidget(button)
            self._buttons.append(button)
        self._refresh()

    def value(self):
        return self._value

    def _set_value(self, n):
        self._value = n
        self._refresh()

    def _refresh(self):
        for i, button in enumerate(self._buttons, start=1):
            button.setText("★" if i <= self._value else "☆")
            button.setStyleSheet(
                "font-size: 18px; border: none; background: transparent; padding: 0; "
                f"color: {'#F5A623' if i <= self._value else '#C7CAD3'};"
            )


class _AddProblemDialog(QWidget):
    def __init__(self, topic_id, on_added):
        super().__init__(None, Qt.WindowStaysOnTopHint)
        self.setObjectName("PopupBg")
        self.setWindowTitle("Carmen Focus — Add Problem")
        self.resize(420, 580)
        self._topic_id = topic_id
        self._on_added = on_added
        self._photo_path = None

        layout = QVBoxLayout(self)

        layout.addWidget(_bold_label("Name"))
        self._name_edit = QLineEdit()
        layout.addWidget(self._name_edit)

        layout.addWidget(_bold_label("Subject"))
        subject_row = QHBoxLayout()
        self._subject_combo = QComboBox()
        subject_row.addWidget(self._subject_combo, 1)
        add_subject_button = QPushButton("+ Add Subject")
        add_subject_button.setProperty("class", "SecondaryButton")
        add_subject_button.clicked.connect(self._toggle_add_subject)
        subject_row.addWidget(add_subject_button)
        layout.addLayout(subject_row)
        self._reload_subjects()

        self._add_subject_form = self._build_add_subject_form()
        self._add_subject_form.setVisible(False)
        layout.addWidget(self._add_subject_form)

        layout.addWidget(_bold_label("Stars"))
        self._star_picker = _StarPicker(initial=3)
        layout.addWidget(self._star_picker)

        layout.addWidget(_bold_label("Description"))
        type_row = QHBoxLayout()
        self._text_button = QPushButton("Text")
        self._photo_button = QPushButton("Photo")
        self._link_button = QPushButton("Link")
        for button in (self._text_button, self._photo_button, self._link_button):
            button.setCheckable(True)
            button.setProperty("class", "SecondaryButton")
            type_row.addWidget(button)
        self._type_group = QButtonGroup(self)
        self._type_group.setExclusive(True)
        for button in (self._text_button, self._photo_button, self._link_button):
            self._type_group.addButton(button)
        self._text_button.setChecked(True)
        layout.addLayout(type_row)

        self._description_stack = QStackedWidget()
        self._text_edit = QTextEdit()
        self._description_stack.addWidget(self._text_edit)

        photo_page = QWidget()
        photo_layout = QVBoxLayout(photo_page)
        photo_layout.setContentsMargins(0, 0, 0, 0)
        choose_button = QPushButton("Choose Image")
        choose_button.clicked.connect(self._choose_photo)
        photo_layout.addWidget(choose_button)
        self._photo_preview = QLabel("No image selected.")
        self._photo_preview.setAlignment(Qt.AlignCenter)
        self._photo_preview.setFixedHeight(180)
        photo_layout.addWidget(self._photo_preview)
        self._description_stack.addWidget(photo_page)

        self._link_edit = QLineEdit()
        self._link_edit.setPlaceholderText("https://example.com/problem")
        self._description_stack.addWidget(self._link_edit)

        layout.addWidget(self._description_stack, 1)

        self._text_button.toggled.connect(lambda c: c and self._description_stack.setCurrentIndex(0))
        self._photo_button.toggled.connect(lambda c: c and self._description_stack.setCurrentIndex(1))
        self._link_button.toggled.connect(lambda c: c and self._description_stack.setCurrentIndex(2))

        self._status_label = QLabel()
        self._status_label.setStyleSheet("color: #c62828;")
        layout.addWidget(self._status_label)

        button_row = QHBoxLayout()
        button_row.addStretch(1)
        cancel_button = QPushButton("Cancel")
        cancel_button.clicked.connect(self.close)
        button_row.addWidget(cancel_button)
        add_button = QPushButton("Add")
        add_button.setProperty("class", "AccentButton")
        add_button.clicked.connect(self._submit)
        button_row.addWidget(add_button)
        layout.addLayout(button_row)

        self.show()

    def _reload_subjects(self, select_id=None):
        self._subject_combo.clear()
        for subject in review_store.list_subjects(self._topic_id):
            self._subject_combo.addItem(subject["name"], subject["id"])
        if select_id is not None:
            index = self._subject_combo.findData(select_id)
            if index >= 0:
                self._subject_combo.setCurrentIndex(index)

    def _build_add_subject_form(self):
        form = QFrame()
        form.setProperty("class", "InlineForm")
        layout = QVBoxLayout(form)

        layout.addWidget(QLabel("New subject name"))
        self._new_subject_name = QLineEdit()
        layout.addWidget(self._new_subject_name)

        layout.addWidget(QLabel("Color"))
        swatch_row = QHBoxLayout()
        self._new_subject_color = COLOR_PALETTE[0]
        self._swatch_buttons = {}
        for hexval in COLOR_PALETTE:
            btn = QPushButton()
            btn.setFixedSize(22, 22)
            btn.clicked.connect(lambda checked=False, h=hexval: self._pick_subject_color(h))
            self._swatch_buttons[hexval] = btn
            swatch_row.addWidget(btn)
        swatch_row.addStretch(1)
        layout.addLayout(swatch_row)
        self._pick_subject_color(self._new_subject_color)

        save_row = QHBoxLayout()
        save_row.addStretch(1)
        save_button = QPushButton("Save Subject")
        save_button.setProperty("class", "AccentButton")
        save_button.clicked.connect(self._save_new_subject)
        save_row.addWidget(save_button)
        layout.addLayout(save_row)

        return form

    def _pick_subject_color(self, hexval):
        self._new_subject_color = hexval
        for h, btn in self._swatch_buttons.items():
            border = "2px solid #1F2328" if h == hexval else "1px solid #0002"
            btn.setStyleSheet(f"background: {h}; border-radius: 4px; border: {border};")

    def _toggle_add_subject(self):
        self._add_subject_form.setVisible(not self._add_subject_form.isVisible())

    def _save_new_subject(self):
        name = self._new_subject_name.text().strip()
        if not name:
            return
        subject = review_store.create_subject(
            self._topic_id, name, self._new_subject_color
        )
        if subject is None:
            return
        self._reload_subjects(select_id=subject["id"])
        self._new_subject_name.setText("")
        self._add_subject_form.setVisible(False)

    def _choose_photo(self):
        path, _ = QFileDialog.getOpenFileName(self, "Choose Image", "", "Images (*.png *.jpg *.jpeg *.gif *.bmp)")
        if not path:
            return
        self._photo_path = path
        pixmap = QPixmap(path)
        if not pixmap.isNull():
            self._photo_preview.setPixmap(pixmap.scaled(300, 180, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        else:
            self._photo_preview.setText("Could not preview this image.")

    def _submit(self):
        name = self._name_edit.text().strip()
        if not name:
            self._status_label.setText("Name is required.")
            return
        subject_id = self._subject_combo.currentData()
        if subject_id is None:
            self._status_label.setText("Add a subject first.")
            return
        stars = self._star_picker.value()

        description_type = None
        description_text = None
        description_link = None
        photo_bytes = None
        photo_filename = None

        if self._text_button.isChecked():
            description_type = "text"
            description_text = self._text_edit.toPlainText().strip()
            if not description_text:
                self._status_label.setText("Description text is required.")
                return
        elif self._photo_button.isChecked():
            description_type = "photo"
            if not self._photo_path:
                self._status_label.setText("Choose an image.")
                return
            with open(self._photo_path, "rb") as f:
                photo_bytes = f.read()
            photo_filename = os.path.basename(self._photo_path)
        else:
            description_type = "link"
            description_link = self._link_edit.text().strip()
            if not _URL_RE.match(description_link):
                self._status_label.setText("Enter a valid URL (e.g. https://example.com).")
                return

        problem = review_store.create_problem(
            self._topic_id, subject_id, name, stars, description_type,
            description_text=description_text, description_link=description_link,
            photo_bytes=photo_bytes, photo_filename=photo_filename,
        )
        if problem is None:
            self._status_label.setText("Could not save this problem.")
            return

        self.close()
        self._on_added(problem)


def _bold_label(text):
    label = QLabel(text)
    label.setStyleSheet("font-weight: 700;")
    return label
