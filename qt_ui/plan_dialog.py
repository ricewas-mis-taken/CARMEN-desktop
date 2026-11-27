"""Plan tomorrow: a short checklist. Write what you want to get done, one line each. Carmen guesses what each line is
(a task with minutes, a board item, or a quick note) and tags it; tap the little label to change the guess, or
right-click a line to change it or report a wrong guess. Save files everything where it belongs."""
from datetime import date, timedelta

from PySide6.QtCore import QPoint, Qt, QTimer
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QInputDialog, QLabel, QMenu, QPushButton, QScrollArea, QVBoxLayout, QWidget,
)

import plan_ml
import plan_store
from qt_ui.ui_kit import FAINT, INK, MUTED, accent, dialog_title, role_label
from qt_ui.wrap_edit import WrapEdit

_open = set()          # keeps an open window alive

KIND_COLORS = {"task": ("#D5F5F3", "#0E7C75"), "board": ("#EDE4FB", "#6B46C1"), "note": ("#FEF0C7", "#93370D")}
KIND_NAMES = {"task": "Task", "board": "Board item", "note": "Quick note"}
MINUTE_CHOICES = (15, 25, 30, 45, 60, 90, 120, 180)
TAG_CHOICES = tuple(plan_ml.TAG_KEYWORDS)
CLASSIFY_DELAY_MS = 280


def _minutes_text(minutes):
    return f"{minutes} min" if minutes < 60 else (f"{minutes // 60} h" if minutes % 60 == 0 else f"{minutes // 60} h {minutes % 60} min")


class _PlanRow(QWidget):
    """One line of the plan: the text, the guessed kind, minutes and tags."""

    def __init__(self, text="", on_change=None, on_enter=None, on_remove=None, kind=None, minutes=None, tags=None):
        super().__init__()
        self._on_change, self._on_enter, self._on_remove = on_change, on_enter, on_remove
        self.guess = None
        self._kind = kind              # None: follow the guess; a value: the person chose it
        self._minutes = minutes
        self._tags = None if tags is None else set(tags)
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 2, 0, 2)
        row.setSpacing(10)
        bullet = QLabel("○")
        bullet.setStyleSheet(f"color:{FAINT};font-size:17px;background:transparent;")
        bullet.setFixedWidth(18)
        row.addWidget(bullet, 0, Qt.AlignTop)
        self.edit = WrapEdit(text, max_rows=2, boxed=False)
        self.edit.setPlaceholderText("What do you want to get done?")
        self.edit.setStyleSheet(
            f"QPlainTextEdit#WrapEdit{{background:transparent;color:{INK};border:none;"
            f"border-bottom:1px solid #D9DEE7;border-radius:0;padding:3px 2px;font-size:15px;}}"
            "QPlainTextEdit#WrapEdit:focus{border-bottom:2px solid #5B8DEF;border-radius:0;}")
        self.edit.returnPressed.connect(lambda: self._on_enter and self._on_enter(self))
        self.edit.textChanged.connect(self._schedule)
        row.addWidget(self.edit, 1)
        self.chip = QPushButton("")
        self.chip.setCursor(Qt.PointingHandCursor)
        self.chip.setFixedHeight(28)
        self.chip.clicked.connect(self._open_menu)
        self.chip.hide()
        row.addWidget(self.chip, 0, Qt.AlignTop)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.reclassify)
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(lambda pos: self._open_menu(self.mapToGlobal(pos)))
        self.edit.setContextMenuPolicy(Qt.CustomContextMenu)
        self.edit.customContextMenuRequested.connect(lambda pos: self._open_menu(self.edit.mapToGlobal(pos)))
        if text:
            self.reclassify()

    # --- the guess -------------------------------------------------------------------------------------------

    def text(self):
        return self.edit.text().strip()

    def _schedule(self):
        self._timer.start(CLASSIFY_DELAY_MS)

    def reclassify(self):
        text = self.text()
        self.guess = plan_ml.predict(text) if text else None
        self._paint()
        if self._on_change:
            self._on_change()

    def kind(self):
        return self._kind or (self.guess["label"] if self.guess else None)

    def minutes(self):
        if self.kind() != "task":
            return None
        if self._minutes:
            return self._minutes
        return (self.guess or {}).get("minutes") or plan_ml.DEFAULT_MINUTES

    def tags(self):
        return sorted(self._tags if self._tags is not None else (self.guess or {}).get("tags") or [])

    def sure(self):
        """True when the guess needs no second look: the person chose it, or it is confident."""
        return self._kind is not None or bool(self.guess and self.guess["auto"])

    def _paint(self):
        kind = self.kind()
        if kind is None:
            self.chip.hide()
            return
        fill, ink = KIND_COLORS[kind]
        label = KIND_NAMES[kind]
        if kind == "task":
            label += f" · {_minutes_text(self.minutes())}"
        if self.tags():
            label += "  " + " ".join("#" + t for t in self.tags()[:2])
        if not self.sure():
            label += "  ?"
        border = f"1px dashed {ink}" if not self.sure() else "1px solid transparent"
        self.chip.setText(label)
        self.chip.setToolTip("Tap to change. Right-click the line to report a wrong guess." if self.sure()
                             else "Not sure about this one. Tap to check or change it.")
        self.chip.setStyleSheet(
            f"QPushButton{{background:{fill};color:{ink};border:{border};border-radius:14px;padding:0 12px;"
            f"font-size:12.5px;font-weight:600;}}QPushButton:hover{{border:1px solid {ink};}}")
        self.chip.show()

    # --- changing the guess ----------------------------------------------------------------------------------

    def _open_menu(self, position=None):
        if not self.text():
            return
        menu = QMenu(self)
        for kind in plan_ml.CLASSES:
            action = menu.addAction(("✓  " if self.kind() == kind else "     ") + KIND_NAMES[kind])
            action.triggered.connect(lambda _=False, k=kind: self.set_kind(k))
        if self.kind() == "task":
            menu.addSeparator()
            times = menu.addMenu("How long")
            for minutes in MINUTE_CHOICES:
                action = times.addAction(("✓  " if self.minutes() == minutes else "     ") + _minutes_text(minutes))
                action.triggered.connect(lambda _=False, m=minutes: self.set_minutes(m))
        tags = menu.addMenu("Tags")
        for tag in TAG_CHOICES:
            action = tags.addAction(("✓  " if tag in self.tags() else "     ") + tag.capitalize())
            action.triggered.connect(lambda _=False, t=tag: self.toggle_tag(t))
        menu.addSeparator()
        report = menu.addAction("Report a wrong guess…")
        report.triggered.connect(self.report_wrong_guess)
        menu.addSeparator()
        remove = menu.addAction("Remove this line")
        remove.triggered.connect(lambda: self._on_remove and self._on_remove(self))
        where = position if isinstance(position, QPoint) else self.chip.mapToGlobal(QPoint(0, self.chip.height()))
        menu.exec(where)

    def set_kind(self, kind):
        self._kind = kind
        self._paint()
        self._notify()

    def set_minutes(self, minutes):
        self._kind = self._kind or "task"
        self._minutes = minutes
        self._paint()
        self._notify()

    def toggle_tag(self, tag):
        current = set(self.tags())
        current.symmetric_difference_update({tag})
        self._tags = current
        self._paint()
        self._notify()

    def _notify(self):
        if self._on_change:
            self._on_change()

    def report_wrong_guess(self):
        """Keeps the guess and what it should have been, and teaches the model the right answer at once."""
        text = self.text()
        if not text or self.guess is None:
            return
        note, ok = QInputDialog.getText(self, "Report a wrong guess",
                                        "Anything we should know? (optional)")
        if not ok:
            return
        wanted = self._kind or self.guess["label"]
        plan_ml.report(text, self.guess["label"], wanted, note)
        self.reclassify()

    def entry(self):
        return {"text": self.text(), "kind": self.kind(), "minutes": self.minutes(), "tags": self.tags(),
                "predicted": {"label": (self.guess or {}).get("label"), "tags": (self.guess or {}).get("tags") or []}}


class PlanDialog(QWidget):
    def __init__(self, day=None, on_saved=None):
        super().__init__(None, Qt.WindowStaysOnTopHint)
        self.setObjectName("PopupBg")
        self.setWindowTitle("Carmen Focus — Plan")
        self.resize(540, 640)
        self._day = day or date.today() + timedelta(days=1)
        self._on_saved = on_saved
        self._rows = []
        self._summary = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(26, 24, 26, 20)
        layout.setSpacing(10)
        today = date.today()
        name = ("Today" if self._day == today else "Tomorrow" if self._day == today + timedelta(days=1)
                else self._day.strftime("%A"))
        layout.addWidget(dialog_title(f"Plan {name.lower()}" if name != "Today" else "Plan today"))
        layout.addWidget(role_label(
            "One line each. Carmen sorts them into tasks, board items and quick notes.", "hint", wrap=True))

        self._carry = QHBoxLayout()
        self._carry.setSpacing(8)
        carry_holder = QWidget()
        carry_holder.setLayout(self._carry)
        layout.addWidget(carry_holder)
        self._fill_carry_over()

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setStyleSheet("QScrollArea{background:transparent;border:none;}")
        body = QWidget()
        body.setStyleSheet("background:transparent;")
        self._rows_layout = QVBoxLayout(body)
        self._rows_layout.setContentsMargins(0, 4, 6, 4)
        self._rows_layout.setSpacing(4)
        self._rows_layout.addStretch(1)
        scroll.setWidget(body)
        layout.addWidget(scroll, 1)
        self._scroll = scroll

        existing = plan_store.load_day(self._day)["items"]
        for item in existing:
            text = item["text"] + (f" {item['minutes']} min" if item["kind"] == "task" and item.get("minutes") else "")
            self.add_row(text, kind=item["kind"], minutes=item.get("minutes"), tags=item.get("tags"))
        self.add_row(focus=True)

        self._summary = role_label("", "hint", wrap=True)
        layout.addWidget(self._summary)
        self._status = role_label("", "error", wrap=True)
        layout.addWidget(self._status)
        row = QHBoxLayout()
        points = QLabel(f"Planning earns +{plan_store.PLAN_MADE_POINTS}. Doing it earns up to "
                        f"{plan_store.SWING} more.")
        points.setStyleSheet(f"color:{MUTED};font-size:12.5px;background:transparent;")
        row.addWidget(points)
        row.addStretch(1)
        cancel = QPushButton("Not now")
        cancel.clicked.connect(self.close)
        row.addWidget(cancel)
        self._save_button = accent(QPushButton("Save plan"))
        self._save_button.clicked.connect(self.save)
        row.addWidget(self._save_button)
        layout.addLayout(row)
        self._update_summary()

    def _fill_carry_over(self):
        before = plan_store.unfinished(self._day - timedelta(days=1))
        before = [i for i in before if i["kind"] == "task"][:4]
        if not before:
            return
        caption = QLabel("Not done yesterday:")
        caption.setStyleSheet(f"color:{MUTED};font-size:12.5px;background:transparent;")
        self._carry.addWidget(caption)
        for item in before:
            button = QPushButton("+ " + item["text"])
            button.setCursor(Qt.PointingHandCursor)
            button.setStyleSheet(
                f"QPushButton{{background:#F3F5F9;color:{INK};border:1px solid #E3E6EC;border-radius:13px;"
                "padding:3px 11px;font-size:12.5px;}QPushButton:hover{background:#E9EDF5;}")
            button.clicked.connect(lambda _=False, i=item, b=button: self._bring_over(i, b))
            self._carry.addWidget(button)
        self._carry.addStretch(1)

    def _bring_over(self, item, button):
        button.setEnabled(False)
        last = self._rows[-1] if self._rows else None
        if last is not None and not last.text():
            self._remove(last)
        self.add_row(f"{item['text']} {item['minutes']} min" if item.get("minutes") else item["text"],
                     kind="task", minutes=item.get("minutes"), tags=item.get("tags"))
        self.add_row(focus=True)

    def add_row(self, text="", focus=False, kind=None, minutes=None, tags=None, after=None):
        row = _PlanRow(text, on_change=self._update_summary, on_enter=self._on_enter, on_remove=self._remove,
                       kind=kind, minutes=minutes, tags=tags)
        index = self._rows.index(after) + 1 if after in self._rows else len(self._rows)
        self._rows.insert(index, row)
        self._rows_layout.insertWidget(index, row)
        if focus:
            QTimer.singleShot(0, row.edit.setFocus)
        return row

    def _on_enter(self, row):
        if not row.text():
            return
        index = self._rows.index(row)
        following = self._rows[index + 1] if index + 1 < len(self._rows) else None
        if following is None or following.text():
            following = self.add_row(after=row)
        following.edit.setFocus()

    def _remove(self, row):
        if row not in self._rows:
            return
        self._rows.remove(row)
        row.hide()
        row.deleteLater()
        if not self._rows:
            self.add_row(focus=True)
        self._update_summary()

    def entries(self):
        return [r.entry() for r in self._rows if r.text() and r.kind()]

    def _update_summary(self):
        if self._summary is None:
            return
        entries = self.entries()
        minutes = sum(e["minutes"] or 0 for e in entries if e["kind"] == "task")
        tasks = sum(1 for e in entries if e["kind"] == "task")
        parts = []
        if tasks:
            parts.append(f"{tasks} task{'s' if tasks != 1 else ''}, {_minutes_text(minutes)} in all")
        for kind, word in (("board", "board item"), ("note", "quick note")):
            n = sum(1 for e in entries if e["kind"] == kind)
            if n:
                parts.append(f"{n} {word}{'s' if n != 1 else ''}")
        self._summary.setText(" · ".join(parts))

    def save(self):
        entries = self.entries()
        if not entries:
            self._status.setText("Add at least one line.")
            return
        items = plan_store.save_plan(self._day, entries)
        self.close()
        if self._on_saved:
            self._on_saved(items)


def open_plan(day=None, on_saved=None):
    """Opens the plan popup (for tomorrow unless a day is given). One at a time: a second call brings it forward."""
    for window in list(_open):
        try:
            window.raise_()
            window.activateWindow()
            return window
        except RuntimeError:
            _open.discard(window)
    dialog = PlanDialog(day, on_saved)
    _open.add(dialog)
    dialog.destroyed.connect(lambda *_: _open.discard(dialog))
    dialog.show()
    return dialog
