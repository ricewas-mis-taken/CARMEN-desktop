"""Save a finished session as an attempt at a problem. Two tabs, each its own form: add the time to a problem that
is already on the list, or add it as a new problem."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QButtonGroup, QCheckBox, QComboBox, QHBoxLayout, QLabel, QPushButton, QSpinBox, QStackedWidget, QVBoxLayout,
    QWidget,
)

import attempt_link
import review_store
from qt_ui.ui_kit import accent, dialog_title, field_label, role_label
from qt_ui.wrap_edit import WrapEdit

_open = set()          # keeps an open window alive


def _minutes_text(seconds):
    minutes, secs = divmod(int(seconds), 60)
    return f"{minutes}:{secs:02d}"


class _Outcome(QWidget):
    """Solved it or checked the answer, and how shaky: asked the same way on both tabs."""

    def __init__(self):
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        self.solved = QCheckBox("I solved it myself")
        self.solved.setChecked(True)
        layout.addWidget(self.solved)
        row = QHBoxLayout()
        row.addWidget(QLabel("How shaky (1 solid, 5 very shaky)"))
        self.shaky = QSpinBox()
        self.shaky.setRange(1, 5)
        self.shaky.setValue(3)
        row.addWidget(self.shaky)
        row.addStretch(1)
        layout.addLayout(row)
        self.solved.toggled.connect(self.shaky.setEnabled)


class _Picker(QWidget):
    """Topic and subject, and (with `with_problems`) the problems under them."""

    def __init__(self, session, with_problems):
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        layout.addWidget(field_label("Topic"))
        self.topic = QComboBox()
        layout.addWidget(self.topic)
        layout.addWidget(field_label("Subject"))
        self.subject = QComboBox()
        layout.addWidget(self.subject)
        self.problem = None
        if with_problems:
            layout.addWidget(field_label("Problem"))
            self.problem = QComboBox()
            layout.addWidget(self.problem)
        topics = review_store.list_topics()
        for topic in topics:
            self.topic.addItem(topic["name"], topic["id"])
        linked = next((i for i, t in enumerate(topics) if t.get("linkedTaskId")
                       and t["linkedTaskId"] == session.get("eventId")), None)
        if linked is not None:
            self.topic.setCurrentIndex(linked)
        self.topic.currentIndexChanged.connect(self._fill_subjects)
        self.subject.currentIndexChanged.connect(self._fill_problems)
        self._fill_subjects()

    def _fill_subjects(self):
        self.subject.blockSignals(True)
        self.subject.clear()
        topic_id = self.topic.currentData()
        for subject in (review_store.list_subjects(topic_id) if topic_id is not None else []):
            self.subject.addItem(subject["name"], subject["id"])
        self.subject.blockSignals(False)
        self._fill_problems()

    def _fill_problems(self):
        if self.problem is None:
            return
        self.problem.clear()
        topic_id, subject_id = self.topic.currentData(), self.subject.currentData()
        if topic_id is not None and subject_id is not None:
            for problem in review_store.list_problems(topic_id, due_only=False):
                if problem.get("subjectId") == subject_id:
                    self.problem.addItem(problem["name"], problem["id"])


class _ExistingPanel(QWidget):
    """Add the time to a problem that is already on the list."""

    def __init__(self, session):
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        self.picker = _Picker(session, with_problems=True)
        layout.addWidget(self.picker)
        self.outcome = _Outcome()
        layout.addWidget(self.outcome)
        layout.addStretch(1)

    def problem_id(self):
        """(problem id, error text)."""
        problem_id = self.picker.problem.currentData()
        if problem_id is None:
            return None, "Pick a problem, or use the Add new tab."
        return problem_id, None


class _NewPanel(QWidget):
    """Make a new problem and put the time on it."""

    def __init__(self, session):
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        self.picker = _Picker(session, with_problems=False)
        layout.addWidget(self.picker)
        layout.addWidget(field_label("Name of the new problem"))
        self.name = WrapEdit("")
        layout.addWidget(self.name)
        stars_row = QHBoxLayout()
        stars_row.addWidget(field_label("Stars"))
        self.stars = QComboBox()
        for n in range(1, 6):
            self.stars.addItem("★" * n, n)
        stars_row.addWidget(self.stars, 1)
        layout.addLayout(stars_row)
        layout.addWidget(field_label("Notes (optional)"))
        self.notes = WrapEdit("")
        layout.addWidget(self.notes)
        self.outcome = _Outcome()
        layout.addWidget(self.outcome)
        layout.addStretch(1)

    def problem_id(self):
        """(id of the new problem, error text)."""
        subject_id = self.picker.subject.currentData()
        if subject_id is None:
            return None, "Pick a subject first (add one in the Review tab if there is none)."
        name = self.name.text().strip()
        if not name:
            return None, "Name the new problem."
        try:
            created = review_store.create_problem(
                self.picker.topic.currentData(), subject_id, name, self.stars.currentData(), "text",
                description_text=self.notes.text().strip() or "Added from the session history.")
        except review_store.DuplicateNameError as exc:
            return None, f"{exc} Use the Add to existing tab instead."
        if created is None:
            return None, "Could not make that problem."
        return created["id"], None


class AttemptLinkDialog(QWidget):
    def __init__(self, session, on_saved=None):
        super().__init__(None, Qt.WindowStaysOnTopHint)
        self.setObjectName("PopupBg")
        self.setWindowTitle("Carmen Focus — Save as an attempt")
        self.resize(480, 640)
        self._session = session
        self._on_saved = on_saved

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 20)
        layout.setSpacing(8)
        layout.addWidget(dialog_title("Save as an attempt"))
        layout.addWidget(role_label(
            f"{_minutes_text(attempt_link.seconds_of(session))} of work, filed as one more go at a problem.",
            "hint", wrap=True))

        tabs = QHBoxLayout()
        tabs.setSpacing(8)
        self._group = QButtonGroup(self)
        self._existing_tab = QPushButton("Add to existing")
        self._new_tab = QPushButton("Add new")
        for index, tab in enumerate((self._existing_tab, self._new_tab)):
            tab.setCheckable(True)
            tab.setCursor(Qt.PointingHandCursor)
            tab.setMinimumHeight(34)
            self._group.addButton(tab, index)
            tabs.addWidget(tab, 1)
        self._existing_tab.setChecked(True)
        layout.addLayout(tabs)

        self._existing = _ExistingPanel(session)
        self._new = _NewPanel(session)
        self._stack = QStackedWidget()
        self._stack.addWidget(self._existing)
        self._stack.addWidget(self._new)
        layout.addWidget(self._stack, 1)
        self._group.idClicked.connect(self._pick_tab)

        self._status = role_label("", "error", wrap=True)
        layout.addWidget(self._status)
        row = QHBoxLayout()
        row.addStretch(1)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.close)
        row.addWidget(cancel)
        self._save_button = accent(QPushButton("Save attempt"))
        self._save_button.clicked.connect(self._save)
        row.addWidget(self._save_button)
        layout.addLayout(row)

    def _pick_tab(self, index):
        self._stack.setCurrentIndex(index)
        self._status.setText("")

    def _save(self):
        panel = self._stack.currentWidget()
        problem_id, error = panel.problem_id()
        if error:
            self._status.setText(error)
            return
        outcome = panel.outcome
        problem = attempt_link.link(self._session, problem_id, self_solved=outcome.solved.isChecked(),
                                    shakiness=outcome.shaky.value())
        if problem is None:
            self._status.setText("Could not save that attempt.")
            return
        self.close()
        if self._on_saved:
            self._on_saved(problem)


def open_attempt_link(session, on_saved=None):
    dialog = AttemptLinkDialog(session, on_saved)
    _open.add(dialog)
    dialog.destroyed.connect(lambda *_: _open.discard(dialog))
    dialog.show()
    return dialog
