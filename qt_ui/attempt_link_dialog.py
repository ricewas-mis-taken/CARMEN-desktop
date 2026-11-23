"""Save a finished session as an attempt at a problem: pick an existing one, or make a new one on the spot."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QHBoxLayout, QLabel, QPushButton, QSpinBox, QVBoxLayout, QWidget,
)

import attempt_link
import review_store
from qt_ui.ui_kit import accent, dialog_title, field_label, role_label
from qt_ui.wrap_edit import WrapEdit

_open = set()          # keeps an open window alive
_NEW = "new"


def _minutes_text(seconds):
    minutes, secs = divmod(int(seconds), 60)
    return f"{minutes}:{secs:02d}"


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

        layout.addWidget(field_label("Topic"))
        self._topic_combo = QComboBox()
        layout.addWidget(self._topic_combo)
        layout.addWidget(field_label("Subject"))
        self._subject_combo = QComboBox()
        layout.addWidget(self._subject_combo)
        layout.addWidget(field_label("Problem"))
        self._problem_combo = QComboBox()
        layout.addWidget(self._problem_combo)

        self._new_box = QWidget()
        new_layout = QVBoxLayout(self._new_box)
        new_layout.setContentsMargins(0, 0, 0, 0)
        new_layout.setSpacing(8)
        new_layout.addWidget(field_label("New problem's name"))
        self._name_edit = WrapEdit("")
        new_layout.addWidget(self._name_edit)
        stars_row = QHBoxLayout()
        stars_row.addWidget(field_label("Stars"))
        self._stars_combo = QComboBox()
        for n in range(1, 6):
            self._stars_combo.addItem("★" * n, n)
        stars_row.addWidget(self._stars_combo, 1)
        new_layout.addLayout(stars_row)
        new_layout.addWidget(field_label("Notes (optional)"))
        self._notes_edit = WrapEdit("")
        new_layout.addWidget(self._notes_edit)
        layout.addWidget(self._new_box)

        self._solved_box = QCheckBox("I solved it myself")
        self._solved_box.setChecked(True)
        layout.addWidget(self._solved_box)
        shaky_row = QHBoxLayout()
        shaky_row.addWidget(QLabel("How shaky (1 solid, 5 very shaky)"))
        self._shaky = QSpinBox()
        self._shaky.setRange(1, 5)
        self._shaky.setValue(3)
        shaky_row.addWidget(self._shaky)
        shaky_row.addStretch(1)
        layout.addLayout(shaky_row)
        self._solved_box.toggled.connect(self._shaky.setEnabled)

        layout.addStretch(1)
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

        self._fill_topics()
        self._topic_combo.currentIndexChanged.connect(self._fill_subjects)
        self._subject_combo.currentIndexChanged.connect(self._fill_problems)
        self._problem_combo.currentIndexChanged.connect(self._sync_new_box)
        self._fill_subjects()

    def _fill_topics(self):
        topics = review_store.list_topics()
        for topic in topics:
            self._topic_combo.addItem(topic["name"], topic["id"])
        linked = next((i for i, t in enumerate(topics) if t.get("linkedTaskId")
                       and t["linkedTaskId"] == self._session.get("eventId")), None)
        if linked is not None:
            self._topic_combo.setCurrentIndex(linked)

    def _fill_subjects(self):
        self._subject_combo.blockSignals(True)
        self._subject_combo.clear()
        topic_id = self._topic_combo.currentData()
        for subject in (review_store.list_subjects(topic_id) if topic_id is not None else []):
            self._subject_combo.addItem(subject["name"], subject["id"])
        self._subject_combo.blockSignals(False)
        self._fill_problems()

    def _fill_problems(self):
        self._problem_combo.blockSignals(True)
        self._problem_combo.clear()
        self._problem_combo.addItem("A new problem…", _NEW)
        topic_id, subject_id = self._topic_combo.currentData(), self._subject_combo.currentData()
        if topic_id is not None and subject_id is not None:
            for problem in review_store.list_problems(topic_id, due_only=False):
                if problem.get("subjectId") == subject_id:
                    self._problem_combo.addItem(problem["name"], problem["id"])
        self._problem_combo.blockSignals(False)
        self._sync_new_box()

    def _sync_new_box(self):
        self._new_box.setVisible(self._problem_combo.currentData() == _NEW)

    def _save(self):
        subject_id = self._subject_combo.currentData()
        if subject_id is None:
            self._status.setText("Pick a subject first (add one in the Review tab if there is none).")
            return
        choice = self._problem_combo.currentData()
        if choice == _NEW:
            name = self._name_edit.text().strip()
            if not name:
                self._status.setText("Name the new problem.")
                return
            notes = self._notes_edit.text().strip() or "Added from the session history."
            try:
                created = review_store.create_problem(
                    self._topic_combo.currentData(), subject_id, name, self._stars_combo.currentData(), "text",
                    description_text=notes)
            except review_store.DuplicateNameError as exc:
                self._status.setText(f"{exc} Pick it from the Problem list instead.")
                return
            if created is None:
                self._status.setText("Could not make that problem.")
                return
            choice = created["id"]
        solved = self._solved_box.isChecked()
        problem = attempt_link.link(self._session, choice, self_solved=solved, shakiness=self._shaky.value())
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
