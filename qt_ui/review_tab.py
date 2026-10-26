import os
import re
import uuid
from datetime import date, datetime, timedelta

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer, QUrl
from PySide6.QtGui import QColor, QDesktopServices, QFontMetrics, QPainter, QPalette, QPixmap
from PySide6.QtWidgets import (
    QButtonGroup,
    QComboBox,
    QFileDialog,
    QFrame,
    QGraphicsDropShadowEffect,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)
from qt_ui.wrap_edit import WrapEdit

import review_store
import session_history
import session_manager
from qt_ui import confirm_dialog
import tasks_store
from qt_ui.safe_link import make_link_label
from qt_ui.ui_kit import (
    accent,
    danger,
    dialog_title,
    field_label,
    role_label,
    AMBER,
    DANGER_STYLE,
    GREY,
    INK,
    MUTED,
    NUM_CSS,
    RED,
    SECONDARY_STYLE,
    ElidedLabel,
    button,
    chip,
    link_button,
)

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


_SEGMENT_STYLE = (
    f"QPushButton{{background:#FFFFFF;color:{INK};border:1px solid #E1E5EC;border-radius:13px;"
    "font-size:12.5px;font-weight:600;padding:0 14px;}"
    "QPushButton:hover{background:#F6F7FA;}"
    f"QPushButton:checked{{background:{INK};color:#FFFFFF;border:1px solid {INK};}}")

_PRIMARY_INK = (
    f"QPushButton{{background:{INK};color:#FFFFFF;border:none;border-radius:12px;"
    "font-size:13.5px;font-weight:600;padding:0 18px;}"
    "QPushButton:hover{background:#2B3445;}"
    "QPushButton:disabled{background:#EEF0F4;color:#A3ABB8;}")


def _soft_shadow(widget, blur=22, alpha=18):
    shadow = QGraphicsDropShadowEffect(widget)
    shadow.setBlurRadius(blur)
    shadow.setOffset(0, 5)
    shadow.setColor(QColor(16, 24, 40, alpha))
    widget.setGraphicsEffect(shadow)


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


def _first_solved_display(problem):
    """The First Solved column's text. A problem that has never been
    reviewed has never been solved either -- dateAdded is just when it was
    logged -- so it reads "Never" (same wording as Last Reviewed) rather
    than implying it was solved on the day it was added."""
    if not problem.get("lastReviewedAt"):
        return "Never"
    return _format_dmy(problem["dateAdded"])


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
        link_label = make_link_label(link)
        link_label.setWordWrap(True)
        layout.addWidget(link_label, 1)


SORT_CHOICES = (("due", "Due first"), ("hardest", "Hardest first"), ("newest", "Newest first"), ("name", "A to Z"))


class _CategoryRow(QPushButton):
    """A category in the sidebar: colour dot, name, how many problems. The chosen one is filled."""

    def __init__(self, name, color, count, checked=False, on_delete=None):
        super().__init__()
        self._name, self._color, self._count = name, color, count
        self._on_delete = on_delete
        self.setCheckable(True)
        self.setChecked(checked)
        self.setFixedHeight(34)
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip(f"Show {name.lower() if color is None else name}: {count} problem{'s' if count != 1 else ''}")
        self.setAccessibleName(name)

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        rect = QRectF(self.rect()).adjusted(0, 1, 0, -1)
        if self.isChecked():
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(INK))
            p.drawRoundedRect(rect, 10, 10)
        elif self.underMouse():
            p.setPen(Qt.NoPen)
            p.setBrush(QColor("#F2F4F7"))
            p.drawRoundedRect(rect, 10, 10)
        ink = QColor("#FFFFFF") if self.isChecked() else QColor(INK)
        muted = QColor("#C9CED8") if self.isChecked() else QColor(MUTED)
        if self._color:
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(self._color))
            p.drawEllipse(QPointF(18, rect.center().y()), 5, 5)
        text_left = 32 if self._color else 14
        font = p.font()
        font.setPixelSize(13)
        font.setBold(self.isChecked() or self._color is None)
        p.setFont(font)
        p.setPen(ink)
        count_w = 34
        name = QFontMetrics(font).elidedText(self._name, Qt.ElideRight, int(rect.width() - text_left - count_w - 8))
        p.drawText(QRectF(text_left, rect.top(), rect.width() - text_left - count_w, rect.height()),
                   Qt.AlignVCenter | Qt.AlignLeft, name)
        font.setBold(False)
        p.setFont(font)
        p.setPen(muted)
        p.drawText(QRectF(rect.right() - count_w - 8, rect.top(), count_w, rect.height()),
                   Qt.AlignVCenter | Qt.AlignRight, str(self._count))

    def enterEvent(self, event):
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self.update()
        super().leaveEvent(event)

    def contextMenuEvent(self, event):
        if self._on_delete is None:
            return
        menu = QMenu(self)
        delete_action = menu.addAction("Delete category")
        if menu.exec(event.globalPos()) == delete_action:
            self._on_delete()


def _next_review_pill(problem):
    """(text, tone) for the "when is it due" pill on a problem's row."""
    when = problem.get("nextReviewDate")
    if not when:
        return "Not scheduled", GREY
    display = _next_review_display(when)
    if _is_overdue(when):
        return f"Overdue · {display}", RED
    if display == "Today":
        return "Due today", AMBER
    return f"Next · {display}", GREY


def _problem_meta(problem):
    reviews = problem["reviewCount"]
    last = problem["lastReviewedAt"]
    solved = _first_solved_display(problem)
    parts = [f"{reviews} review{'s' if reviews != 1 else ''}",
             f"last reviewed {_relative_time(last).lower()}" if last else "never reviewed",
             "not solved yet" if solved == "Never" else f"first solved {solved}"]
    fastest = _fastest_display(problem)
    if fastest != "--:--":
        parts.append(f"fastest {fastest}")
    return " · ".join(parts)


class _ProblemRow(QFrame):
    """One problem: what it is, how hard, which subject, when it is due, its history in
    one quiet line, and the Start button. Click anywhere else to read its description;
    right-click to edit."""

    def __init__(self, problem, is_current, on_start, on_open, on_edit, on_score=None, on_delete=None):
        super().__init__()
        self.problem = problem
        self._on_open = on_open
        self._on_edit = on_edit
        self._on_score = on_score
        self._on_delete = on_delete
        self._is_current = is_current
        self.setObjectName("ProblemRow")
        self.setStyleSheet(
            "QFrame#ProblemRow{background:#FFFFFF;border:1px solid #E6E9EF;border-radius:14px;}"
            "QFrame#ProblemRow:hover{border:1px solid #C9CED8;}")
        _soft_shadow(self, 16, 14)
        color = problem["subjectColor"]
        self.first_solved_text = _first_solved_display(problem)

        outer = QHBoxLayout(self)
        outer.setContentsMargins(14, 12, 14, 12)
        outer.setSpacing(14)
        bar = QFrame()
        bar.setFixedWidth(5)
        bar.setStyleSheet(f"background:{color};border:none;border-radius:2px;")
        outer.addWidget(bar)

        text = QVBoxLayout()
        text.setSpacing(5)
        top = QHBoxLayout()
        top.setSpacing(8)
        self.name_label = QLabel(problem["name"])
        self.name_label.setStyleSheet(f"font-size:15px;font-weight:650;color:{INK};background:transparent;")
        top.addWidget(self.name_label)
        self.stars_label = QLabel(_star_text(problem["stars"]))
        self.stars_label.setStyleSheet("font-size:12px;color:#F5A623;background:transparent;")
        top.addWidget(self.stars_label)
        top.addWidget(chip(problem["subjectName"], color))
        top.addStretch(1)
        text.addLayout(top)
        self.meta_label = ElidedLabel(_problem_meta(problem))
        self.meta_label.setStyleSheet(f"font-size:12px;color:{MUTED};background:transparent;")
        text.addWidget(self.meta_label)
        outer.addLayout(text, 1)

        pill_text, tone = _next_review_pill(problem)
        self.next_pill = chip(pill_text, tone=tone)
        outer.addWidget(self.next_pill, 0, Qt.AlignVCenter)

        self.start_button = button("Start", _PRIMARY_INK, height=36)
        self.start_button.setObjectName("reviewStartButton")
        self.start_button.setMinimumWidth(78)
        self.start_button.clicked.connect(lambda: on_start(problem))
        if is_current:
            self.start_button.setEnabled(False)
            self.start_button.setToolTip("You are reviewing this one right now")
        outer.addWidget(self.start_button, 0, Qt.AlignVCenter)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton and self.childAt(event.position().toPoint()) is not self.start_button:
            self._on_open(self.problem)
        super().mousePressEvent(event)

    def contextMenuEvent(self, event):
        menu = QMenu(self)
        edit_action = menu.addAction("Edit Problem")
        score_action = menu.addAction("Score as a set...") if self._on_score else None
        delete_action = None
        if self._on_delete:
            menu.addSeparator()
            delete_action = menu.addAction("Delete problem")
            if self._is_current:
                delete_action.setEnabled(False)
                delete_action.setToolTip("You are reviewing this one right now")
        chosen = menu.exec(event.globalPos())
        if chosen is None:
            return
        if chosen == edit_action:
            self._on_edit(self.problem)
        elif chosen == score_action:
            self._on_score(self.problem)
        elif chosen == delete_action:
            self._on_delete(self.problem)


class ReviewTab(QWidget):
    def __init__(self):
        super().__init__()
        self._is_reviewing = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        title = QLabel("Review")
        title.setStyleSheet(f"font-size: 26px; font-weight: 700; color: {INK}; background: transparent;")
        title.setContentsMargins(28, 24, 28, 10)
        layout.addWidget(title)

        # The one review banner for every topic, above the tab strip: a review
        # of one topic stays in view while another topic's tab is open.
        self.banner = _ReviewBanner(on_finished=self._banner_finished, on_changed=self._rerender_start_cells)
        banner_holder = QVBoxLayout()
        banner_holder.setContentsMargins(28, 0, 28, 10)
        banner_holder.addWidget(self.banner)
        # Reviews paused and set aside to make room for another: collapsed rows.
        self._strip = _ParkedStrip(on_resume=self._resume_row, on_end=self._end_row)
        banner_holder.addWidget(self._strip)
        layout.addLayout(banner_holder)
        # Banner state of set-aside first attempts (their callbacks into the Add
        # Problem dialog can't be rebuilt from any store).
        self._stash = []

        self._tabs = QTabWidget()
        self._tabs.setDocumentMode(True)
        # Only tab-bar selectors, so nothing leaks into the topic views inside.
        self._tabs.setStyleSheet(
            "QTabWidget::pane { border: none; background: transparent; }"
            "QTabWidget::tab-bar { left: 28px; }"
            "QTabBar { background: transparent; }"
            f"QTabBar::tab {{ background: #FFFFFF; color: {INK}; border: 1px solid #E1E5EC; border-radius: 14px;"
            " padding: 6px 16px; margin: 0 6px 8px 0; font-size: 13px; font-weight: 600; }"
            "QTabBar::tab:hover:!selected { background: #F6F7FA; }"
            f"QTabBar::tab:selected {{ background: {INK}; color: #FFFFFF; border-color: {INK}; }}")
        self._tabs.tabBarClicked.connect(self._on_tab_bar_clicked)
        self._tabs.tabBar().setContextMenuPolicy(Qt.CustomContextMenu)
        self._tabs.tabBar().customContextMenuRequested.connect(self._on_tab_context_menu)
        layout.addWidget(self._tabs, 1)


        self._topic_views = {}
        self._topic_names = {}
        self._plus_index = None
        self.refresh()
        # A review's session can come back while this tab is already open
        # (switched to from the Tasks tab or the extension, or restored when
        # the task running over it ended) -- nothing else tells the banner, so
        # it looks again while it is hidden.
        self._follow_timer = QTimer(self)
        self._follow_timer.timeout.connect(self._follow_active_review)
        self._follow_timer.start(1000)

    def showEvent(self, event):
        self._refresh_strip()
        super().showEvent(event)

    def _follow_active_review(self):
        # The strip only matters while it can be seen (showEvent catches up).
        if self.isVisible():
            self._refresh_strip()
        if self.banner.is_running():
            return
        for view in self._topic_views.values():
            if view._resume_linked_session() or view._resume_independent_review():
                self._refresh_strip()
                return

    # ---- reviews set aside while another one runs --------------------------
    def park_current_review(self):
        """Pauses the review in the banner and collapses it into the strip."""
        snapshot = self.banner.park_current()
        if snapshot is None:
            return
        if snapshot["first_attempt_callback"] is not None:
            self._stash.append(snapshot)
        self._is_reviewing = False
        self._refresh_strip()

    def take_first_attempt_stash(self, event_id=None, stash_id=None):
        """Removes and returns a set-aside first attempt's banner state: the
        one with this stash id, or the session-backed one for this task."""
        for snapshot in self._stash:
            if (stash_id is not None and snapshot["id"] == stash_id) or (
                    stash_id is None and event_id is not None
                    and snapshot["problem"].get("eventId") == event_id
                    and snapshot["end_session_on_finish"]):
                self._stash.remove(snapshot)
                return snapshot
        return None

    def _parked_rows(self):
        rows = []
        status = session_manager.get_status()
        shown = self.banner._problem if self.banner.is_running() else None
        sessions = []
        if status.get("isActive") and status.get("isPaused"):
            sessions.append({**status, "parkId": None})
        sessions.extend(status.get("parkedSessions") or [])
        for session in sessions:
            if session.get("source") != "review":
                continue
            if shown is not None and _session_belongs_to(shown, session):
                continue
            first = session_manager.is_first_attempt(session)
            rows.append({
                "kind": "session", "key": session.get("parkId") or "active", "session": session,
                "name": "First attempt" if first else (session.get("reviewProblemName") or "Review"),
                "problemId": session.get("reviewProblemId"),
                "seconds": tasks_store.worked_seconds(session["startTime"], None, session.get("violationLog"))
                if session.get("startTime") else 0,
            })
        for parked in review_store.list_parked_reviews():
            rows.append({
                "kind": "independent", "key": parked["token"], "parked": parked,
                "name": parked["problemName"], "problemId": parked["problemId"],
                "seconds": parked["elapsedSeconds"],
            })
        for snapshot in self._stash:
            if not snapshot["end_session_on_finish"]:
                rows.append({
                    "kind": "stash", "key": snapshot["id"], "snapshot": snapshot, "name": "First attempt",
                    "problemId": None, "seconds": snapshot["accumulated_seconds"],
                })
        return rows

    def _refresh_strip(self):
        self._strip.set_rows(self._parked_rows())

    def parked_row_for_problem(self, problem_id):
        return next((r for r in self._parked_rows() if r["problemId"] == problem_id), None)

    def confirm_switch(self, verb, target_name):
        """Yes/No before pausing the running review for another one."""
        current = self.banner.current_label()
        return _confirm(
            self, "Switch review",
            f'Pause "{current}" and {verb} "{target_name}"? '
            "The one you are in will wait, collapsed, until you resume it.")

    def _resume_row(self, row):
        if self.banner.is_running() and not self.confirm_switch("resume", row["name"]):
            return
        self.park_current_review()
        kind = row["kind"]
        if kind == "session":
            session = row["session"]
            if session.get("parkId"):
                session_manager.swap_with_parked(session["parkId"])
            else:
                session_manager.resume_session()
            self._follow_active_review()
        elif kind == "independent":
            review_store.unpark_review(row["key"])
            # Straight onto the banner: _follow_active_review() would pick a
            # paused linked session (another collapsed row) ahead of it.
            for view in self._topic_views.values():
                if view._resume_independent_review():
                    break
        else:
            snapshot = row["snapshot"]
            self._stash.remove(snapshot)
            self.on_review_started()
            self.banner.start(
                snapshot["problem"], None, end_session_on_finish=False,
                first_attempt_callback=snapshot["first_attempt_callback"],
                first_attempt_cancelled_callback=snapshot["first_attempt_cancelled_callback"],
                accumulated_seconds=snapshot["accumulated_seconds"],
            )
        self._refresh_strip()

    def _end_row(self, row):
        if not _confirm(self, "End review", f'End "{row["name"]}" without saving it?'):
            return
        kind = row["kind"]
        cancelled = None
        if kind == "session":
            session = row["session"]
            if session.get("parkId"):
                session_manager.end_parked_session(park_id=session["parkId"])
            else:
                session_manager.end_session()
            snapshot = self.take_first_attempt_stash(event_id=session.get("eventId"))
            cancelled = snapshot["first_attempt_cancelled_callback"] if snapshot else None
        elif kind == "independent":
            review_store.abandon_parked_review(row["key"])
        else:
            snapshot = row["snapshot"]
            self._stash.remove(snapshot)
            cancelled = snapshot["first_attempt_cancelled_callback"]
        if cancelled is not None:
            cancelled()  # a first attempt: its Add Problem dialog comes back
        self._refresh_strip()
        self._rerender_start_cells()

    def _banner_finished(self):
        self.on_review_finished()
        for view in self._topic_views.values():
            view.refresh()
        # A review set aside earlier may now be the live one again.
        self._follow_active_review()

    def _rerender_start_cells(self):
        self._refresh_strip()
        for view in self._topic_views.values():
            view._render_table()

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
        # What was running before a rebuild: a no-op when the banner already is.
        self._follow_active_review()

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
        if not confirm_dialog.ask(
                self, "Delete topic",
                f'Delete "{name}" and all its categories and problems? This cannot be undone.',
                yes="Delete", risky=True):
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
        self.resize(440, 300)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 20)
        layout.setSpacing(8)
        layout.addWidget(dialog_title("New topic", "A topic is a tab on the Review page: one subject you study."))
        layout.addWidget(field_label("Name"))
        self._name_edit = WrapEdit()
        self._name_edit.setPlaceholderText("e.g. Math")
        self._name_edit.returnPressed.connect(self._create)
        layout.addWidget(self._name_edit)

        layout.addStretch(1)
        self._status_label = role_label("", "error")
        layout.addWidget(self._status_label)

        button_row = QHBoxLayout()
        button_row.addStretch(1)
        cancel_button = QPushButton("Cancel")
        cancel_button.clicked.connect(self.close)
        button_row.addWidget(cancel_button)
        create_button = accent(QPushButton("Create topic"))
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

        root = QHBoxLayout(self)
        root.setContentsMargins(28, 6, 28, 20)
        root.setSpacing(16)
        layout = QVBoxLayout()
        layout.setSpacing(10)
        root.addLayout(layout, 1)
        self._subject_filter = None   # a subject id, or None for every category
        self._search = ""
        self._sort = "due"
        self._category_buttons = {}

        layout.addLayout(self._build_header())
        self._summary_label = QLabel()
        self._summary_label.setStyleSheet(f"font-size: 13px; color: {MUTED}; background: transparent;")
        layout.addWidget(self._summary_label)
        self._rows = []

        if review_tab is not None:
            # The banner lives above the tabs, owned by the ReviewTab, which
            # also does the looking-for-a-session-to-follow.
            self._review_banner = review_tab.banner
        else:
            self._review_banner = _ReviewBanner(on_finished=self._banner_finished)
            layout.addWidget(self._review_banner)
            self._resume_if_active()
            # A review's session can come back while this view is already open
            # (switched to from the Tasks tab or the extension, or restored when
            # the task running over it ended) -- nothing else tells this view,
            # so it looks again while its banner is hidden.
            self._follow_timer = QTimer(self)
            self._follow_timer.timeout.connect(self._show_banner_for_linked_session)
            self._follow_timer.start(1000)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setStyleSheet("QScrollArea { background: transparent; border: none; }")
        self._list_container = QWidget()
        self._list_container.setObjectName("ProblemList")
        self._list_container.setStyleSheet("QWidget#ProblemList { background: transparent; }")
        self._list_layout = QVBoxLayout(self._list_container)
        self._list_layout.setContentsMargins(2, 2, 8, 16)
        self._list_layout.setSpacing(10)
        self._list_layout.setAlignment(Qt.AlignTop)
        scroll.setWidget(self._list_container)
        layout.addWidget(scroll, 1)

        root.addWidget(self._build_sidebar())

        self.refresh()

    def _show_banner_for_linked_session(self):
        if not self._review_banner.is_running():
            self._resume_linked_session() or self._resume_independent_review()

    def _resume_linked_session(self):
        """Starts the banner for this topic's task session if one exists,
        running or waiting behind another task. Returns True if it did."""
        status = session_manager.get_status()
        candidates = []
        if status.get("isActive"):
            candidates.append(status)
        candidates.extend(status.get("parkedSessions") or [])
        topic = review_store.get_topic(self._topic_id)
        if not topic or not topic.get("linkedTaskId"):
            return False
        for session in candidates:
            if session.get("source") == "review" and session.get("eventId") == topic["linkedTaskId"]:
                problem_name = session.get("reviewProblemName") or "this review"
                if self._review_tab:
                    self._review_tab.on_review_started()
                stashed = (
                    self._review_tab.take_first_attempt_stash(event_id=session.get("eventId"))
                    if self._review_tab and session_manager.is_first_attempt(session) else None)
                if stashed is not None:
                    self._review_banner.start(
                        stashed["problem"], token=None, end_session_on_finish=True,
                        first_attempt_callback=stashed["first_attempt_callback"],
                        first_attempt_cancelled_callback=stashed["first_attempt_cancelled_callback"],
                    )
                    return True
                if session_manager.is_first_attempt(session):
                    # The banner that was timing this first attempt is gone
                    # (app restart, or this view rebuilt), and with it the
                    # callback that hands the result to the Add Problem
                    # dialog -- without one, Finish > Submit saved nothing.
                    # A fresh, still-hidden dialog takes its place.
                    dialog = _register_popup(_AddProblemDialog(
                        self._topic_id, on_added=lambda _p: self.refresh(),
                        on_start_first_attempt=self._start_first_attempt,
                    ))
                    done, cancelled = self._first_attempt_callbacks(dialog)
                    self._review_banner.start(
                        {"name": problem_name, "topicId": self._topic_id, "eventId": session.get("eventId")},
                        token=None, end_session_on_finish=True,
                        first_attempt_callback=done, first_attempt_cancelled_callback=cancelled,
                    )
                    return True
                # token=None: review_store's own active-session tracking
                # (_active_sessions) is in-memory only and did not survive
                # whatever took this session_manager session and this
                # widget out of sync in the first place. Finish still logs
                # a real review_sessions row via reviewProblemId.
                self._review_banner.start(
                    {"name": problem_name, "id": session.get("reviewProblemId")},
                    token=None, end_session_on_finish=True,
                )
                return True
        return False

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
        if self._resume_linked_session():
            return
        self._resume_independent_review()

    def _resume_independent_review(self):
        """Starts the banner for the review being timed with no task session
        under it (it may have been started on another computer). Returns True
        if it did."""
        # Independent review -- one that was (or still is) running
        # alongside a completely different session, or with no session at
        # all, so it never touched session_manager (see _begin_review's
        # `not session_manager.is_active()` guard below). Unlike the case
        # above, review_store.get_active_review() DOES persist this across
        # a restart now (see review_store.py's ACTIVE_SESSION_PATH), so the
        # real token and original start time are both recoverable here.
        active = review_store.get_active_review()
        if not active:
            return False
        problem = review_store.get_problem(active["problemId"])
        if not problem or problem["topicId"] != self._topic_id:
            return False
        if self._review_tab:
            self._review_tab.on_review_started()
        self._review_banner.start(
            problem, token=active["token"], end_session_on_finish=False,
            resumed_started_at=datetime.fromisoformat(active["startedAt"]),
        )
        return True

    def _build_header(self):
        header = QHBoxLayout()
        header.setSpacing(6)

        self._due_button = QPushButton("Due")
        self._all_button = QPushButton("All")
        toggle_group = QButtonGroup(self)
        toggle_group.setExclusive(True)
        for pill in (self._due_button, self._all_button):
            pill.setCheckable(True)
            pill.setCursor(Qt.PointingHandCursor)
            pill.setFixedHeight(30)
            pill.setStyleSheet(_SEGMENT_STYLE)
            toggle_group.addButton(pill)
            header.addWidget(pill)
        self._due_button.setChecked(True)
        self._due_button.toggled.connect(lambda checked: checked and self._set_due_only(True))
        self._all_button.toggled.connect(lambda checked: checked and self._set_due_only(False))

        header.addStretch(1)

        missed_button = button("Log a mistake", SECONDARY_STYLE, height=36)
        missed_button.setToolTip("Add a problem you got wrong. It comes back for review soon.")
        missed_button.clicked.connect(
            lambda: _register_popup(_MissDialog(self._topic_id, on_done=self._after_quick_add)))
        header.addWidget(missed_button)

        paste_button = button("Paste a list", SECONDARY_STYLE, height=36)
        paste_button.setToolTip("Add many problems at once from a list, a spreadsheet column or a range.")
        paste_button.clicked.connect(
            lambda: _register_popup(_BatchDialog(self._topic_id, on_done=self._after_quick_add)))
        header.addWidget(paste_button)

        add_button = button("+ Add problem", _PRIMARY_INK, height=36)
        add_button.clicked.connect(self._open_add_problem)
        header.addWidget(add_button)

        return header

    def _after_quick_add(self, message=None):
        """New problems are not due yet, so the "Due" view would hide what was just added.
        Show everything and say what happened."""
        self._all_button.setChecked(True)
        self.refresh()
        if message:
            self._summary_label.setText(message)

    def _set_due_only(self, due_only):
        self._due_only = due_only
        self.refresh()

    # ---- the right-hand side: find, category, sort ----------------------------------

    def _build_sidebar(self):
        card = QFrame()
        card.setObjectName("ReviewSide")
        card.setFixedWidth(232)
        card.setStyleSheet("QFrame#ReviewSide { background: #FFFFFF; border: 1px solid #E6E9EF; border-radius: 16px; }")
        _soft_shadow(card, 18, 12)
        box = QVBoxLayout(card)
        box.setContentsMargins(14, 14, 14, 14)
        box.setSpacing(8)

        def heading(text):
            label = QLabel(text)
            label.setStyleSheet(f"font-size: 12px; font-weight: 700; color: {MUTED}; background: transparent;")
            return label

        box.addWidget(heading("Find"))
        self._search_edit = QLineEdit()
        self._search_edit.setPlaceholderText("Search name, subject or notes")
        self._search_edit.setClearButtonEnabled(True)
        self._search_edit.setToolTip("Type to filter the list. Matches a problem's name, subject and notes.")
        self._search_edit.setStyleSheet(
            "QLineEdit { background: #F7F8FA; border: 1px solid #E6E9EF; border-radius: 10px; padding: 7px 10px; "
            f"font-size: 13px; color: {INK}; }} QLineEdit:focus {{ border: 1.5px solid {INK}; background: #FFFFFF; }}")
        self._search_edit.textChanged.connect(self._set_search)
        box.addWidget(self._search_edit)

        box.addSpacing(6)
        category_head = QHBoxLayout()
        category_head.addWidget(heading("Category"), 1)
        new_category = link_button("+ New")
        new_category.setToolTip("Make a category to sort problems into. Right-click one to delete it.")
        new_category.clicked.connect(lambda: self._new_category())
        category_head.addWidget(new_category)
        box.addLayout(category_head)
        self._category_box = QVBoxLayout()
        self._category_box.setSpacing(2)
        box.addLayout(self._category_box)

        box.addSpacing(6)
        box.addWidget(heading("Sort by"))
        self._sort_combo = QComboBox()
        for key, label in SORT_CHOICES:
            self._sort_combo.addItem(label, key)
        self._sort_combo.setToolTip("How the list is ordered")
        self._sort_combo.currentIndexChanged.connect(self._set_sort)
        box.addWidget(self._sort_combo)
        box.addStretch(1)
        return card

    def _set_search(self, text):
        self._search = text.strip().lower()
        self.refresh()

    def _set_sort(self, _index):
        self._sort = self._sort_combo.currentData()
        self.refresh()

    def _set_subject_filter(self, subject_id):
        self._subject_filter = subject_id
        self.refresh()

    def _rebuild_categories(self, base):
        """One row per subject (plus "All"), each with how many problems it holds in this view."""
        while self._category_box.count():
            widget = self._category_box.takeAt(0).widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()
        self._category_buttons = {}
        counts = {}
        for problem in base:
            counts[problem["subjectId"]] = counts.get(problem["subjectId"], 0) + 1
        subjects = review_store.list_subjects(self._topic_id)
        if self._subject_filter is not None and not any(sub["id"] == self._subject_filter for sub in subjects):
            self._subject_filter = None
        entries = [(None, "All categories", None, len(base))] + [
            (sub["id"], sub["name"], sub["color"], counts.get(sub["id"], 0)) for sub in subjects]
        for subject_id, name, color, count in entries:
            row = _CategoryRow(
                name, color, count, checked=subject_id == self._subject_filter,
                on_delete=None if subject_id is None else
                (lambda sid=subject_id, n=name, c=count: self._delete_category(sid, n, c)))
            row.clicked.connect(lambda _c=False, sid=subject_id: self._set_subject_filter(sid))
            self._category_buttons[subject_id] = row
            self._category_box.addWidget(row)

    def _matches_search(self, problem):
        if not self._search:
            return True
        haystack = " ".join(str(problem.get(k) or "") for k in ("name", "subjectName", "descriptionText")).lower()
        return all(word in haystack for word in self._search.split())

    def _sorted(self, problems):
        if self._sort == "hardest":
            return sorted(problems, key=lambda p: -p["stars"])
        if self._sort == "newest":
            return sorted(problems, key=lambda p: p.get("dateAdded") or "", reverse=True)
        if self._sort == "name":
            return sorted(problems, key=lambda p: p["name"].lower())
        # "due": the store's own next-review / stars order, with overdue ones pinned on top
        # (stable sort keeps that ordering within each group).
        return sorted(problems, key=lambda p: not _is_overdue(p.get("nextReviewDate")))

    def refresh(self):
        base = review_store.list_problems(self._topic_id, due_only=self._due_only)
        self._rebuild_categories(base)
        shown = [p for p in base if self._subject_filter in (None, p["subjectId"]) and self._matches_search(p)]
        self._problems = self._sorted(shown)
        self._update_summary(len(base))
        self._render_table()

    def _update_summary(self, in_view=None):
        everything = len(review_store.list_problems(self._topic_id, due_only=False))
        due = len(review_store.list_problems(self._topic_id, due_only=True))
        lead = f"{due} due" if due else "Nothing due"
        text = f"{lead} · {everything} problem{'s' if everything != 1 else ''}"
        if in_view is not None and len(self._problems) != in_view:
            text += f" · showing {len(self._problems)}"
        self._summary_label.setText(text)

    def _render_table(self):
        """(Kept under its old name: ReviewTab asks every view to redraw.) One row per
        problem, rebuilt from scratch."""
        while self._list_layout.count():
            widget = self._list_layout.takeAt(0).widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()
        self._rows = []
        current_id = self._review_banner.current_problem_id()
        for problem in self._problems:
            row = _ProblemRow(
                problem, is_current=problem["id"] == current_id, on_start=self._start_problem,
                on_open=lambda p: _DescriptionPopup(p), on_edit=self._open_edit_problem,
                on_score=lambda p: _register_popup(_ScoreDialog(p, on_done=self.refresh)),
                on_delete=self._delete_problem)
            self._rows.append(row)
            self._list_layout.addWidget(row)
        if not self._rows:
            if self._search or self._subject_filter is not None:
                text = "Nothing matches. Clear the search or pick All categories."
            elif self._due_only:
                text = "Nothing to review today. Switch to All to see every problem."
            else:
                text = "No problems yet. Add one to start building the list."
            empty = QLabel(text)
            empty.setAlignment(Qt.AlignCenter)
            empty.setWordWrap(True)
            empty.setStyleSheet(
                f"color: {MUTED}; font-size: 14px; background: #FFFFFF; border: 1px dashed #D0D5DD;"
                "border-radius: 16px; padding: 32px 24px;")
            self._list_layout.addWidget(empty)

    def _delete_problem(self, problem):
        if not confirm_dialog.ask(
                self, "Delete problem",
                f'Delete "{problem["name"]}"? Its review history goes with it. This cannot be undone.',
                yes="Delete", risky=True):
            return
        review_store.delete_problem(problem["id"])
        self.refresh()

    def _new_category(self, on_done=None):
        _register_popup(_NewCategoryDialog(self._topic_id, on_done=on_done or (lambda _subject: self.refresh())))

    def _delete_category(self, subject_id, name, count):
        extra = f" and its {count} problem{'s' if count != 1 else ''}" if count else ""
        if not confirm_dialog.ask(
                self, "Delete category", f'Delete "{name}"{extra}? This cannot be undone.',
                yes="Delete", risky=True):
            return
        if self._subject_filter == subject_id:
            self._subject_filter = None
        review_store.delete_subject(subject_id)
        self.refresh()

    def _open_edit_problem(self, problem):
        _register_popup(_AddProblemDialog(self._topic_id, on_added=lambda _p: self.refresh(), problem=problem))

    def _start_problem(self, problem):
        tab = self._review_tab
        if tab is None:
            if not self._review_banner.is_running():
                _ReviewStartDialog(problem, on_start=self._begin_review)
            return
        if tab.banner.current_problem_id() == problem["id"] or tab.banner.is_finishing():
            return
        parked = tab.parked_row_for_problem(problem["id"])
        if parked is not None:
            # Already started and set aside: pick that one up, don't begin a second.
            tab._resume_row(parked)
            return
        if tab.banner.is_running():
            if not tab.confirm_switch("start", problem["name"]):
                return
            _ReviewStartDialog(problem, on_start=self._switch_to_review)
        else:
            _ReviewStartDialog(problem, on_start=self._begin_review)

    def _switch_to_review(self, problem):
        # Only now (the start dialog confirmed) does the running review step
        # aside -- cancelling that dialog must leave everything as it was.
        self._review_tab.park_current_review()
        self._begin_review(problem)

    def _begin_review(self, problem):
        # Whether this review is about to drive its own session_manager
        # session is decided FIRST, before ever touching review_store's own
        # _active_sessions tracking -- start_review() must only be called
        # for the case it's actually for (an independent review, timed on
        # its own). Calling it unconditionally used to leave a permanent,
        # never-synced duplicate elapsed-time tracker sitting in
        # review_store for a task-linked review too: session_manager's own
        # pause/resume (Tasks tab, the extension, Focus tab -- anything but
        # this banner's own Pause button) never touches review_store, so
        # that duplicate entry just kept counting real wall-clock time
        # since the review started, pause or no pause, forever diverging
        # from the correct pause-aware number this banner actually displays
        # (see _elapsed_seconds_now()'s end_session_on_finish branch).
        end_session_on_finish = False
        topic = review_store.get_topic(self._topic_id)
        # See _make_room_for(): the review gets its own blocking session unless the
        # SAME task's session is already running (then it rides beside it).
        task = tasks_store.get_task(topic["linkedTaskId"]) if topic and topic.get("linkedTaskId") else None
        if task and _make_room_for(task):
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
                hide_taskbar_badges=bool(task.get("hideTaskbarBadges")),
                stop_taskbar_flashing=bool(task.get("stopTaskbarFlashing")),
            )
            end_session_on_finish = True

        # token=None when end_session_on_finish: same as _resume_if_active()
        # rebuilding this same banner after a restart -- Finish still logs a
        # real review_sessions row via reviewProblemId (persisted in
        # session_manager's own state) and _ReviewBanner's
        # finish_review_for_problem fallback, see _complete_finish.
        token = None if end_session_on_finish else review_store.start_review(problem["id"])
        if not end_session_on_finish and token is None:
            # start_review() returns None for two different reasons now --
            # the problem no longer existing, or a review already being
            # timed (review_store.py's own one-at-a-time guard, which this
            # tab's _is_reviewing flag should already prevent reaching in
            # the normal case, but a review started from another surface --
            # api_server.py's route -- has no such flag). Tell them apart so
            # the message is actually right either way.
            if review_store.get_problem(problem["id"]) is None:
                QMessageBox.warning(self, "Carmen Focus", "That problem no longer exists.")
            else:
                QMessageBox.warning(self, "Carmen Focus", "A review is already in progress.")
            self.refresh()
            return

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
        event_id = None
        topic = review_store.get_topic(self._topic_id)
        task = tasks_store.get_task(topic["linkedTaskId"]) if topic and topic.get("linkedTaskId") else None
        if task and _make_room_for(task):
            session_manager.start_session(
                duration_minutes=tasks_store.BURNOUT_MINUTES,
                lock_mode=task["lockMode"],
                process_blocklist=task.get("processBlocklist", []),
                domain_whitelist=task.get("domainWhitelist", []),
                source="review",
                event_id=task["id"],
                event_title=f"{task['name']} - first attempt",
                review_problem_name=session_manager.FIRST_ATTEMPT_NAME,
                review_subject_name=None,
                hide_taskbar_badges=bool(task.get("hideTaskbarBadges")),
                stop_taskbar_flashing=bool(task.get("stopTaskbarFlashing")),
            )
            end_session_on_finish = True
            event_id = task["id"]

        if self._review_tab:
            self._review_tab.on_review_started()

        done, cancelled = self._first_attempt_callbacks(add_problem_dialog)
        # eventId lets the banner tell its own session from another task's
        # when this one is paused and waiting behind it (a first attempt has
        # no problem id to go by -- see _session_belongs_to()).
        self._review_banner.start(
            {"name": "your new problem", "topicId": self._topic_id, "eventId": event_id}, token=None,
            end_session_on_finish=end_session_on_finish,
            first_attempt_callback=done,
            first_attempt_cancelled_callback=cancelled,
        )

    def _first_attempt_callbacks(self, add_problem_dialog):
        def _on_first_attempt_done(elapsed_seconds, self_solved, shakiness):
            add_problem_dialog.apply_first_attempt(elapsed_seconds, self_solved, shakiness)
            add_problem_dialog.show()

        def _on_first_attempt_cancelled():
            # "End" (or the underlying session getting ended some other
            # way) mid first-attempt -- no time/outcome to apply, but the
            # dialog must still come back so the problem itself isn't lost
            # with no way to fill in details and save it.
            add_problem_dialog.show()

        return _on_first_attempt_done, _on_first_attempt_cancelled


class _ReviewBanner(QWidget):
    def __init__(self, on_finished, on_changed=None):
        super().__init__()
        self._on_finished = on_finished
        self._on_changed = on_changed
        self._session_token = None
        self._end_session_on_finish = False
        self._problem = None
        self._start_time = None
        self._accumulated_seconds = 0
        self._is_paused = False
        self._first_attempt_callback = None
        self._first_attempt_cancelled_callback = None
        self._first_attempt_started_at = None
        self._auto_paused = False
        self._was_on_break = False

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        card = QFrame()
        card.setObjectName("ReviewCard")
        card.setStyleSheet(
            "QFrame#ReviewCard { background: #FFFFFF; border: 1px solid #E6E9EF; border-radius: 18px; }")
        _soft_shadow(card)
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(20, 16, 20, 16)
        card_layout.setSpacing(2)

        self._problem_label = QLabel()
        self._problem_label.setAlignment(Qt.AlignCenter)
        self._problem_label.setStyleSheet(
            f"font-size: 14px; font-weight: 650; color: {MUTED}; background: transparent; border: none;"
        )
        card_layout.addWidget(self._problem_label)

        self._timer_label = QLabel("00:00")
        self._timer_label.setAlignment(Qt.AlignCenter)
        self._timer_label.setStyleSheet(
            f"font-size: 60px; {NUM_CSS} color: {INK}; background: transparent; border: none;"
        )
        card_layout.addWidget(self._timer_label)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)
        btn_row.addStretch(1)

        self._pause_btn = button("Pause", SECONDARY_STYLE, height=40)
        self._pause_btn.setFixedWidth(96)
        self._pause_btn.clicked.connect(self._pause_resume)
        btn_row.addWidget(self._pause_btn)

        finish_btn = button("Finish", _PRIMARY_INK, height=40)
        finish_btn.setFixedWidth(130)
        finish_btn.clicked.connect(self._finish)
        btn_row.addWidget(finish_btn)

        # Sandwiched between two stretches, Pause/Finish read as a centered
        # pair -- End sits outside that on its own, pinned to the row's
        # right edge (bottom-right of the timer card) rather than grouped
        # in with them, so it doesn't get mistaken for "end this review the
        # normal way" the way it did sitting between Pause and Finish.
        btn_row.addStretch(1)

        end_btn = button("End", DANGER_STYLE, height=40)
        end_btn.setFixedWidth(84)
        end_btn.clicked.connect(self._end_early)
        btn_row.addWidget(end_btn)

        card_layout.addLayout(btn_row)

        outer.addWidget(card)

        self._finishing = False
        self._tick_timer = QTimer(self)
        self._tick_timer.timeout.connect(self._tick)

        self.hide()

    def _publish_first_attempt(self):
        """Mirrors a first-attempt timing (no linked-task session driving it)
        into review_store so GET /status can show it to the extension."""
        if self._first_attempt_callback is None or self._end_session_on_finish:
            return
        review_store.publish_first_attempt(
            self._problem.get("topicId"),
            (self._first_attempt_started_at or datetime.now()).isoformat(),
            self._elapsed_seconds_now(),
            self._currently_paused(),
            auto_paused=self._auto_paused,
        )

    def _sync_first_attempt_with_pomodoro_break(self):
        """Break time isn't working time -- a first attempt (the only review
        kind this widget times with local pause bookkeeping, see
        _is_independent_review()) auto-pauses when a pomodoro flips to its
        break and auto-resumes when the break ends, same as review_store's
        auto_pause_for_break()/auto_resume_from_break() do for a
        problem-backed review. Edge-triggered on the break flag (not
        level-triggered) so a manual Resume during a break sticks instead of
        being re-paused a second later; and only a pause THIS method made is
        auto-resumed, never one the user chose."""
        if self._first_attempt_callback is None or self._end_session_on_finish:
            return
        on_break = bool(session_manager.get_status().get("isBreak"))
        entered_break = on_break and not self._was_on_break
        left_break = self._was_on_break and not on_break
        self._was_on_break = on_break
        if entered_break and not self._is_paused:
            self._is_paused = True
            self._auto_paused = True
            self._accumulated_seconds = self._elapsed_seconds_now()
            self._start_time = None
        elif left_break and self._is_paused and self._auto_paused:
            self._is_paused = False
            self._auto_paused = False
            self._start_time = datetime.now()

    def _is_independent_review(self):
        # A real review_store-tracked review (has a token) that ISN'T
        # driving session_manager's own session -- the case that can be
        # auto-paused for a pomodoro break (see review_store.py's
        # auto_pause_for_break/auto_resume_from_break) and is visible to the
        # extension via GET /status's reviewInProgress. Excludes the
        # first-attempt flow (_start_first_attempt always passes token=None
        # since the problem doesn't exist yet) -- that one keeps its own
        # local-only pause bookkeeping below, unchanged.
        return not self._end_session_on_finish and self._session_token is not None

    def _linked_view(self):
        """(status, is_parked) for the task session this banner rides on.
        Normally the live session. When a different task is running and this
        review's session is the one waiting in session_manager's parking spot,
        that parked session (frozen, paused). If neither is this review's, the
        session is gone: {"isActive": False}. Without this the banner would read
        -- and its buttons would pause, resume or end -- the OTHER task."""
        status = session_manager.get_status()
        problem = self._problem or {}
        if not self._end_session_on_finish or (problem.get("id") is None and problem.get("eventId") is None):
            return status, False
        if status.get("isActive") and _session_belongs_to(problem, status):
            return status, False
        for parked in status.get("parkedSessions") or []:
            if _session_belongs_to(problem, parked):
                return {**parked, "isActive": True, "isPaused": True}, True
        return {"isActive": False}, False

    def _elapsed_seconds_now(self):
        if self._end_session_on_finish:
            # Pause-aware off the linked task session's own startTime/
            # violationLog (same math as the Tasks tab's running card) --
            # this session can be paused/resumed from either tab, so its own
            # local start_time/accumulated_seconds bookkeeping (below)
            # can't be trusted to reflect a pause that happened elsewhere.
            status, _ = self._linked_view()
            if not status.get("startTime"):
                return 0
            return tasks_store.worked_seconds(status["startTime"], None, status.get("violationLog"))
        if self._is_independent_review():
            # review_store is the source of truth once a pomodoro's break
            # can pause this from outside this widget entirely (see
            # auto_pause_for_break()) -- local _start_time/_accumulated_seconds
            # bookkeeping below would silently drift from that.
            active = review_store.get_active_review()
            return active["elapsedSeconds"] if active else 0
        if self._start_time is None:
            return self._accumulated_seconds
        return self._accumulated_seconds + int((datetime.now() - self._start_time).total_seconds())

    def start(
        self, problem, token, end_session_on_finish=False,
        first_attempt_callback=None, first_attempt_cancelled_callback=None,
        resumed_started_at=None, accumulated_seconds=0,
    ):
        self._problem = problem
        self._session_token = token
        self._end_session_on_finish = end_session_on_finish
        self._first_attempt_callback = first_attempt_callback
        self._first_attempt_cancelled_callback = first_attempt_cancelled_callback
        # resumed_started_at: an independent review (review_store's own
        # _active_sessions, not a linked task session) recovered after an
        # app restart -- see _resume_if_active() -- must keep its real
        # original start time, not reset elapsed to zero just because this
        # widget instance is new.
        self._start_time = resumed_started_at or datetime.now()
        self._accumulated_seconds = accumulated_seconds
        self._is_paused = False
        is_first_attempt = first_attempt_callback is not None or (
            problem.get("id") is None and problem.get("name") == session_manager.FIRST_ATTEMPT_NAME
        )
        label = "Timing first attempt" if is_first_attempt else f"Reviewing: {problem['name']}"
        self._problem_label.setText(label)
        # Not hardcoded "00:00" -- when this is _resume_if_active() rebuilding
        # a fresh banner for a session that survived an app restart, real
        # elapsed (pause-aware, from session_manager) can already be nonzero,
        # and a paused session's _tick() never gets a chance to correct a
        # wrong initial value (see _tick()'s own comment).
        self._timer_label.setText(_format_mmss(self._elapsed_seconds_now()))
        self._pause_btn.setText(self._pause_button_text(self._currently_paused()))
        # Always available -- pausing here freezes the review's own elapsed
        # timer (and _start_first_attempt.../ordinary reviews that aren't
        # tied to a linked task session still get to pause) rather than only
        # showing up when there happens to be an underlying session to pause.
        self._pause_btn.setVisible(True)
        self._first_attempt_started_at = self._start_time
        self._auto_paused = False
        self._was_on_break = False
        self._publish_first_attempt()
        self._finishing = False
        self._tick_timer.start(1000)
        self.show()
        self._changed()

    def is_finishing(self):
        """True while the how-did-it-go dialog is open for the review just finished."""
        return self._finishing

    def current_label(self):
        problem = self._problem or {}
        if problem.get("id") is None and (
                self._first_attempt_callback is not None or problem.get("name") == session_manager.FIRST_ATTEMPT_NAME):
            return "your first attempt"
        return problem.get("name") or "this review"

    def park_current(self):
        """Pauses whatever this banner is timing and stops showing it. Returns
        what is needed to bring it back (the Add Problem callbacks of a first
        attempt live only here), or None if nothing is being timed."""
        if not self.is_running() or self._finishing:
            return None
        snapshot = {
            "id": uuid.uuid4().hex,
            "problem": self._problem,
            "end_session_on_finish": self._end_session_on_finish,
            "first_attempt_callback": self._first_attempt_callback,
            "first_attempt_cancelled_callback": self._first_attempt_cancelled_callback,
            "accumulated_seconds": 0,
        }
        if self._end_session_on_finish:
            status, is_parked = self._linked_view()
            if status.get("isActive") and not is_parked and not status.get("isPaused"):
                session_manager.pause_session()
        elif self._is_independent_review():
            review_store.park_active_review()
        else:
            snapshot["accumulated_seconds"] = self._elapsed_seconds_now()
        self._tick_timer.stop()
        review_store.clear_first_attempt()
        self._session_token = None
        self._end_session_on_finish = False
        self._start_time = None
        self._first_attempt_callback = None
        self._first_attempt_cancelled_callback = None
        self.hide()
        self._changed()
        return snapshot

    def current_problem_id(self):
        """The id of the problem being reviewed right now, or None (nothing
        running, or a first attempt, which has no problem yet)."""
        if not self.is_running():
            return None
        return (self._problem or {}).get("id")

    def _changed(self):
        if self._on_changed is not None:
            self._on_changed()

    def is_running(self):
        # While the "how did it go" dialog is open the banner is hidden but the
        # review is not over yet -- it must not be brought back underneath it.
        return self._tick_timer.isActive() or self._finishing

    def _currently_paused(self):
        # When a linked task session is driving this review, session_manager
        # is the source of truth for pause state -- it can also be
        # paused/resumed from the Tasks tab's own Pause button on the same
        # underlying session, and this banner needs to reflect that instead
        # of drifting out of sync with its own separate _is_paused flag.
        # An independent review can likewise be paused from outside this
        # widget entirely (a pomodoro break auto-pausing it, or the
        # extension's own pause/resume call) -- review_store is that one's
        # source of truth. Otherwise (the first-attempt flow, no linked
        # session and no review_store token) there's nothing external to
        # defer to, so _is_paused is authoritative.
        if self._end_session_on_finish:
            return self._linked_view()[0].get("isPaused", False)
        if self._is_independent_review():
            active = review_store.get_active_review()
            return bool(active and active.get("isPaused"))
        return self._is_paused

    def _pause_button_text(self, is_paused):
        if self._end_session_on_finish and self._linked_view()[1]:
            return "Switch to this"
        if is_paused and self._auto_paused:
            return "Resume (on break)"
        if is_paused and self._is_independent_review():
            active = review_store.get_active_review()
            if active and active.get("autoPaused"):
                return "Resume (on break)"
        return "Resume" if is_paused else "Pause"

    def _tick(self):
        if self._end_session_on_finish and not self._linked_view()[0].get("isActive"):
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
        if self._is_independent_review():
            # Finished, abandoned or replaced from another computer (or any
            # other path): nothing left here to time.
            active = review_store.get_active_review()
            if active is None or active["token"] != self._session_token:
                self._abandon_after_external_end()
                return
        self._apply_first_attempt_request()
        self._sync_first_attempt_with_pomodoro_break()
        is_paused = self._currently_paused()
        self._pause_btn.setText(self._pause_button_text(is_paused))
        # Updated unconditionally, even while paused -- _elapsed_seconds_now()
        # already freezes correctly at the pause point on its own (pause-aware
        # worked_seconds for a linked task, or the stored _accumulated_seconds
        # otherwise), so there's nothing wrong with recomputing it while
        # paused, and skipping the update here is what used to leave a
        # freshly-rebuilt-after-restart banner stuck showing "00:00" the
        # entire time it stayed paused.
        self._timer_label.setText(_format_mmss(self._elapsed_seconds_now()))
        self._publish_first_attempt()

    def _apply_first_attempt_request(self):
        """The extension asked (via /review/pause or /review/resume) to pause or resume a
        first attempt, which only this banner can do."""
        if self._first_attempt_callback is None or self._end_session_on_finish:
            return
        request = review_store.take_first_attempt_request()
        if request == "pause" and not self._currently_paused():
            self._pause_resume()
        elif request == "resume" and self._currently_paused():
            self._pause_resume()

    def _pause_resume(self):
        if self._end_session_on_finish and self._linked_view()[1]:
            # This review's session is waiting behind a different task: the
            # button brings it to the front (the other task takes its place).
            session_manager.swap_with_parked(self._linked_view()[0].get("parkId"))
            return
        if self._is_independent_review():
            # review_store is the source of truth here (see
            # _currently_paused()) -- delegate the actual state change to it
            # instead of a local flag, so a manual pause/resume from this
            # button is indistinguishable from one made through the
            # extension's own pause/resume call.
            if self._currently_paused():
                review_store.resume_active_review()
            else:
                review_store.pause_active_review()
            self._pause_btn.setText(self._pause_button_text(self._currently_paused()))
            return
        self._auto_paused = False
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
        review_store.clear_first_attempt()
        token = self._session_token
        first_attempt_cancelled_callback = self._first_attempt_cancelled_callback
        self._session_token = None
        self._end_session_on_finish = False
        self._start_time = None
        self._first_attempt_callback = None
        self._first_attempt_cancelled_callback = None
        self.hide()
        review_store.abandon_review(token)
        if first_attempt_cancelled_callback is not None:
            first_attempt_cancelled_callback()
        self._on_finished()

    def _end_early(self):
        self._tick_timer.stop()
        review_store.clear_first_attempt()
        token = self._session_token
        end_session = self._end_session_on_finish
        first_attempt_cancelled_callback = self._first_attempt_cancelled_callback
        self._session_token = None
        self._end_session_on_finish = False
        self._start_time = None
        self._first_attempt_callback = None
        self._first_attempt_cancelled_callback = None
        self.hide()
        # No-op when token is None (first-attempt mode -- there was never a
        # start_review() session to abandon).
        review_store.abandon_review(token)
        if end_session:
            _end_session_for_problem(self._problem)
        if first_attempt_cancelled_callback is not None:
            # Clicking "End" (or the session it rode on getting ended
            # externally) mid first-attempt used to just vanish -- the Add
            # Problem dialog stayed hidden with no way back to it, silently
            # discarding the in-progress problem with no chance to still
            # fill in details and save it. Reopen it blank instead (no
            # first-attempt time to apply, since the attempt was abandoned,
            # not finished).
            first_attempt_cancelled_callback()
        self._on_finished()

    def _finish(self):
        self._tick_timer.stop()
        self._finishing = True
        review_store.clear_first_attempt()
        token = self._session_token
        end_session = self._end_session_on_finish
        problem = self._problem
        elapsed = self._elapsed_seconds_now()
        # Grabbed now, before end_session() (called from _complete_finish,
        # after the post-review dialog closes) resets session_manager's
        # state -- only needed for the token=None recovery path below, but
        # harmless to capture unconditionally.
        started_at_iso = self._linked_view()[0].get("startTime")
        first_attempt_callback = self._first_attempt_callback
        self._session_token = None
        self._end_session_on_finish = False
        self._start_time = None
        self._first_attempt_callback = None
        self._first_attempt_cancelled_callback = None
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
            _end_session_for_problem(problem)
        else:
            _file_untimed_review(problem, elapsed)
        self._finishing = False
        self._on_finished()


def _file_untimed_review(problem, seconds, now=None):
    """A review that ran without a session of its own (another kind of session was running,
    so none could start) is still time on its task. Session History and the task's
    count both read finished sessions, so this files the review as one. Skipped when a
    session of the same task is running, because that session already counts it."""
    topic = review_store.get_topic((problem or {}).get("topicId")) if (problem or {}).get("topicId") else None
    task = tasks_store.get_task(topic["linkedTaskId"]) if topic and topic.get("linkedTaskId") else None
    if task is None or seconds < 1:
        return None
    status = session_manager.get_status()
    if status.get("isActive") and status.get("source") in ("task", "review") and status.get("eventId") == task["id"]:
        return None
    now = now or datetime.now()
    first_attempt = problem.get("id") is None
    subject = None if first_attempt else problem.get("subjectName")
    entry = {
        "startTime": (now - timedelta(seconds=seconds)).isoformat(),
        "endTime": now.isoformat(),
        "endType": "manual", "reason": None, "lockMode": task.get("lockMode", "soft"),
        "processBlocklist": [], "domainWhitelist": [], "violationCount": 0, "violationLog": [],
        "domainWhitelistAdditions": [], "processBlocklistExceptions": [],
        "source": "review", "eventId": task["id"],
        "eventTitle": f"{task['name']} - " + ("first attempt" if first_attempt else f"{subject} review"),
        "reviewProblemName": session_manager.FIRST_ATTEMPT_NAME if first_attempt else problem["name"],
        "reviewSubjectName": subject,
        "pomodoro": None, "sessionId": "untimed-" + uuid.uuid4().hex,
    }
    session_history.append_entry(entry)
    return entry


def _can_drive_over(status):
    """True when a review may start its own blocking session: nothing is running, or
    what is running is paused (it is set aside and comes back when the review ends)."""
    return not status.get("isActive") or bool(status.get("isPaused"))


def _make_room_for(task):
    """True when a review of `task` may start its own blocking session (the linked task's
    app/site rules). Nothing running, or a paused session: yes (the paused one is set
    aside and comes back). A running session of a DIFFERENT task is paused first, so its
    clock stops and the review's rules replace its rules. A running session of the SAME
    task keeps going and the review rides beside it (a pomodoro's breaks then pause the
    review by themselves). Anything else running (a manual or calendar session) is left
    alone."""
    status = session_manager.get_status()
    if _can_drive_over(status):
        return True
    if status.get("source") == "task" and status.get("eventId") != task["id"]:
        session_manager.pause_session()
        return True
    return False


def _confirm(parent, title, text):
    return confirm_dialog.ask(parent, title, text, yes="Yes", no="No")


class _ParkedStrip(QWidget):
    """Collapsed rows, one per review that was paused and set aside to make room
    for another: name, frozen time, Resume and End."""

    def __init__(self, on_resume, on_end):
        super().__init__()
        self._on_resume = on_resume
        self._on_end = on_end
        self._signature = None
        self._rows_layout = QVBoxLayout(self)
        self._rows_layout.setContentsMargins(0, 0, 0, 0)
        self._rows_layout.setSpacing(4)
        self.hide()

    def set_rows(self, rows):
        signature = [(r["kind"], r["key"], r["name"], r["seconds"]) for r in rows]
        if signature == self._signature:
            return
        self._signature = signature
        while self._rows_layout.count():
            widget = self._rows_layout.takeAt(0).widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()
        for row in rows:
            frame = QFrame()
            frame.setObjectName("ParkedRow")
            frame.setStyleSheet(
                "QFrame#ParkedRow { background: #FFFFFF; border: 1px dashed #C9CED8; border-radius: 12px; }"
                f"QLabel {{ border: none; background: transparent; color: {MUTED}; font-weight: 600; }}"
            )
            line = QHBoxLayout(frame)
            line.setContentsMargins(14, 6, 8, 6)
            line.setSpacing(8)
            line.addWidget(QLabel(f"⏸  {row['name']}  ·  {_format_mmss(row['seconds'])}  ·  paused"), 1)
            resume = button("Resume", SECONDARY_STYLE, height=30)
            resume.clicked.connect(lambda checked=False, r=row: self._on_resume(r))
            end = button("End", DANGER_STYLE, height=30)
            end.clicked.connect(lambda checked=False, r=row: self._on_end(r))
            line.addWidget(resume)
            line.addWidget(end)
            self._rows_layout.addWidget(frame)
            frame.show()  # added to an already-visible parent: not shown by itself
        self.setVisible(bool(rows))


def _end_session_for_problem(problem):
    """Ends the task session a review rode on -- which may be one of the
    waiting sessions rather than the running one."""
    problem = problem or {}
    status = session_manager.get_status()
    if (problem.get("id") is not None or problem.get("eventId") is not None)             and not _session_belongs_to(problem, status):
        for parked in status.get("parkedSessions") or []:
            if _session_belongs_to(problem, parked):
                session_manager.end_parked_session(park_id=parked.get("parkId"))
                return
    session_manager.end_session()


def _session_belongs_to(problem, session):
    """True if `session` (the running one or a waiting one) is the task session
    this review banner rides on. A review is matched by its problem id; a first
    attempt has no problem yet, so it is matched by being a first-attempt
    session of the same linked task."""
    if problem.get("id") is not None:
        return session.get("reviewProblemId") == problem["id"]
    return (
        problem.get("eventId") is not None
        and session_manager.is_first_attempt(session)
        and session.get("eventId") == problem["eventId"]
    )


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
            btn.setFixedSize(40, 40)
            btn.setCheckable(True)
            btn.setStyleSheet(
                "QPushButton { border-radius: 20px; font-size: 14px; font-weight: 600; "
                "background: #F2F4F7; color: #1B2230; border: none; padding: 0; }"
                "QPushButton:hover { background: #E7EAF0; }"
                "QPushButton:checked { background: #1B2230; color: white; }"
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
    "QPushButton { padding: 14px 12px; border-radius: 12px; font-size: 14px; font-weight: 600; "
    "background: #F2F4F7; color: #1B2230; border: none; }"
    "QPushButton:hover { background: #E7EAF0; }"
    "QPushButton:checked { background: #1B2230; color: white; }"
)


class _PostReviewDialog(QWidget):
    def __init__(self, problem_name, on_submit):
        super().__init__(None, Qt.WindowStaysOnTopHint)
        self.setObjectName("PopupBg")
        self.setWindowTitle("Carmen Focus — Review complete")
        self.resize(440, 400)
        self._on_submit = on_submit
        self._self_solved = None
        self._method_chosen = False
        self._shakiness = 3
        self._submitted = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 20)
        layout.setSpacing(12)
        layout.addWidget(dialog_title("How did it go?", problem_name))

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
        shak_layout.addWidget(field_label("How shaky was it?", "1 solid, 5 very shaky"))
        self._shak_picker = _ShakinessPicker(initial=3, on_changed=self._set_shakiness)
        shak_layout.addWidget(self._shak_picker)
        self._shak_section.setVisible(False)
        layout.addWidget(self._shak_section)

        layout.addStretch(1)
        submit_btn = accent(QPushButton("Save result"))
        submit_btn.setMinimumHeight(42)
        submit_btn.setEnabled(False)
        submit_btn.clicked.connect(self._submit)
        layout.addWidget(submit_btn)
        self._submit_btn = submit_btn

        self._hint_label = role_label('Choose "Solved it!" or "Checked the answer" first.', "error", wrap=True)
        self._hint_label.setAlignment(Qt.AlignCenter)
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
        self.resize(480, 600)
        self._problem = problem
        self._on_start = on_start

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 20)
        layout.setSpacing(8)

        header_row = QHBoxLayout()
        name_label = role_label(problem["name"], "title", wrap=True)
        header_row.addWidget(name_label, 1)
        stars_label = QLabel(_star_text(problem["stars"]))
        stars_label.setStyleSheet("color: #F5A623; font-size: 18px;")
        header_row.addWidget(stars_label, 0, Qt.AlignTop)
        layout.addLayout(header_row)

        layout.addWidget(role_label(_subject_and_task_text(problem), "subtitle"))

        stats_row = QHBoxLayout()
        stats_row.addWidget(role_label(f"Fastest {_fastest_display(problem)}", "hint"))
        stats_row.addStretch(1)
        stats_row.addWidget(role_label(
            f"{problem['reviewCount']} attempt{'s' if problem['reviewCount'] != 1 else ''}", "hint"))
        layout.addLayout(stats_row)

        first_attempt_text = _first_attempt_text(problem)
        if first_attempt_text:
            layout.addWidget(role_label(first_attempt_text, "hint"))

        _build_review_timeline(layout, problem)

        _build_description_content(layout, problem)

        start_button = accent(QPushButton("Start review"))
        start_button.setMinimumHeight(42)
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
        self.resize(440, 300)
        self._topic = topic
        self._on_saved = on_saved

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 20)
        layout.setSpacing(8)
        layout.addWidget(dialog_title(
            "Link this tab to a task", f"Reviews in \"{topic['name']}\" count toward that task's time."))
        layout.addWidget(field_label("Task"))

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

        layout.addStretch(1)
        btn_row = QHBoxLayout()
        btn_row.addStretch(1)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.close)
        btn_row.addWidget(cancel)
        save = accent(QPushButton("Save link"))
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
        self.resize(440, 280)
        self._topic_id = topic_id
        self._on_renamed = on_renamed

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 20)
        layout.setSpacing(8)
        layout.addWidget(dialog_title("Rename tab"))
        layout.addWidget(field_label("New name"))
        self._name_edit = WrapEdit(current_name)
        self._name_edit.selectAll()
        self._name_edit.returnPressed.connect(self._save)
        layout.addWidget(self._name_edit)

        layout.addStretch(1)
        self._status_label = role_label("", "error", wrap=True)
        layout.addWidget(self._status_label)

        btn_row = QHBoxLayout()
        btn_row.addStretch(1)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.close)
        btn_row.addWidget(cancel)
        save = accent(QPushButton("Rename"))
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
        self.resize(520, 520)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 20)
        layout.setSpacing(8)

        header_row = QHBoxLayout()
        header_row.addWidget(role_label(problem["name"], "title", wrap=True), 1)
        stars_label = QLabel(_star_text(problem["stars"]))
        stars_label.setStyleSheet("color: #F5A623; font-size: 18px;")
        header_row.addWidget(stars_label, 0, Qt.AlignTop)
        layout.addLayout(header_row)
        layout.addWidget(role_label(_subject_and_task_text(problem), "subtitle"))

        first_attempt_text = _first_attempt_text(problem)
        if first_attempt_text:
            layout.addWidget(role_label(first_attempt_text, "hint"))

        _build_review_timeline(layout, problem)

        _build_description_content(layout, problem)

        self.show()
        _register_popup(self)


class _NewCategoryDialog(QWidget):
    """A name and a colour. Reached from the sidebar, the paste dialog and the mistake dialog,
    so a category never needs the Add problem form."""

    def __init__(self, topic_id, on_done, name=""):
        super().__init__(None, Qt.WindowStaysOnTopHint)
        self.setObjectName("PopupBg")
        self.setWindowTitle("New category")
        self.resize(440, 340)
        self._topic_id = topic_id
        self._on_done = on_done
        used = {s["color"].lower() for s in review_store.list_subjects(topic_id)}
        self._color = next((c for c in COLOR_PALETTE if c.lower() not in used), COLOR_PALETTE[0])

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 20)
        layout.setSpacing(8)
        layout.addWidget(dialog_title("New category", "A group of problems, like Sorting or Chapter 7."))
        layout.addWidget(field_label("Name"))
        self._name = QLineEdit(name)
        self._name.setPlaceholderText("Sorting")
        self._name.returnPressed.connect(self._save)
        layout.addWidget(self._name)
        layout.addWidget(field_label("Colour"))
        swatches = QHBoxLayout()
        self._swatches = {}
        for hexval in COLOR_PALETTE:
            swatch = QPushButton()
            swatch.setFixedSize(26, 26)
            swatch.setCursor(Qt.PointingHandCursor)
            swatch.clicked.connect(lambda _c=False, h=hexval: self._pick(h))
            self._swatches[hexval] = swatch
            swatches.addWidget(swatch)
        swatches.addStretch(1)
        layout.addLayout(swatches)
        self._pick(self._color)
        self._status = role_label("", "error", wrap=True)
        layout.addWidget(self._status)
        layout.addStretch(1)
        row = QHBoxLayout()
        row.addStretch(1)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.close)
        row.addWidget(cancel)
        save = accent(QPushButton("Create"))
        save.clicked.connect(self._save)
        row.addWidget(save)
        layout.addLayout(row)
        self.show()
        self._name.setFocus()

    def _pick(self, hexval):
        self._color = hexval
        for h, swatch in self._swatches.items():
            border = "3px solid #1B2230" if h == hexval else "1px solid #0002"
            swatch.setStyleSheet(f"background:{h};border:{border};border-radius:13px;padding:0;")

    def _save(self):
        name = self._name.text().strip()
        if not name:
            self._status.setText("Give it a name.")
            return
        try:
            subject = review_store.create_subject(self._topic_id, name, self._color)
        except (review_store.DuplicateNameError, review_store.DuplicateColorError) as e:
            self._status.setText(str(e))
            return
        if subject is None:
            self._status.setText("Could not create the category.")
            return
        self.close()
        self._on_done(subject)


def _category_combo(topic_id, select_id=None, include_mistakes=False):
    """A combo of the topic's categories. Index data is the category id; the last entry
    (data "new") means "make a new one"."""
    combo = QComboBox()
    for subject in review_store.list_subjects(topic_id):
        combo.addItem(subject["name"], subject["id"])
    if include_mistakes and combo.findText("Mistakes") < 0:
        combo.insertItem(0, "Mistakes (made for you)", None)
    combo.addItem("+ New category...", "new")
    index = combo.findData(select_id) if select_id is not None else 0
    combo.setCurrentIndex(max(0, index))
    return combo


class _MissDialog(QWidget):
    """Log a problem you got wrong: what it was, where to file it, how hard it was."""

    def __init__(self, topic_id, on_done):
        super().__init__(None, Qt.WindowStaysOnTopHint)
        self.setObjectName("PopupBg")
        self.setWindowTitle("Log a mistake")
        self.resize(460, 480)
        self._topic_id = topic_id
        self._on_done = on_done
        self._previous = 0

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 20)
        layout.setSpacing(8)
        layout.addWidget(dialog_title(
            "Log a mistake", "Add a problem you got wrong. It comes back for review soon, "
            "and sooner the harder you say it was."))
        layout.addWidget(field_label("Problem"))
        self._edit = QLineEdit()
        self._edit.setPlaceholderText("Ch 7 #14, or Fence Painting")
        self._edit.returnPressed.connect(self._save)
        layout.addWidget(self._edit)

        layout.addWidget(field_label("Category"))
        self._category = _category_combo(topic_id, include_mistakes=True)
        self._category.activated.connect(self._category_picked)
        layout.addWidget(self._category)

        layout.addWidget(field_label("How hard was it? (5 is hardest)"))
        self._stars = _StarPicker(3)
        layout.addWidget(self._stars)

        layout.addStretch(1)
        self._status = role_label("", "hint", wrap=True)
        layout.addWidget(self._status)
        row = QHBoxLayout()
        row.addStretch(1)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.close)
        row.addWidget(cancel)
        save = accent(QPushButton("Add to review"))
        save.clicked.connect(self._save)
        row.addWidget(save)
        layout.addLayout(row)
        self.show()
        self._edit.setFocus()

    def _category_picked(self, index):
        if self._category.itemData(index) != "new":
            return
        self._category.setCurrentIndex(0)
        _register_popup(_NewCategoryDialog(self._topic_id, on_done=self._category_made))

    def _category_made(self, subject):
        self._category.blockSignals(True)
        while self._category.count():
            self._category.removeItem(0)
        for item in review_store.list_subjects(self._topic_id):
            self._category.addItem(item["name"], item["id"])
        self._category.addItem("+ New category...", "new")
        self._category.setCurrentIndex(self._category.findData(subject["id"]))
        self._category.blockSignals(False)

    def _save(self):
        import review_quicklog
        text = self._edit.text()
        if not text.strip():
            self._status.setText("Type which problem it was first.")
            return
        result = review_quicklog.log_miss(
            self._topic_id, text, subject_id=self._category.currentData() if self._category.currentData() != "new" else None,
            stars=self._stars.value())
        if result is None:
            self._status.setText("Could not add it.")
            return
        problem = result["problem"]
        verb = "Logged again" if result["repeat"] else "Added"
        self.close()
        self._on_done(f'{verb} "{problem["name"]}" ({problem["stars"]} stars). Back for review on {problem["nextReviewDate"]}.')


class _BatchDialog(QWidget):
    """Paste names (one per line, a spreadsheet column, or a range like
    'Ch 7 problems 1-30') and see exactly what will be added first."""

    def __init__(self, topic_id, on_done):
        super().__init__(None, Qt.WindowStaysOnTopHint)
        self.setObjectName("PopupBg")
        self.setWindowTitle("Paste a list")
        self.resize(520, 660)
        self._topic_id = topic_id
        self._on_done = on_done

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 20)
        layout.setSpacing(8)
        layout.addWidget(dialog_title(
            "Paste a list", "One problem per line. 'Ch 7 problems 1-30' becomes thirty."))

        layout.addWidget(field_label("Add to category"))
        self._subject = _category_combo(topic_id)
        self._subject.activated.connect(self._category_picked)
        layout.addWidget(self._subject)

        layout.addWidget(field_label("How hard? (5 is hardest)"))
        self._stars = _StarPicker(3)
        layout.addWidget(self._stars)

        layout.addWidget(field_label("The list"))
        self._text = QPlainTextEdit()
        self._text.setPlaceholderText("Fence Painting\nSleepy Cow Herding\nCh 7 problems 1-30")
        self._text.setMinimumHeight(150)
        self._text.textChanged.connect(self._preview)
        layout.addWidget(self._text, 1)

        self._summary = role_label("", "hint", wrap=True)
        layout.addWidget(self._summary)

        row = QHBoxLayout()
        row.addStretch(1)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.close)
        row.addWidget(cancel)
        self._add = accent(QPushButton("Add"))
        self._add.clicked.connect(self._save)
        row.addWidget(self._add)
        layout.addLayout(row)
        self._preview()
        self.show()

    def _category_picked(self, index):
        if self._subject.itemData(index) != "new":
            return
        self._subject.setCurrentIndex(0)
        _register_popup(_NewCategoryDialog(self._topic_id, on_done=self._category_made))

    def _category_made(self, subject):
        self._subject.blockSignals(True)
        while self._subject.count():
            self._subject.removeItem(0)
        for item in review_store.list_subjects(self._topic_id):
            self._subject.addItem(item["name"], item["id"])
        self._subject.addItem("+ New category...", "new")
        self._subject.setCurrentIndex(self._subject.findData(subject["id"]))
        self._subject.blockSignals(False)
        self._preview()

    def _names(self):
        import review_quicklog
        return review_quicklog.parse_batch(self._text.toPlainText())

    def _chosen_subject(self):
        data = self._subject.currentData()
        return data if isinstance(data, int) else None

    def _preview(self):
        names = self._names()
        if self._chosen_subject() is None:
            self._summary.setText("Make a category first (pick '+ New category...' above), then paste.")
            self._add.setEnabled(False)
            return
        self._add.setEnabled(bool(names))
        if not names:
            self._summary.setText("Nothing to add yet. Press Enter for a new line.")
            self._add.setText("Add")
            return
        shown = ", ".join(names[:4]) + (" ..." if len(names) > 4 else "")
        self._summary.setText(f"{len(names)} to add: {shown}")
        self._add.setText(f"Add {len(names)}")

    def _save(self):
        import review_quicklog
        names = self._names()
        subject_id = self._chosen_subject()
        if not names or subject_id is None:
            return
        created, skipped = review_quicklog.add_batch(self._topic_id, subject_id, names, self._stars.value())
        message = f"Added {created} problem{'s' if created != 1 else ''} to {self._subject.currentText()}."
        if skipped:
            message += f" {skipped} already there."
        self.close()
        self._on_done(message)


class _ScoreDialog(QWidget):
    """A set of questions scored as one: the result feeds the usual schedule
    (90%+ solid, 60-89% shaky, under 60% counts as needing the answers)."""

    def __init__(self, problem, on_done):
        super().__init__(None, Qt.WindowStaysOnTopHint)
        self.setObjectName("PopupBg")
        self.setWindowTitle("Score a set")
        self.resize(420, 330)
        self._problem = problem
        self._on_done = on_done

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 20)
        layout.setSpacing(8)
        layout.addWidget(dialog_title(problem["name"], "How did the set go?"))

        score_row = QHBoxLayout()
        self._correct = QLineEdit()
        self._correct.setPlaceholderText("right")
        self._total = QLineEdit()
        self._total.setPlaceholderText("out of")
        for edit in (self._correct, self._total):
            edit.textChanged.connect(self._preview)
            edit.returnPressed.connect(self._save)
            score_row.addWidget(edit)
        layout.addLayout(score_row)

        self._status = role_label("", "hint", wrap=True)
        layout.addWidget(self._status)
        layout.addStretch(1)

        row = QHBoxLayout()
        row.addStretch(1)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.close)
        row.addWidget(cancel)
        save = accent(QPushButton("Save score"))
        save.clicked.connect(self._save)
        row.addWidget(save)
        layout.addLayout(row)
        self.show()

    def _numbers(self):
        try:
            return int(self._correct.text()), int(self._total.text())
        except ValueError:
            return None

    def _preview(self):
        import review_quicklog
        numbers = self._numbers()
        outcome = review_quicklog.outcome_for_score(*numbers) if numbers and numbers[0] <= numbers[1] else None
        if outcome is None:
            self._status.setText("")
        elif not outcome[0]:
            self._status.setText("Counts as needing the answers: back to the start of the schedule.")
        elif outcome[1] <= 2:
            self._status.setText("Shaky: it comes back sooner than usual.")
        else:
            self._status.setText("Solid: it moves to the next, longer interval.")

    def _save(self):
        import review_quicklog
        numbers = self._numbers()
        if not numbers or review_quicklog.score_set(self._problem["id"], *numbers) is None:
            self._status.setText("Enter how many you got right and how many there were.")
            return
        self.close()
        self._on_done()


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
        self.resize(500, 760)
        self._topic_id = topic_id
        self._on_added = on_added
        self._on_start_first_attempt = on_start_first_attempt
        self._problem = problem
        self._photo_path = None
        self._pending_first_attempt = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 20)
        layout.setSpacing(8)
        layout.addWidget(dialog_title(
            "Edit problem" if self._editing else "Add a problem",
            "Its history and schedule are not touched." if self._editing
            else "It joins the list and comes back for review on a spaced schedule."))

        # Only offered when adding (not editing) and the caller wired up a
        # handler -- editing an existing problem has nothing to "attempt"
        # here, and _TopicView is the only caller that passes the handler.
        if not self._editing and self._on_start_first_attempt is not None:
            # No name/subject/description required to click this -- timing
            # starts immediately, on the same embedded review banner every
            # other review uses, and the form gets filled in afterward, once
            # there's actually something to fill in about.
            first_attempt_button = accent(QPushButton("Start first attempt now"))
            first_attempt_button.setMinimumHeight(40)
            first_attempt_button.setToolTip("Start the timer straight away and fill the form in afterwards.")
            first_attempt_button.clicked.connect(self._start_first_attempt)
            layout.addWidget(first_attempt_button)

            self._first_attempt_status = QLabel()
            self._first_attempt_status.setStyleSheet("color: #157F3B; font-size: 12.5px;")
            self._first_attempt_status.setWordWrap(True)
            self._first_attempt_status.setVisible(False)
            layout.addWidget(self._first_attempt_status)

        layout.addWidget(_bold_label("Name"))
        self._name_edit = WrapEdit(problem["name"] if problem else "")
        layout.addWidget(self._name_edit)

        layout.addWidget(_bold_label("Subject"))
        subject_row = QHBoxLayout()
        self._subject_combo = QComboBox()
        subject_row.addWidget(self._subject_combo, 1)
        add_subject_button = QPushButton("+ New subject")
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

        self._link_edit = WrapEdit(problem.get("descriptionLink") or "" if problem else "")
        self._link_edit.setPlaceholderText("https://example.com/problem")
        self._description_stack.addWidget(self._link_edit)

        layout.addWidget(self._description_stack, 1)

        self._text_button.toggled.connect(lambda c: c and self._description_stack.setCurrentIndex(0))
        self._photo_button.toggled.connect(lambda c: c and self._description_stack.setCurrentIndex(1))
        self._link_button.toggled.connect(lambda c: c and self._description_stack.setCurrentIndex(2))

        initial_type = problem["descriptionType"] if problem else "text"
        {"text": self._text_button, "photo": self._photo_button, "link": self._link_button}[initial_type].setChecked(True)

        self._status_label = role_label("", "error")
        layout.addWidget(self._status_label)

        button_row = QHBoxLayout()
        button_row.addStretch(1)
        cancel_button = QPushButton("Cancel")
        cancel_button.clicked.connect(self.close)
        button_row.addWidget(cancel_button)
        submit_button = accent(QPushButton("Save changes" if self._editing else "Add problem"))
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

        layout.addWidget(field_label("New subject name"))
        self._new_subject_name = QLineEdit()
        layout.addWidget(self._new_subject_name)

        layout.addWidget(field_label("Colour"))
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
        save_button = accent(QPushButton("Save subject"))
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
                try:
                    with open(self._photo_path, "rb") as f:
                        photo_bytes = f.read()
                except OSError:
                    self._status_label.setText("Could not read the chosen image -- choose it again.")
                    return None
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
    """The small grey label above a field (the name is older than the look)."""
    return field_label(text)
