"""The guided tour: the window dims, one part stays bright, and a note beside it says what it is.

Chapters are short and each can be skipped, or the whole tour. Steps either spotlight a part of the window,
wait for the user to do something (create a first task), or just talk. The tour can be started again from
Options > Help, and a single chapter can be replayed on its own.

Three pieces: Tour (the state machine, no drawing), _Dim (the dimmed layer with a bright hole, a child of
the main window) and _Card (the note, a frameless window of its own so it can sit over popups too)."""
from dataclasses import dataclass, field
from typing import Callable, Optional

from PySide6.QtCore import QEvent, QObject, QPointF, QRect, QRectF, QTimer, Qt, QVariantAnimation
from PySide6.QtGui import QColor, QKeyEvent, QPainter, QPainterPath, QPen, QRegion
from PySide6.QtWidgets import QApplication, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from qt_ui import motion
from qt_ui.ui_kit import INK, MUTED, FAINT

DIM = QColor(16, 12, 38, 178)
GLOW = QColor("#A99BFF")
_CARD_WIDTH = 392
_GAP = 18
_PAD = 8


@dataclass
class Step:
    title: str
    body: str
    target: Optional[Callable[[], Optional[QWidget]]] = None   # what stays bright; None = a note in the middle
    page: Optional[str] = None                                  # the page to show first
    free: bool = False                                          # no dimming: the user is meant to click around
    wait_for: Optional[Callable[[], bool]] = None               # moves on by itself once this is true
    buttons: list = field(default_factory=list)                 # extra (label, callable) buttons on the note
    pad: int = _PAD


@dataclass
class Chapter:
    id: str
    title: str
    summary: str
    steps: list


class Tour(QObject):
    """Where the user is in the tour. The window and the note are told when it changes."""

    def __init__(self, window, chapters, on_finished=None):
        super().__init__(window)
        self.window = window
        self.chapters = list(chapters)
        self._on_finished = on_finished
        self.chapter = 0
        self.index = 0
        self.active = False
        self._poll = QTimer(self)
        self._poll.setInterval(400)
        self._poll.timeout.connect(self._check_wait)
        self.dim = _Dim(window)
        self.card = _Card(self)
        self._follow = QTimer(self)               # keeps the bright part on its target as layouts move
        self._follow.setInterval(120)
        self._follow.timeout.connect(self._place)

    # ---- state ------------------------------------------------------------------------
    @property
    def step(self):
        return self.chapters[self.chapter].steps[self.index]

    def start(self, chapter_ids=None):
        if chapter_ids:
            self.chapters = [c for c in self.chapters if c.id in chapter_ids] or self.chapters
        self.chapter, self.index, self.active = 0, 0, True
        self.dim.resize(self.window.size())
        motion.HOLD += 1                         # no page entrances under the dimmed window
        self.dim.show()
        self.dim.raise_()
        self._follow.start()
        self._poll.start()
        self._show_step()

    def next(self):
        if not self.active:
            return
        chapter = self.chapters[self.chapter]
        if self.index + 1 < len(chapter.steps):
            self.index += 1
        elif self.chapter + 1 < len(self.chapters):
            self.chapter, self.index = self.chapter + 1, 0
        else:
            return self.finish(completed=True)
        self._show_step()

    def back(self):
        if not self.active:
            return
        if self.index > 0:
            self.index -= 1
        elif self.chapter > 0:
            self.chapter -= 1
            self.index = len(self.chapters[self.chapter].steps) - 1
        self._show_step()

    def skip_chapter(self):
        """Leaves the rest of this chapter and goes to the first step of the next."""
        if not self.active:
            return
        if self.chapter + 1 < len(self.chapters):
            self.chapter, self.index = self.chapter + 1, 0
            self._show_step()
        else:
            self.finish(completed=True)

    def finish(self, completed=False):
        if not self.active:
            return
        self.active = False
        motion.HOLD = max(0, motion.HOLD - 1)
        self._follow.stop()
        self._poll.stop()
        self.dim.hide()
        self.card.hide()
        if self._on_finished:
            self._on_finished(completed)

    def position(self):
        """(step number, steps in this chapter) for the counter on the note."""
        return self.index + 1, len(self.chapters[self.chapter].steps)

    # ---- showing a step ---------------------------------------------------------------
    def _show_step(self):
        step = self.step
        if step.page and hasattr(self.window, "_show_tab"):
            self.window._show_tab(step.page)
        self.dim.set_free(step.free)
        self.card.show_step(self)
        self._place(animate=True)
        self.card.raise_()

    def _target_rect(self):
        step = self.step
        widget = step.target() if step.target else None
        if widget is None or not widget.isVisible():
            return None
        top_left = widget.mapTo(self.window, widget.rect().topLeft())
        rect = QRect(top_left, widget.size()).adjusted(-step.pad, -step.pad, step.pad, step.pad)
        return rect.intersected(self.window.rect())

    def _place(self, animate=False):
        if not self.active:
            return
        self.dim.resize(self.window.size())
        rect = self._target_rect()
        self.dim.set_hole(rect, animate)
        self.card.place_near(self.window, rect, corner=self.step.free)

    def _check_wait(self):
        wait = self.step.wait_for if self.active else None
        try:
            if wait is not None and wait():
                self.next()
        except Exception:
            pass


class _Dim(QWidget):
    """The dimmed window with one bright hole. It blocks clicks everywhere except in the hole, so the part
    being explained is the only thing that can be used; a free step lets everything through."""

    def __init__(self, window):
        super().__init__(window)
        self.setAttribute(Qt.WA_NoSystemBackground, True)
        self.setMouseTracking(True)
        self._hole = None
        self._shown = None
        self._free = False
        self._anim = QVariantAnimation(self)
        self._anim.setDuration(240)
        self._anim.valueChanged.connect(self._tween)
        self.hide()
        window.installEventFilter(self)

    def eventFilter(self, obj, event):
        if event.type() in (QEvent.Resize, QEvent.Move):
            self.resize(obj.size())
            self._apply_mask()
        return False

    def _apply_mask(self):
        """The hole is cut out of the layer itself, so clicks in it reach the part underneath."""
        if self._free or self._hole is None:
            self.clearMask()
        else:
            self.setMask(QRegion(self.rect()).subtracted(QRegion(self._hole)))

    def set_free(self, free):
        self._free = free
        self.setAttribute(Qt.WA_TransparentForMouseEvents, free)
        self._apply_mask()
        self.update()

    def set_hole(self, rect, animate):
        if rect == self._hole:
            return
        self._hole = rect
        self._apply_mask()
        if animate and rect is not None and self._shown is not None and not self._free:
            self._anim.stop()
            self._anim.setStartValue(QRectF(self._shown))
            self._anim.setEndValue(QRectF(rect))
            self._anim.start()
        else:
            self._anim.stop()
            self._shown = QRectF(rect) if rect is not None else None
            self.update()

    def _tween(self, value):
        self._shown = value
        self.update()

    def mousePressEvent(self, event):
        event.accept()               # everything outside the bright part is out of reach

    def mouseReleaseEvent(self, event):
        event.accept()

    def paintEvent(self, _event):
        if self._free:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        whole = QPainterPath()
        whole.addRect(QRectF(self.rect()))
        hole = self._shown
        if hole is not None:
            cut = QPainterPath()
            cut.addRoundedRect(hole, 14, 14)
            whole = whole.subtracted(cut)
        p.fillPath(whole, DIM)
        if hole is not None:
            for grow, alpha in ((6, 28), (3, 60)):             # a soft light round the bright part
                glow = QColor(GLOW)
                glow.setAlpha(alpha)
                p.setPen(QPen(glow, 2))
                p.setBrush(Qt.NoBrush)
                p.drawRoundedRect(hole.adjusted(-grow, -grow, grow, grow), 14 + grow, 14 + grow)
            p.setPen(QPen(GLOW, 2))
            p.drawRoundedRect(hole, 14, 14)


class _Card(QWidget):
    """The note: which chapter, the title and words, and the buttons. Frameless, always on top."""

    def __init__(self, tour):
        super().__init__(None, Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.setObjectName("TourCard")
        self.setAttribute(Qt.WA_TranslucentBackground, True)     # so the rounded corners are really round
        self._tour = tour
        self.setFixedWidth(_CARD_WIDTH)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 18, 22, 16)
        layout.setSpacing(10)

        self._progress = _Segments()
        layout.addWidget(self._progress)
        self._chapter = QLabel("")
        self._chapter.setStyleSheet(f"color:{MUTED};font-size:12.5px;font-weight:600;background:transparent;border:none;")
        layout.addWidget(self._chapter)
        self._title = QLabel("")
        self._title.setWordWrap(True)
        self._title.setStyleSheet(f"color:{INK};font-size:20px;font-weight:800;background:transparent;border:none;")
        layout.addWidget(self._title)
        self._body = QLabel("")
        self._body.setWordWrap(True)
        self._body.setTextFormat(Qt.RichText)
        self._body.setOpenExternalLinks(True)
        self._body.setStyleSheet(f"color:{INK};font-size:14px;background:transparent;border:none;")
        layout.addWidget(self._body)
        self._extras = QHBoxLayout()
        self._extras.setSpacing(8)
        layout.addLayout(self._extras)

        row = QHBoxLayout()
        row.setSpacing(6)
        self._count = QLabel("")
        self._count.setStyleSheet(f"color:{FAINT};font-size:12.5px;background:transparent;border:none;")
        row.addWidget(self._count)
        row.addStretch(1)
        self._back = self._flat("Back", tour.back)
        row.addWidget(self._back)
        self._next = QPushButton("Next")
        self._next.setCursor(Qt.PointingHandCursor)
        self._next.setStyleSheet(
            f"QPushButton{{background:{INK};color:#FFFFFF;border:none;border-radius:11px;padding:9px 20px;"
            "font-size:14px;font-weight:700;}QPushButton:hover{background:#3B3170;}")
        self._next.clicked.connect(tour.next)
        row.addWidget(self._next)
        layout.addLayout(row)

        skips = QHBoxLayout()
        skips.setSpacing(14)
        self._skip_part = self._flat("Skip this part", tour.skip_chapter, muted=True)
        skips.addWidget(self._skip_part)
        self._skip_all = self._flat("Skip the tour", lambda: tour.finish(completed=False), muted=True)
        skips.addWidget(self._skip_all)
        skips.addStretch(1)
        layout.addLayout(skips)

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(QPen(QColor("#D9D3FF"), 1))
        p.setBrush(QColor("#FFFFFF"))
        p.drawRoundedRect(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), 18, 18)

    def _flat(self, text, handler, muted=False):
        button = QPushButton(text)
        button.setCursor(Qt.PointingHandCursor)
        colour = FAINT if muted else INK
        button.setStyleSheet(
            f"QPushButton{{background:transparent;color:{colour};border:none;padding:6px 8px;font-size:13px;"
            "font-weight:600;}QPushButton:hover{text-decoration:underline;}")
        button.clicked.connect(handler)
        return button

    def show_step(self, tour):
        step, chapter = tour.step, tour.chapters[tour.chapter]
        self._progress.set_state(len(tour.chapters), tour.chapter)
        self._chapter.setText(chapter.title)
        self._title.setText(step.title)
        self._body.setText(step.body)
        number, total = tour.position()
        self._count.setText(f"{number} of {total}")
        self._back.setVisible(not (tour.chapter == 0 and tour.index == 0))
        last = tour.chapter == len(tour.chapters) - 1 and tour.index == total - 1
        self._next.setText("Finish" if last else "Next")
        self._skip_part.setVisible(not last)
        self._skip_all.setVisible(not last)
        while self._extras.count():
            item = self._extras.takeAt(0)
            if item.widget() is not None:
                item.widget().hide()
                item.widget().deleteLater()
        for label, handler in step.buttons:
            extra = QPushButton(label)
            extra.setCursor(Qt.PointingHandCursor)
            extra.setStyleSheet(
                "QPushButton{background:#EFEBFF;color:#241B55;border:none;border-radius:10px;padding:7px 14px;"
                "font-size:13px;font-weight:600;}QPushButton:hover{background:#E2DBFF;}")
            extra.clicked.connect(handler)
            self._extras.addWidget(extra)
        self._extras.addStretch(1)
        self._fit()
        self.show()

    def _fit(self):
        """As tall as this step needs, not as tall as the biggest one so far."""
        layout = self.layout()
        layout.activate()
        self.resize(_CARD_WIDTH, layout.totalHeightForWidth(_CARD_WIDTH))

    def place_near(self, window, hole, corner=False):
        """Beside the bright part where there is room (right, left, below, above), else in the middle.
        While the user is working in something (corner), the note tucks into the window's lower right."""
        self._fit()
        frame = QRect(window.mapToGlobal(window.rect().topLeft()), window.size())
        w, h = self.width(), self.height()
        if corner:
            x, y = frame.right() - w - 24, frame.bottom() - h - 24
        elif hole is None:
            x, y = frame.center().x() - w // 2, frame.center().y() - h // 2
        else:
            g = QRect(window.mapToGlobal(hole.topLeft()), hole.size())
            options = (
                (g.right() + _GAP, g.center().y() - h // 2, frame.right() - g.right() - _GAP - w),
                (g.left() - _GAP - w, g.center().y() - h // 2, g.left() - _GAP - w - frame.left()),
                (g.center().x() - w // 2, g.bottom() + _GAP, frame.bottom() - g.bottom() - _GAP - h),
                (g.center().x() - w // 2, g.top() - _GAP - h, g.top() - _GAP - h - frame.top()),
            )
            x, y, room = max(options, key=lambda o: o[2])
            if room < 0:                                   # nothing fits beside it: float over the middle
                x, y = frame.center().x() - w // 2, frame.bottom() - h - 28
        screen = (self.screen() or QApplication.primaryScreen()).availableGeometry()
        x = max(screen.left() + 8, min(x, screen.right() - w - 8))
        y = max(screen.top() + 8, min(y, screen.bottom() - h - 8))
        self.move(int(x), int(y))

    def keyPressEvent(self, event: QKeyEvent):
        key = event.key()
        if key in (Qt.Key_Right, Qt.Key_Return, Qt.Key_Enter):
            self._tour.next()
        elif key == Qt.Key_Left:
            self._tour.back()
        elif key == Qt.Key_Escape:
            self._tour.finish(completed=False)
        else:
            super().keyPressEvent(event)


class _Segments(QWidget):
    """One bar per chapter: finished ones solid, the current one lit, the rest quiet."""

    def __init__(self):
        super().__init__()
        self._total, self._current = 1, 0
        self.setFixedHeight(5)

    def set_state(self, total, current):
        self._total, self._current = total, current
        self.update()

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        n = max(1, self._total)
        gap = 3.0
        width = (self.width() - gap * (n - 1)) / n
        for i in range(n):
            colour = QColor(INK) if i < self._current else QColor("#8B7BE8") if i == self._current else QColor("#E4E0F7")
            p.setBrush(colour)
            p.drawRoundedRect(QRectF(i * (width + gap), 0, width, 5), 2.5, 2.5)
