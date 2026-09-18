import os
import re
from datetime import date, datetime

from PySide6.QtCore import Qt, QTimer, QUrl
from PySide6.QtGui import QColor, QDesktopServices, QPalette, QPixmap
from PySide6.QtWidgets import (
    QButtonGroup,
    QComboBox,
    QFileDialog,
    QFrame,
    QGridLayout,
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

# Start is first, not last -- with this many columns the table is wider
# than the window and needs horizontal scrolling, and a trailing Start
# button would get scrolled out of view. Leading keeps it reachable without
# scrolling at all.
COLUMN_START, COLUMN_NAME, COLUMN_SUBJECT, COLUMN_STARS, COLUMN_REVIEWS, \
    COLUMN_LAST_REVIEWED, COLUMN_NEXT_REVIEW, COLUMN_FIRST_SOLVED, COLUMN_FASTEST = range(9)
COLUMN_HEADERS = [
    "", "Problem Name", "Subject", "Difficulty", "Reviews",
    "Last Reviewed", "Next Review", "First Solved", "Fastest Time",
]

_URL_RE = re.compile(r"^https?://[^\s]+\.[^\s]+$", re.IGNORECASE)


def _lighten(hex_color, mix=0.78):
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
    # %-d (no leading zero) is Linux/macOS-only and raises ValueError on
    # Windows (this app's target platform) -- day is formatted by hand
    # instead, same workaround as qt_ui/board_tab.py's _format_date.
    return f"{d.strftime('%b')} {d.day}, {d.year}"


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


def _next_review_display(iso_date_string):
    """Days until the problem's next scheduled review, e.g. "in 3 days",
    "Today", "Tomorrow", or "N days overdue" if it's past due."""
    if not iso_date_string:
        return "—"
    days = (date.fromisoformat(iso_date_string) - date.today()).days
    if days == 0:
        return "Today"
    if days == 1:
        return "Tomorrow"
    if days > 1:
        return f"in {days} days"
    if days == -1:
        return "Yesterday"
    return f"{-days} days ago"


def _is_overdue(iso_date_string):
    return bool(iso_date_string) and date.fromisoformat(iso_date_string) < date.today()


def _fastest_display(problem):
    """The Fastest Time column's text. A real solved time always wins. With
    no solve recorded yet, fastestTimeSeconds instead stands in with the
    fastest "checked the answer" time (see review_store._apply_review_outcome)
    so it's not just a blank dash -- shown with an "(A)" (attempt) marker
    until an actual solve happens and takes over."""
    fastest = problem.get("fastestTimeSeconds")
    if fastest is None:
        return "--:--"
    marker = "" if problem.get("fastestTimeIsSolved") else " (A)"
    return f"{_format_mmss(fastest)}{marker}"


def _subject_and_task_text(problem):
    """"Subject — linked task" (or just the subject if its topic has no
    linked task) -- the linked task is the one review sessions for this
    topic actually start/end a focus session against, see
    _TopicView._begin_review."""
    text = problem["subjectName"]
    topic = review_store.get_topic(problem["topicId"])
    task_id = topic.get("linkedTaskId") if topic else None
    task = tasks_store.get_task(task_id) if task_id else None
    if task:
        text = f"{text} — {task['name']}"
    return text


def _first_attempt_text(problem):
    """None if this problem was never started via "Add & Start First
    Attempt" -- older/regular problems just don't have this stat. Also
    hidden once the problem has more than one real review -- the first
    attempt's "(checked the answer)" note is only useful before later
    reviews make it stale."""
    seconds = problem.get("firstAttemptSeconds")
    if seconds is None:
        return None
    if problem.get("reviewCount", 0) > 1:
        return None
    if problem.get("firstAttemptSelfSolved"):
        shakiness = problem.get("firstAttemptShakiness")
        return f"First attempt: {_format_mmss(seconds)}, shakiness {shakiness}/5 (solved it)"
    return f"First attempt: {_format_mmss(seconds)} (checked the answer)"


def _format_session_date(iso_datetime_string):
    if not iso_datetime_string:
        return "?"
    return _format_dmy(iso_datetime_string[:10])


def _session_summary_fields(number, session):
    """The 4 timeline columns for one session, as separate strings so they
    can go in their own QGridLayout column and actually line up -- a single
    dash-joined string can't stay aligned across rows in a proportional
    font."""
    duration = _format_mmss(session.get("durationSeconds"))
    shakiness = session.get("shakiness")
    shakiness_text = f"{shakiness if shakiness is not None else '-'}/5"
    a_marker = " (A)" if not session.get("selfSolved") else ""
    date_text = _format_session_date(session.get("finishedAt"))
    return (f"#{number}", duration, f"{shakiness_text}{a_marker}", date_text)


def _timeline_sessions(sessions):
    """Picks which of a problem's full session history (newest first, from
    review_store.list_sessions) to show in the review timeline, each paired
    with its session number (1 = first ever): everything if there are 5 or
    fewer, otherwise the newest 3 plus the first-ever and second-ever
    session (in that chronological order), with a gap marker in between so
    it's clear there's more history not shown."""
    total = len(sessions)
    numbered = [(total - i, session) for i, session in enumerate(sessions)]
    if total <= 5:
        return numbered, False
    newest_three = numbered[:3]
    first_two_ever = [numbered[-1], numbered[-2]]
    return newest_three + first_two_ever, True


_TIMELINE_HEADER_CELLS = ("#", "Time", "Shakiness", "Date")


def _timeline_gap_marker():
    """3 stacked dot labels, each its own widget centered in a tight
    QVBoxLayout -- rendering the vertical-ellipsis glyph ("⋮") as a
    single QLabel comes out visibly off-axis in some fonts, so 3
    independently-centered dots are used instead to guarantee a straight
    vertical line."""
    dots_widget = QWidget()
    dots_layout = QVBoxLayout(dots_widget)
    dots_layout.setContentsMargins(0, 4, 0, 4)
    dots_layout.setSpacing(3)
    dots_layout.setAlignment(Qt.AlignHCenter)
    for _ in range(3):
        dot_label = QLabel("•")
        dot_label.setStyleSheet("font-size: 13px; font-weight: 700; color: #5A6070;")
        dot_label.setAlignment(Qt.AlignHCenter)
        dots_layout.addWidget(dot_label)
    return dots_widget


def _build_review_timeline(layout, problem):
    sessions = review_store.list_sessions(problem["id"])
    if not sessions:
        return
    shown, has_gap = _timeline_sessions(sessions)

    layout.addWidget(_bold_label("Review Timeline"))

    # A real grid, not a dash-joined string -- each column (#, Time,
    # Shakiness, Date) is its own cell so rows and the header actually line
    # up under each other in a proportional font, instead of drifting based
    # on how wide each value happens to be.
    grid_container = QWidget()
    grid = QGridLayout(grid_container)
    grid.setContentsMargins(0, 0, 0, 0)
    grid.setHorizontalSpacing(18)
    grid.setVerticalSpacing(4)
    for column, header_text in enumerate(_TIMELINE_HEADER_CELLS):
        header_label = QLabel(header_text)
        header_label.setStyleSheet("font-size: 11px; font-weight: 700; color: #8A8F98;")
        grid.addWidget(header_label, 0, column)

    row = 1
    for index, (number, session) in enumerate(shown):
        for column, field_text in enumerate(_session_summary_fields(number, session)):
            field_label = QLabel(field_text)
            field_label.setStyleSheet("font-size: 12px; color: #5A6070;")
            grid.addWidget(field_label, row, column)
        row += 1
        if has_gap and index == 2:
            grid.addWidget(_timeline_gap_marker(), row, 0, 1, len(_TIMELINE_HEADER_CELLS))
            row += 1

    layout.addWidget(grid_container, alignment=Qt.AlignLeft)


def _build_description_content(layout, problem):
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
        link_label = QLabel(f'<a style="color: #1F2328;" href="{link}">{link}</a>')
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
        if self._topic_views:
            self._tabs.setCurrentIndex(0)
        _register_popup(_AddTopicDialog(on_added=self._on_topic_added))

    def _on_topic_added(self, topic):
        if topic is None:
            return
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
        try:
            topic = review_store.create_topic(name)
        except review_store.DuplicateNameError as e:
            self._status_label.setText(str(e))
            return
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
        self._resume_if_active()

        self._table = QTableWidget(0, len(COLUMN_HEADERS))
        self._table.setHorizontalHeaderLabels(COLUMN_HEADERS)
        self._table.verticalHeader().setVisible(False)
        self._table.setEditTriggers(QTableWidget.NoEditTriggers)
        self._table.setSelectionMode(QTableWidget.NoSelection)
        self._table.horizontalHeader().setStretchLastSection(False)
        self._table.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self._table.setColumnWidth(COLUMN_START, 90)
        self._table.setColumnWidth(COLUMN_NAME, 220)
        self._table.setColumnWidth(COLUMN_SUBJECT, 120)
        self._table.setColumnWidth(COLUMN_STARS, 90)
        self._table.setColumnWidth(COLUMN_REVIEWS, 70)
        self._table.setColumnWidth(COLUMN_LAST_REVIEWED, 120)
        self._table.setColumnWidth(COLUMN_NEXT_REVIEW, 110)
        self._table.setColumnWidth(COLUMN_FIRST_SOLVED, 100)
        self._table.setColumnWidth(COLUMN_FASTEST, 100)
        self._table.cellClicked.connect(self._on_cell_clicked)
        self._table.setContextMenuPolicy(Qt.CustomContextMenu)
        self._table.customContextMenuRequested.connect(self._on_table_context_menu)
        layout.addWidget(self._table, 1)

        self.refresh()

    def _resume_if_active(self):
        # A review-linked task session survives independently of this
        # widget (it lives in session_manager, persisted to
        # session_state.json) -- but the banner that shows its timer is
        # pure in-memory widget state that only ever gets set by
        # _begin_review()/_start_first_attempt() below. If this _TopicView
        # is constructed while such a session is already running (most
        # commonly: the app restarted -- e.g. --dev's watch-and-restart on
        # every .py save -- while a review was mid-flight), the Tasks tab
        # still shows it correctly (it reads session_manager.get_status()
        # fresh every time) but this banner would otherwise just sit
        # hidden, showing no timer at all for a session that's very much
        # still active and still enforcing.
        status = session_manager.get_status()
        if not status.get("isActive") or status.get("source") != "review":
            return
        topic = review_store.get_topic(self._topic_id)
        if not topic or not topic.get("linkedTaskId") or topic["linkedTaskId"] != status.get("eventId"):
            return
        problem_name = status.get("reviewProblemName") or "this review"
        if self._review_tab:
            self._review_tab.on_review_started()
        # token=None: review_store's own active-session tracking
        # (_active_sessions) is in-memory only and didn't survive whatever
        # took this session_manager session and this widget out of sync in
        # the first place. Finish still logs a real review_sessions row
        # though, via reviewProblemId (persisted in session_manager's own
        # state, unlike _active_sessions) and _ReviewBanner's
        # finish_review_for_problem fallback -- see _complete_finish.
        self._review_banner.start(
            {"name": problem_name, "id": status.get("reviewProblemId")},
            token=None, end_session_on_finish=True,
        )

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
        if self._due_only:
            # Overdue problems are the most urgent -- pin them above ones
            # merely due today, on top of the store's next_review_date/stars
            # ordering (stable sort keeps that ordering within each group).
            self._problems.sort(key=lambda p: not _is_overdue(p.get("nextReviewDate")))
        self._render_table()

    def _render_table(self):
        self._table.setRowCount(len(self._problems))
        for row, problem in enumerate(self._problems):
            tint = _lighten(problem["subjectColor"])

            name_item = QTableWidgetItem(problem["name"])
            self._table.setItem(row, COLUMN_NAME, name_item)

            reviews_item = QTableWidgetItem(str(problem["reviewCount"]))
            reviews_item.setTextAlignment(Qt.AlignCenter)
            self._table.setItem(row, COLUMN_REVIEWS, reviews_item)

            last_item = QTableWidgetItem(_relative_time(problem["lastReviewedAt"]))
            self._table.setItem(row, COLUMN_LAST_REVIEWED, last_item)

            next_review_item = QTableWidgetItem(_next_review_display(problem.get("nextReviewDate")))
            next_review_item.setTextAlignment(Qt.AlignCenter)
            self._table.setItem(row, COLUMN_NEXT_REVIEW, next_review_item)

            first_item = QTableWidgetItem(_format_dmy(problem["dateAdded"]))
            self._table.setItem(row, COLUMN_FIRST_SOLVED, first_item)

            fastest_item = QTableWidgetItem(_fastest_display(problem))
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

            text_cols = (COLUMN_NAME, COLUMN_REVIEWS, COLUMN_LAST_REVIEWED, COLUMN_NEXT_REVIEW, COLUMN_FIRST_SOLVED, COLUMN_FASTEST)
            for col in text_cols:
                self._table.item(row, col).setForeground(QColor("#1F2328"))
            for col in text_cols + (COLUMN_STARS,):
                self._table.item(row, col).setBackground(QColor(tint))

            if _is_overdue(problem.get("nextReviewDate")):
                for col in text_cols:
                    item = self._table.item(row, col)
                    item.setForeground(QColor("#c62828"))
                    font = item.font()
                    font.setBold(True)
                    item.setFont(font)

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

    def _on_table_context_menu(self, pos):
        row = self._table.rowAt(pos.y())
        if row < 0 or row >= len(self._problems):
            return
        problem = self._problems[row]

        menu = QMenu(self)
        edit_action = menu.addAction("Edit Problem")
        action = menu.exec(self._table.viewport().mapToGlobal(pos))
        if action == edit_action:
            self._open_edit_problem(problem)

    def _open_edit_problem(self, problem):
        _register_popup(_AddProblemDialog(self._topic_id, on_added=lambda _p: self.refresh(), problem=problem))

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
                    process_blocklist=task.get("processBlocklist", []),
                    domain_whitelist=task.get("domainWhitelist", []),
                    source="review",
                    event_id=task["id"],
                    event_title=f"{task['name']} - {problem['subjectName']} review",
                    review_problem_name=problem["name"],
                    review_subject_name=problem["subjectName"],
                    review_problem_id=problem["id"],
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
        _register_popup(_AddProblemDialog(
            self._topic_id, on_added=lambda _p: self.refresh(),
            on_start_first_attempt=self._start_first_attempt,
        ))

    def _start_first_attempt(self, add_problem_dialog):
        """The Add Problem dialog's top "Start First Attempt" button --
        times the very first attempt on the same embedded review banner
        every other review uses (not a separate popup window), before the
        problem even has a name/subject/description yet. The dialog hides
        while the banner runs and reopens once you finish, pre-filled with
        the recorded time/outcome, to actually save the problem."""
        if self._review_tab and not self._review_tab.can_start_review():
            return
        add_problem_dialog.hide()

        end_session_on_finish = False
        topic = review_store.get_topic(self._topic_id)
        if topic and topic.get("linkedTaskId") and not session_manager.is_active():
            task = tasks_store.get_task(topic["linkedTaskId"])
            if task:
                session_manager.start_session(
                    duration_minutes=tasks_store.BURNOUT_MINUTES,
                    lock_mode=task["lockMode"],
                    process_blocklist=task.get("processBlocklist", []),
                    domain_whitelist=task.get("domainWhitelist", []),
                    source="review",
                    event_id=task["id"],
                    event_title=f"{task['name']} - first attempt",
                    review_problem_name="First attempt",
                    review_subject_name=None,
                )
                end_session_on_finish = True

        if self._review_tab:
            self._review_tab.on_review_started()

        def _on_first_attempt_done(elapsed_seconds, self_solved, shakiness):
            add_problem_dialog.apply_first_attempt(elapsed_seconds, self_solved, shakiness)
            add_problem_dialog.show()

        self._review_banner.start(
            {"name": "your new problem"}, token=None,
            end_session_on_finish=end_session_on_finish,
            first_attempt_callback=_on_first_attempt_done,
        )


class _ReviewBanner(QWidget):
    def __init__(self, on_finished):
        super().__init__()
        self._on_finished = on_finished
        self._session_token = None
        self._end_session_on_finish = False
        self._problem = None
        self._start_time = None
        self._accumulated_seconds = 0
        self._is_paused = False
        self._first_attempt_callback = None

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

        btn_row = QHBoxLayout()
        btn_row.addStretch(1)

        self._pause_btn = QPushButton("Pause")
        self._pause_btn.setFixedWidth(80)
        # Explicit styling rather than relying on bare OS-default QPushButton
        # look (there's no unclassed QPushButton rule in styles.qss outside
        # #PopupBg) -- unstyled, it was easy to miss against this card's
        # light #EAF2FF background.
        self._pause_btn.setStyleSheet(
            "QPushButton { background: #FFFFFF; color: #1F2328; "
            "border: 1px solid #BDD4F7; border-radius: 6px; font-weight: 600; padding: 6px 0; }"
            "QPushButton:hover { background: #DCE9FF; }"
        )
        self._pause_btn.clicked.connect(self._pause_resume)
        btn_row.addWidget(self._pause_btn)

        finish_btn = QPushButton("Finish")
        finish_btn.setProperty("class", "AccentButton")
        finish_btn.setFixedWidth(120)
        finish_btn.clicked.connect(self._finish)
        btn_row.addWidget(finish_btn)

        # Sandwiched between two stretches, Pause/Finish read as a centered
        # pair -- End sits outside that on its own, pinned to the row's
        # right edge (bottom-right of the timer card) rather than grouped
        # in with them, so it doesn't get mistaken for "end this review the
        # normal way" the way it did sitting between Pause and Finish.
        btn_row.addStretch(1)

        end_btn = QPushButton("End")
        end_btn.setFixedWidth(80)
        end_btn.setStyleSheet(
            "QPushButton { background: #e53935; color: white; border: none; "
            "border-radius: 6px; font-weight: 600; padding: 6px 0; }"
            "QPushButton:hover { background: #c62828; }"
        )
        end_btn.clicked.connect(self._end_early)
        btn_row.addWidget(end_btn)

        card_layout.addLayout(btn_row)

        outer.addWidget(card)

        self._tick_timer = QTimer(self)
        self._tick_timer.timeout.connect(self._tick)

        self.hide()

    def _elapsed_seconds_now(self):
        if self._end_session_on_finish:
            # Pause-aware off the linked task session's own startTime/
            # violationLog (same math as the Tasks tab's running card) --
            # this session can be paused/resumed from either tab, so its own
            # local start_time/accumulated_seconds bookkeeping (below)
            # can't be trusted to reflect a pause that happened elsewhere.
            status = session_manager.get_status()
            if not status.get("startTime"):
                return 0
            return tasks_store.worked_seconds(status["startTime"], None, status.get("violationLog"))
        if self._start_time is None:
            return self._accumulated_seconds
        return self._accumulated_seconds + int((datetime.now() - self._start_time).total_seconds())

    def start(self, problem, token, end_session_on_finish=False, first_attempt_callback=None):
        self._problem = problem
        self._session_token = token
        self._end_session_on_finish = end_session_on_finish
        self._first_attempt_callback = first_attempt_callback
        self._start_time = datetime.now()
        self._accumulated_seconds = 0
        self._is_paused = False
        label = "Timing first attempt" if first_attempt_callback is not None else f"Reviewing: {problem['name']}"
        self._problem_label.setText(label)
        # Not hardcoded "00:00" -- when this is _resume_if_active() rebuilding
        # a fresh banner for a session that survived an app restart, real
        # elapsed (pause-aware, from session_manager) can already be nonzero,
        # and a paused session's _tick() never gets a chance to correct a
        # wrong initial value (see _tick()'s own comment).
        self._timer_label.setText(_format_mmss(self._elapsed_seconds_now()))
        self._pause_btn.setText("Resume" if self._currently_paused() else "Pause")
        # Always available -- pausing here freezes the review's own elapsed
        # timer (and _start_first_attempt.../ordinary reviews that aren't
        # tied to a linked task session still get to pause) rather than only
        # showing up when there happens to be an underlying session to pause.
        self._pause_btn.setVisible(True)
        self._tick_timer.start(1000)
        self.show()

    def _currently_paused(self):
        # When a linked task session is driving this review, session_manager
        # is the source of truth for pause state -- it can also be
        # paused/resumed from the Tasks tab's own Pause button on the same
        # underlying session, and this banner needs to reflect that instead
        # of drifting out of sync with its own separate _is_paused flag.
        # Otherwise (a standalone review with no linked session) there's
        # nothing external to defer to, so _is_paused is authoritative.
        if self._end_session_on_finish:
            return session_manager.get_status().get("isPaused", False)
        return self._is_paused

    def _tick(self):
        if self._end_session_on_finish and not session_manager.get_status().get("isActive"):
            # The linked task session was ended from somewhere else entirely
            # -- the Tasks tab's "End Task", the Focus tab's Nuclear End, a
            # natural timeout, or a direct API call -- none of which know or
            # care that a review was riding along on top of it. This banner
            # has no session left to keep ticking against. Treat it like the
            # user clicking "End" themselves (abandon, don't fabricate a
            # "Finish" with a duration measured against dead state) rather
            # than leaving it stuck showing a live timer forever, which also
            # permanently blocked can_start_review() from ever going True
            # again until the app was restarted.
            self._abandon_after_external_end()
            return
        is_paused = self._currently_paused()
        self._pause_btn.setText("Resume" if is_paused else "Pause")
        # Updated unconditionally, even while paused -- _elapsed_seconds_now()
        # already freezes correctly at the pause point on its own (pause-aware
        # worked_seconds for a linked task, or the stored _accumulated_seconds
        # otherwise), so there's nothing wrong with recomputing it while
        # paused, and skipping the update here is what used to leave a
        # freshly-rebuilt-after-restart banner stuck showing "00:00" the
        # entire time it stayed paused.
        self._timer_label.setText(_format_mmss(self._elapsed_seconds_now()))

    def _pause_resume(self):
        if self._currently_paused():
            self._is_paused = False
            self._start_time = datetime.now()
            self._pause_btn.setText("Pause")
            # A linked task session is paused/resumed alongside the review
            # timer -- otherwise pausing the review would leave the task
            # session's own clock (and enforcement) running underneath it.
            if self._end_session_on_finish:
                session_manager.resume_session()
        else:
            self._is_paused = True
            self._accumulated_seconds = self._elapsed_seconds_now()
            self._start_time = None
            self._pause_btn.setText("Resume")
            if self._end_session_on_finish:
                session_manager.pause_session()

    def _abandon_after_external_end(self):
        """Same cleanup as _end_early(), minus the session_manager.end_session()
        call -- the session is already gone by the time this runs, ended by
        whatever external path _tick() just detected, so calling end_session()
        again here would be a no-op at best and misattribute an "ended twice"
        history entry at worst."""
        self._tick_timer.stop()
        token = self._session_token
        self._session_token = None
        self._end_session_on_finish = False
        self._start_time = None
        self._first_attempt_callback = None
        self.hide()
        review_store.abandon_review(token)
        self._on_finished()

    def _end_early(self):
        self._tick_timer.stop()
        token = self._session_token
        end_session = self._end_session_on_finish
        self._session_token = None
        self._end_session_on_finish = False
        self._start_time = None
        self._first_attempt_callback = None
        self.hide()
        # No-op when token is None (first-attempt mode -- there was never a
        # start_review() session to abandon).
        review_store.abandon_review(token)
        if end_session:
            session_manager.end_session()
        self._on_finished()

    def _finish(self):
        self._tick_timer.stop()
        token = self._session_token
        end_session = self._end_session_on_finish
        problem = self._problem
        elapsed = self._elapsed_seconds_now()
        # Grabbed now, before end_session() (called from _complete_finish,
        # after the post-review dialog closes) resets session_manager's
        # state -- only needed for the token=None recovery path below, but
        # harmless to capture unconditionally.
        started_at_iso = session_manager.get_status().get("startTime")
        first_attempt_callback = self._first_attempt_callback
        self._session_token = None
        self._end_session_on_finish = False
        self._start_time = None
        self._first_attempt_callback = None
        self.hide()
        _PostReviewDialog(
            problem_name=problem["name"],
            on_submit=lambda self_solved, shakiness: self._complete_finish(
                token, end_session, self_solved, shakiness, elapsed,
                first_attempt_callback, problem, started_at_iso,
            ),
        )

    def _complete_finish(
        self, token, end_session, self_solved, shakiness, elapsed,
        first_attempt_callback, problem, started_at_iso,
    ):
        if first_attempt_callback is not None:
            # No problem exists yet -- hand the timing/outcome back to the
            # Add Problem dialog instead of writing to review_store, which
            # needs a real problem_id to attach a session to.
            first_attempt_callback(elapsed, self_solved, shakiness)
        elif token is not None:
            review_store.finish_review(
                token, self_solved=self_solved, shakiness=shakiness, duration_seconds=elapsed,
            )
        elif problem and problem.get("id") is not None:
            # Recovered after an app restart (see _TopicView._resume_if_active)
            # -- the original start_review() token lived only in
            # review_store's in-memory _active_sessions and didn't survive,
            # but the problem id did (persisted via session_manager), so the
            # review still gets logged instead of silently vanishing.
            review_store.finish_review_for_problem(
                problem["id"], elapsed, self_solved=self_solved, shakiness=shakiness,
                started_at=datetime.fromisoformat(started_at_iso) if started_at_iso else None,
            )
        if end_session:
            session_manager.end_session()
        self._on_finished()


class _ShakinessPicker(QWidget):
    def __init__(self, initial=3, on_changed=None):
        super().__init__()
        self._value = initial
        self._on_changed = on_changed
        self._buttons = []
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        layout.addStretch(1)
        for n in range(1, 6):
            btn = QPushButton(str(n))
            btn.setFixedSize(36, 36)
            btn.setCheckable(True)
            btn.setStyleSheet(
                "QPushButton { border-radius: 18px; font-size: 14px; font-weight: 600; "
                "background: #E5E8EF; color: #1F2328; border: none; padding: 0; }"
                "QPushButton:checked { background: #4A90E2; color: white; }"
            )
            btn.clicked.connect(lambda checked=False, n=n: self._set_value(n))
            layout.addWidget(btn)
            self._buttons.append(btn)
        layout.addStretch(1)
        self._refresh()

    def _set_value(self, n):
        self._value = n
        self._refresh()
        if self._on_changed:
            self._on_changed(n)

    def _refresh(self):
        for i, btn in enumerate(self._buttons, start=1):
            btn.setChecked(i == self._value)


_METHOD_BTN_STYLE = (
    "QPushButton { padding: 8px 12px; border-radius: 6px; font-size: 13px; "
    "background: #E5E8EF; color: #5A6070; border: none; }"
    "QPushButton:checked { background: #4A90E2; color: white; font-weight: 600; }"
)


class _PostReviewDialog(QWidget):
    def __init__(self, problem_name, on_submit):
        super().__init__(None, Qt.WindowStaysOnTopHint)
        self.setObjectName("PopupBg")
        self.setWindowTitle("Carmen Focus — Review complete")
        self.resize(360, 250)
        self._on_submit = on_submit
        self._self_solved = None
        self._method_chosen = False
        self._shakiness = 3
        self._submitted = False

        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        header = QLabel("How did it go?")
        header.setStyleSheet("font-size: 14px; font-weight: 700;")
        header.setAlignment(Qt.AlignCenter)
        layout.addWidget(header)

        name_lbl = QLabel(problem_name)
        name_lbl.setStyleSheet("font-size: 12px; color: #5A6070;")
        name_lbl.setAlignment(Qt.AlignCenter)
        name_lbl.setWordWrap(True)
        layout.addWidget(name_lbl)

        method_row = QHBoxLayout()
        self._solved_btn = QPushButton("Solved it!")
        self._solved_btn.setCheckable(True)
        self._solved_btn.setStyleSheet(_METHOD_BTN_STYLE)
        self._checked_btn = QPushButton("Checked the answer")
        self._checked_btn.setCheckable(True)
        self._checked_btn.setStyleSheet(_METHOD_BTN_STYLE)
        method_grp = QButtonGroup(self)
        method_grp.setExclusive(True)
        method_grp.addButton(self._solved_btn)
        method_grp.addButton(self._checked_btn)
        self._solved_btn.toggled.connect(lambda chk: self._set_method(True) if chk else None)
        self._checked_btn.toggled.connect(lambda chk: self._set_method(False) if chk else None)
        method_row.addWidget(self._solved_btn)
        method_row.addWidget(self._checked_btn)
        layout.addLayout(method_row)

        self._shak_section = QWidget()
        shak_layout = QVBoxLayout(self._shak_section)
        shak_layout.setContentsMargins(0, 0, 0, 0)
        shak_layout.setSpacing(4)
        shak_label = QLabel("Shakiness (1 solid → 5 very shaky):")
        shak_label.setStyleSheet("font-size: 12px;")
        shak_label.setAlignment(Qt.AlignCenter)
        shak_layout.addWidget(shak_label)
        self._shak_picker = _ShakinessPicker(initial=3, on_changed=self._set_shakiness)
        shak_layout.addWidget(self._shak_picker)
        self._shak_section.setVisible(False)
        layout.addWidget(self._shak_section)

        submit_btn = QPushButton("Submit")
        submit_btn.setProperty("class", "AccentButton")
        submit_btn.setEnabled(False)
        submit_btn.clicked.connect(self._submit)
        layout.addWidget(submit_btn)
        self._submit_btn = submit_btn

        self._hint_label = QLabel('Choose "Solved it!" or "Checked the answer" first.')
        self._hint_label.setStyleSheet("color: #c62828; font-size: 11px;")
        self._hint_label.setAlignment(Qt.AlignCenter)
        self._hint_label.setWordWrap(True)
        self._hint_label.setVisible(False)
        layout.addWidget(self._hint_label)

        _register_popup(self)
        self.show()

    def _set_method(self, self_solved):
        self._self_solved = self_solved
        self._method_chosen = True
        self._shak_section.setVisible(self_solved)
        self._submit_btn.setEnabled(True)
        self._hint_label.setVisible(False)

    def _set_shakiness(self, val):
        self._shakiness = val

    def _submit(self):
        # Guards both a disabled-button click getting through somehow and a
        # double-submit -- an outcome must be chosen before this can fire.
        if self._submitted or not self._method_chosen:
            return
        self._submitted = True
        self.close()
        # Shakiness only means anything for a solved attempt -- "checked the
        # answer" never showed the picker, so its stale default must not be
        # sent along as if the user had actually rated it.
        self._on_submit(self._self_solved, self._shakiness if self._self_solved else None)

    def closeEvent(self, event):
        # The review outcome must be recorded as either "solved it" or
        # "checked the answer" -- closing the window (title-bar X) before
        # picking one used to silently record it as solved by default.
        if not self._method_chosen:
            self._hint_label.setVisible(True)
            event.ignore()
            return
        if not self._submitted:
            self._submitted = True
            self._on_submit(self._self_solved, self._shakiness if self._self_solved else None)
        super().closeEvent(event)


class _ReviewStartDialog(QWidget):
    def __init__(self, problem, on_start):
        super().__init__(None, Qt.WindowStaysOnTopHint)
        self.setObjectName("PopupBg")
        self.setWindowTitle(problem["name"])
        self.resize(380, 460)
        self._problem = problem
        self._on_start = on_start

        layout = QVBoxLayout(self)

        header_row = QHBoxLayout()
        name_label = QLabel(problem["name"])
        name_label.setStyleSheet("font-size: 22px; font-weight: 700;")
        header_row.addWidget(name_label, 1)
        stars_label = QLabel(_star_text(problem["stars"]))
        stars_label.setStyleSheet("color: #F5A623; font-size: 22px;")
        header_row.addWidget(stars_label)
        layout.addLayout(header_row)

        subject_task_label = QLabel(_subject_and_task_text(problem))
        subject_task_label.setStyleSheet("font-size: 13px; color: #5A6070;")
        layout.addWidget(subject_task_label)

        stats_row = QHBoxLayout()
        fastest_label = QLabel(f"Fastest: {_fastest_display(problem)}")
        fastest_label.setStyleSheet("font-size: 12px; color: #5A6070;")
        stats_row.addWidget(fastest_label)
        stats_row.addStretch(1)
        attempts_label = QLabel(f"Attempts: {problem['reviewCount']}")
        attempts_label.setStyleSheet("font-size: 12px; color: #5A6070;")
        stats_row.addWidget(attempts_label)
        layout.addLayout(stats_row)

        first_attempt_text = _first_attempt_text(problem)
        if first_attempt_text:
            first_attempt_label = QLabel(first_attempt_text)
            first_attempt_label.setStyleSheet("font-size: 12px; color: #5A6070;")
            layout.addWidget(first_attempt_label)

        _build_review_timeline(layout, problem)

        _build_description_content(layout, problem)

        start_button = QPushButton("Start")
        start_button.setStyleSheet(
            "background: #28a745; color: white; font-weight: 600; "
            "border-radius: 8px; padding: 8px 20px; font-size: 13px;"
        )
        start_button.clicked.connect(self._do_start)
        layout.addWidget(start_button)

        self.show()
        _register_popup(self)

    def _do_start(self):
        self.close()
        self._on_start(self._problem)


class _TopicTaskLinkDialog(QWidget):
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

        self._status_label = QLabel()
        self._status_label.setStyleSheet("color: #c62828;")
        self._status_label.setWordWrap(True)
        layout.addWidget(self._status_label)

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
        try:
            review_store.rename_topic(self._topic_id, name)
        except review_store.DuplicateNameError as e:
            self._status_label.setText(str(e))
            return
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

        first_attempt_text = _first_attempt_text(problem)
        if first_attempt_text:
            first_attempt_label = QLabel(first_attempt_text)
            first_attempt_label.setStyleSheet("font-size: 12px; color: #5A6070;")
            layout.addWidget(first_attempt_label)

        _build_review_timeline(layout, problem)

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
    """Also used for editing (right-click a row > Edit Problem in the Review
    tab) -- pass an existing `problem` dict to pre-fill every field, switch
    the submit button to "Save", and call review_store.update_problem
    instead of create_problem. Review history/schedule fields aren't shown
    here at all, so editing can't touch them."""

    def __init__(self, topic_id, on_added, problem=None, on_start_first_attempt=None):
        super().__init__(None, Qt.WindowStaysOnTopHint)
        self.setObjectName("PopupBg")
        self._editing = problem is not None
        self.setWindowTitle(f"Carmen Focus — {'Edit' if self._editing else 'Add'} Problem")
        self.resize(420, 620)
        self._topic_id = topic_id
        self._on_added = on_added
        self._on_start_first_attempt = on_start_first_attempt
        self._problem = problem
        self._photo_path = None
        self._pending_first_attempt = None

        layout = QVBoxLayout(self)

        # Only offered when adding (not editing) and the caller wired up a
        # handler -- editing an existing problem has nothing to "attempt"
        # here, and _TopicView is the only caller that passes the handler.
        if not self._editing and self._on_start_first_attempt is not None:
            # No name/subject/description required to click this -- timing
            # starts immediately, on the same embedded review banner every
            # other review uses, and the form gets filled in afterward, once
            # there's actually something to fill in about.
            first_attempt_button = QPushButton("Start First Attempt")
            first_attempt_button.setStyleSheet(
                "background: #28a745; color: white; font-weight: 600; "
                "border-radius: 8px; padding: 8px 12px; font-size: 13px;"
            )
            first_attempt_button.clicked.connect(self._start_first_attempt)
            layout.addWidget(first_attempt_button)

            self._first_attempt_status = QLabel()
            self._first_attempt_status.setStyleSheet("color: #2e7d32; font-size: 12px;")
            self._first_attempt_status.setWordWrap(True)
            self._first_attempt_status.setVisible(False)
            layout.addWidget(self._first_attempt_status)

        layout.addWidget(_bold_label("Name"))
        self._name_edit = QLineEdit(problem["name"] if problem else "")
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
        self._reload_subjects(select_id=problem["subjectId"] if problem else None)

        self._add_subject_form = self._build_add_subject_form()
        self._add_subject_form.setVisible(False)
        layout.addWidget(self._add_subject_form)

        layout.addWidget(_bold_label("Stars"))
        self._star_picker = _StarPicker(initial=problem["stars"] if problem else 3)
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
        layout.addLayout(type_row)

        self._description_stack = QStackedWidget()
        self._text_edit = QTextEdit()
        if problem:
            self._text_edit.setPlainText(problem.get("descriptionText") or "")
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
        existing_photo_path = problem.get("descriptionPhotoPath") if problem else None
        if existing_photo_path and os.path.exists(existing_photo_path):
            pixmap = QPixmap(existing_photo_path)
            if not pixmap.isNull():
                self._photo_preview.setPixmap(pixmap.scaled(300, 180, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        photo_layout.addWidget(self._photo_preview)
        self._description_stack.addWidget(photo_page)

        self._link_edit = QLineEdit(problem.get("descriptionLink") or "" if problem else "")
        self._link_edit.setPlaceholderText("https://example.com/problem")
        self._description_stack.addWidget(self._link_edit)

        layout.addWidget(self._description_stack, 1)

        self._text_button.toggled.connect(lambda c: c and self._description_stack.setCurrentIndex(0))
        self._photo_button.toggled.connect(lambda c: c and self._description_stack.setCurrentIndex(1))
        self._link_button.toggled.connect(lambda c: c and self._description_stack.setCurrentIndex(2))

        initial_type = problem["descriptionType"] if problem else "text"
        {"text": self._text_button, "photo": self._photo_button, "link": self._link_button}[initial_type].setChecked(True)

        self._status_label = QLabel()
        self._status_label.setStyleSheet("color: #c62828;")
        layout.addWidget(self._status_label)

        button_row = QHBoxLayout()
        button_row.addStretch(1)
        cancel_button = QPushButton("Cancel")
        cancel_button.clicked.connect(self.close)
        button_row.addWidget(cancel_button)
        submit_button = QPushButton("Save" if self._editing else "Add")
        submit_button.setProperty("class", "AccentButton")
        submit_button.clicked.connect(self._submit)
        button_row.addWidget(submit_button)
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
        try:
            subject = review_store.create_subject(
                self._topic_id, name, self._new_subject_color
            )
        except (review_store.DuplicateNameError, review_store.DuplicateColorError) as e:
            self._status_label.setText(str(e))
            return
        if subject is None:
            self._status_label.setText("Could not create subject.")
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

    def _gather_and_save(self):
        """Validates the form and creates/updates the problem. Returns the
        saved problem dict, or None (with an explanatory _status_label
        already set) if validation or the save itself failed."""
        name = self._name_edit.text().strip()
        if not name:
            self._status_label.setText("Name is required.")
            return None
        subject_id = self._subject_combo.currentData()
        if subject_id is None:
            self._status_label.setText("Add a subject first.")
            return None
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
                return None
        elif self._photo_button.isChecked():
            description_type = "photo"
            has_existing_photo = self._editing and self._problem.get("descriptionPhotoPath")
            if self._photo_path:
                with open(self._photo_path, "rb") as f:
                    photo_bytes = f.read()
                photo_filename = os.path.basename(self._photo_path)
            elif not has_existing_photo:
                self._status_label.setText("Choose an image.")
                return None
            # else: editing and keeping the existing photo -- photo_bytes
            # stays None, which tells update_problem to leave it alone.
        else:
            description_type = "link"
            description_link = self._link_edit.text().strip()
            if not _URL_RE.match(description_link):
                self._status_label.setText("Enter a valid URL (e.g. https://example.com).")
                return None

        try:
            if self._editing:
                problem = review_store.update_problem(
                    self._problem["id"], subject_id, name, stars, description_type,
                    description_text=description_text, description_link=description_link,
                    photo_bytes=photo_bytes, photo_filename=photo_filename,
                )
            else:
                problem = review_store.create_problem(
                    self._topic_id, subject_id, name, stars, description_type,
                    description_text=description_text, description_link=description_link,
                    photo_bytes=photo_bytes, photo_filename=photo_filename,
                )
        except review_store.DuplicateNameError as e:
            self._status_label.setText(str(e))
            return None
        if problem is None:
            self._status_label.setText("Could not save this problem.")
            return None
        return problem

    def _submit(self):
        problem = self._gather_and_save()
        if problem is None:
            return
        if not self._editing and self._pending_first_attempt is not None:
            problem = review_store.record_first_attempt(
                problem["id"],
                self._pending_first_attempt["seconds"],
                self_solved=self._pending_first_attempt["selfSolved"],
                shakiness=self._pending_first_attempt["shakiness"],
            ) or problem
        self.close()
        self._on_added(problem)

    def _start_first_attempt(self):
        self._on_start_first_attempt(self)

    def apply_first_attempt(self, elapsed_seconds, self_solved, shakiness):
        """Called by _TopicView once the embedded review banner's first-
        attempt timer finishes -- stashes the recorded time/outcome so
        _submit() can pass it to review_store.record_first_attempt() once
        the problem itself is actually saved, and shows a confirmation so
        it's clear the time wasn't lost."""
        self._pending_first_attempt = {
            "seconds": elapsed_seconds, "selfSolved": self_solved, "shakiness": shakiness,
        }
        detail = f"shakiness {shakiness}/5 (solved it)" if self_solved else "checked the answer"
        self._first_attempt_status.setText(
            f"First attempt recorded: {_format_mmss(elapsed_seconds)}, {detail}. "
            "Fill in the details below and Add to save it."
        )
        self._first_attempt_status.setVisible(True)
        self.show()


def _bold_label(text):
    label = QLabel(text)
    label.setStyleSheet("font-weight: 700;")
    return label
