"""Today: the plan you made last night, as a day that flows.

Tasks run down a rail one after another from now, each as long as its minutes, with a short breather between and
the free time you have left shown as a gap. The rail ends at the time you usually stop; a task that runs past it
hangs below the line, tinted, so an over-full day is visible before it is lived. Board items and quick notes the
plan made sit underneath, ticked off the same way. Right-click any line to change what Carmen thought it was,
change its length, or report a wrong guess."""
from datetime import date, datetime, timedelta

from PySide6.QtCore import QPointF, Qt, QTimer
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import (
    QButtonGroup, QFrame, QHBoxLayout, QInputDialog, QLabel, QMenu, QPushButton, QScrollArea, QVBoxLayout, QWidget,
)

import board_store
import config
import plan_ml
import plan_store
import shutdown_model
import today_flow
from qt_ui.ui_kit import FAINT, GREY, INK, MUTED, NUM_CSS, ACCENT, accent

REFRESH_MS = 30000
MINUTE_CHOICES = (15, 25, 30, 45, 60, 90, 120)
KIND_NAMES = {"task": "Task", "board": "Board item", "note": "Quick note"}
PX_PER_MINUTE = 0.7
MIN_BLOCK_PX, MAX_BLOCK_PX = 62, 130
OVER_FILL, OVER_INK = "#FDECEA", "#B42318"


def _minute_of(stamp):
    try:
        moment = datetime.fromisoformat(stamp)
    except (TypeError, ValueError):
        return None
    return moment.hour * 60 + moment.minute


def _tag_colors(tags):
    for tag in tags or []:
        preset = board_store.PRESET_TAGS_BY_ID.get(tag)
        if preset:
            return preset["bg"], preset["color"]
    return GREY


class _Rail(QWidget):
    """The thin line down the left of the timeline, with a dot where a block starts."""

    def __init__(self, color, dot=True, dashed=False, top_gap=18):
        super().__init__()
        self._color, self._dot, self._dashed, self._top = QColor(color), dot, dashed, top_gap
        self.setFixedWidth(22)

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        x = self.width() / 2
        pen = QPen(QColor("#D5DAE3"), 2)
        if self._dashed:
            pen.setStyle(Qt.DashLine)
        painter.setPen(pen)
        painter.drawLine(QPointF(x, 0), QPointF(x, self.height()))
        if self._dot:
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor("#FFFFFF"))
            painter.drawEllipse(QPointF(x, self._top), 7, 7)
            painter.setBrush(self._color)
            painter.drawEllipse(QPointF(x, self._top), 5, 5)


def _tag_chip(tag):
    preset = board_store.PRESET_TAGS_BY_ID.get(tag)
    fill, ink = (preset["bg"], preset["color"]) if preset else GREY
    label = QLabel(preset["label"] if preset else tag.capitalize())
    label.setStyleSheet(f"background:{fill};color:{ink};border-radius:9px;padding:1px 8px;font-size:11.5px;"
                        "font-weight:600;")
    return label


class _Block(QFrame):
    """One task on the rail."""

    def __init__(self, entry, today_view, over, on_toggle, on_start, on_menu):
        super().__init__()
        item = entry["item"]
        fill, ink = _tag_colors(item.get("tags"))
        if over and not item["done"]:
            fill, ink = OVER_FILL, OVER_INK
        if item["done"]:
            fill, ink = "#F1F3F7", "#8A93A3"
        minutes = int(item["minutes"])
        self.setMinimumHeight(int(max(MIN_BLOCK_PX, min(MAX_BLOCK_PX, minutes * PX_PER_MINUTE))))
        self.setObjectName("TodayBlock")
        self.setStyleSheet(
            f"QFrame#TodayBlock{{background:{fill};border:none;border-left:5px solid {ink};border-radius:14px;}}"
            )
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(lambda pos: on_menu(item, self.mapToGlobal(pos)))
        row = QHBoxLayout(self)
        row.setContentsMargins(14, 10, 12, 10)
        row.setSpacing(12)
        from qt_ui.quicknote import _Tick
        tick = _Tick()
        tick.setFixedSize(22, 22)
        tick.setChecked(item["done"])
        tick.toggled.connect(lambda on: on_toggle(item, on))
        row.addWidget(tick, 0, Qt.AlignTop)
        column = QVBoxLayout()
        column.setSpacing(3)
        title = QLabel(item["text"])
        title.setWordWrap(True)
        font = title.font()
        font.setStrikeOut(bool(item["done"]))
        title.setFont(font)
        title.setStyleSheet(f"color:{INK};font-size:15px;font-weight:600;background:transparent;")
        column.addWidget(title)
        span = f"{today_flow.clock_text(entry['start'])} to {today_flow.clock_text(entry['end'])}"
        timing = QLabel(f"{span}  ·  {today_flow.length_text(minutes)}" + ("  ·  pinned" if entry["pinned"] else ""))
        timing.setStyleSheet(f"color:{ink};font-size:12.5px;font-weight:600;background:transparent;")
        column.addWidget(timing)
        if over and not item["done"]:
            warn = QLabel("Past the time you usually stop")
            warn.setStyleSheet(f"color:{OVER_INK};font-size:12px;background:transparent;")
            column.addWidget(warn)
        if item.get("tags"):
            chips = QHBoxLayout()
            chips.setSpacing(6)
            for tag in item["tags"][:3]:
                chips.addWidget(_tag_chip(tag))
            chips.addStretch(1)
            column.addLayout(chips)
        column.addStretch(1)
        row.addLayout(column, 1)
        if today_view and not item["done"]:
            start = QPushButton("Start")
            start.setCursor(Qt.PointingHandCursor)
            start.setFixedHeight(30)
            start.setStyleSheet(
                f"QPushButton{{background:{INK};color:#FFFFFF;border:none;border-radius:15px;padding:0 16px;"
                "font-size:13px;font-weight:600;}QPushButton:hover{background:#2F353D;}")
            start.clicked.connect(lambda: on_start(item))
            row.addWidget(start, 0, Qt.AlignTop)


class _SmallRow(QFrame):
    """A board item or quick note planned for the day."""

    def __init__(self, item, on_toggle, on_menu):
        super().__init__()
        self.setObjectName("TodaySmall")
        self.setStyleSheet("QFrame#TodaySmall{background:#FFFFFF;border:1px solid #E3E6EC;border-radius:12px;}")
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(lambda pos: on_menu(item, self.mapToGlobal(pos)))
        row = QHBoxLayout(self)
        row.setContentsMargins(12, 9, 12, 9)
        row.setSpacing(10)
        from qt_ui.quicknote import _Tick
        tick = _Tick()
        tick.setFixedSize(20, 20)
        tick.setChecked(item["done"])
        tick.toggled.connect(lambda on: on_toggle(item, on))
        row.addWidget(tick)
        label = QLabel(item["text"])
        label.setWordWrap(True)
        font = label.font()
        font.setStrikeOut(bool(item["done"]))
        label.setFont(font)
        label.setStyleSheet(f"color:{FAINT if item['done'] else INK};font-size:14.5px;background:transparent;")
        row.addWidget(label, 1)
        for tag in (item.get("tags") or [])[:2]:
            row.addWidget(_tag_chip(tag))
        kind = QLabel(KIND_NAMES[item["kind"]])
        kind.setStyleSheet(f"color:{MUTED};font-size:12px;background:transparent;")
        row.addWidget(kind)


class TodayTab(QWidget):
    def __init__(self):
        super().__init__()
        self._view = date.today()
        self._signature = None
        outer = QVBoxLayout(self)
        outer.setContentsMargins(28, 24, 28, 20)
        outer.setSpacing(12)

        head = QHBoxLayout()
        head.setSpacing(12)
        titles = QVBoxLayout()
        titles.setSpacing(2)
        self._title = QLabel("")
        self._title.setStyleSheet(f"font-size:26px;font-weight:700;color:{INK};background:transparent;")
        titles.addWidget(self._title)
        self._sub = QLabel("")
        self._sub.setStyleSheet(f"font-size:13.5px;color:{MUTED};background:transparent;")
        titles.addWidget(self._sub)
        head.addLayout(titles, 1)
        self._day_buttons = QButtonGroup(self)
        for index, label in enumerate(("Today", "Tomorrow")):
            button = QPushButton(label)
            button.setCheckable(True)
            button.setCursor(Qt.PointingHandCursor)
            button.setFixedHeight(32)
            button.setStyleSheet(
                f"QPushButton{{background:#F3F5F9;color:{MUTED};border:none;border-radius:16px;padding:0 16px;"
                "font-weight:600;font-size:13px;}"
                f"QPushButton:checked{{background:{INK};color:#FFFFFF;}}")
            self._day_buttons.addButton(button, index)
            head.addWidget(button, 0, Qt.AlignTop)
        self._day_buttons.button(0).setChecked(True)
        self._day_buttons.idClicked.connect(self._pick_day)
        self._plan_button = accent(QPushButton("Plan"))
        self._plan_button.setCursor(Qt.PointingHandCursor)
        self._plan_button.clicked.connect(self._open_plan)
        head.addWidget(self._plan_button, 0, Qt.AlignTop)
        outer.addLayout(head)

        self._bar = QFrame()
        self._bar.setFixedHeight(6)
        outer.addWidget(self._bar)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setStyleSheet("QScrollArea{background:transparent;border:none;}")
        self._content = QWidget()
        self._content.setStyleSheet("background:transparent;")
        self._column = QVBoxLayout(self._content)
        self._column.setContentsMargins(0, 6, 8, 18)
        self._column.setSpacing(0)
        scroll.setWidget(self._content)
        outer.addWidget(scroll, 1)
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(REFRESH_MS)
        self.refresh()

    # --- state -----------------------------------------------------------------------------------------------

    def showEvent(self, event):
        self.refresh()
        super().showEvent(event)

    def _tick(self):
        if self.isVisible():
            self.refresh()

    def _pick_day(self, index):
        self._view = date.today() + timedelta(days=index)
        self.refresh(force=True)

    def _is_today(self):
        return self._view == date.today()

    def _open_plan(self):
        from qt_ui import plan_dialog
        plan_dialog.open_plan(self._view, on_saved=lambda _items: self.refresh(force=True))

    def _stop_minutes(self, _now):
        return shutdown_model.usual_stop_minutes(self._view)

    def _state_signature(self, loaded, now):
        stamp = (now.hour, now.minute // 5) if self._is_today() else None
        return (self._view, stamp, tuple((i["id"], i["done"], i.get("minutes"), i.get("start"), i["kind"], tuple(i["tags"]))
                                         for i in loaded["items"]))

    # --- drawing ---------------------------------------------------------------------------------------------

    def refresh(self, force=False):
        now = datetime.now()
        loaded = plan_store.load_day(self._view)
        signature = self._state_signature(loaded, now)
        if not force and signature == self._signature:
            return
        self._signature = signature
        items = [dict(i, doneMinute=_minute_of(i.get("doneAt"))) for i in loaded["items"]]
        self._set_header(items, now)
        while self._column.count():
            child = self._column.takeAt(0)
            widget = child.widget()
            if widget is not None:
                widget.hide()
                widget.deleteLater()
        if not items:
            self._column.addWidget(self._empty_state())
            self._column.addStretch(1)
            return
        self._draw_timeline(items, now)
        self._draw_small(items)
        self._column.addStretch(1)

    def _set_header(self, items, now):
        name = "Today" if self._is_today() else "Tomorrow"
        self._title.setText(f"{name}, {self._view.strftime('%A %d').replace(' 0', ' ')}")
        done, total = plan_store.progress(self._view)
        if not items:
            self._sub.setText("Nothing planned yet.")
        else:
            self._sub.setText(f"{done} of {total} done" if self._is_today() else f"{total} planned")
        self._plan_button.setText("Edit plan" if items else "Plan this day")
        share = (done / total) if total else 0
        self._bar.setStyleSheet(
            "QFrame{background:#E9EDF5;border-radius:3px;border:none;}")
        fill = self._bar.findChild(QFrame, "TodayFill") or QFrame(self._bar)
        fill.setObjectName("TodayFill")
        fill.setStyleSheet(f"background:{ACCENT};border-radius:3px;border:none;")
        fill.setGeometry(0, 0, int(max(0, self._bar.width()) * share), 6)
        fill.show()
        self._share = share

    def resizeEvent(self, event):
        super().resizeEvent(event)
        fill = self._bar.findChild(QFrame, "TodayFill")
        if fill is not None:
            fill.setGeometry(0, 0, int(self._bar.width() * getattr(self, "_share", 0)), 6)

    def _empty_state(self):
        box = QFrame()
        box.setStyleSheet("QFrame{background:#F7F8FB;border:1px dashed #D5DAE3;border-radius:16px;}"
                          "QLabel{background:transparent;border:none;}")
        layout = QVBoxLayout(box)
        layout.setContentsMargins(28, 30, 28, 30)
        layout.setSpacing(10)
        title = QLabel("Nothing planned for this day." if not self._is_today() else "No plan for today.")
        title.setStyleSheet(f"font-size:18px;font-weight:700;color:{INK};")
        layout.addWidget(title)
        text = QLabel("Write a few lines about what you want to get done. Carmen sorts them into tasks, board items "
                      "and quick notes, and lays the tasks out as a day. It will also ask you each night, a little "
                      "before you usually stop.")
        text.setWordWrap(True)
        text.setStyleSheet(f"font-size:13.5px;color:{MUTED};")
        layout.addWidget(text)
        button = accent(QPushButton("Plan this day"))
        button.setCursor(Qt.PointingHandCursor)
        button.clicked.connect(self._open_plan)
        layout.addWidget(button, 0, Qt.AlignLeft)
        return box

    def _row(self, time_text, rail, body, time_color=MUTED):
        holder = QWidget()
        holder.setStyleSheet("background:transparent;")
        line = QHBoxLayout(holder)
        line.setContentsMargins(0, 0, 0, 0)
        line.setSpacing(10)
        label = QLabel(time_text)
        label.setFixedWidth(66)
        label.setAlignment(Qt.AlignRight | Qt.AlignTop)
        label.setContentsMargins(0, 11, 0, 0)
        label.setStyleSheet(f"font-size:12.5px;{NUM_CSS}color:{time_color};background:transparent;")
        line.addWidget(label)
        line.addWidget(rail)
        pad = QVBoxLayout()
        pad.setContentsMargins(0, 4, 0, 4)
        pad.addWidget(body)
        line.addLayout(pad, 1)
        self._column.addWidget(holder)

    def _note_line(self, text, color=MUTED, bold=False):
        label = QLabel(text)
        label.setWordWrap(True)
        label.setStyleSheet(f"color:{color};font-size:13px;{'font-weight:600;' if bold else ''}background:transparent;"
                            "padding:6px 2px;")
        return label

    def _draw_timeline(self, items, now):
        planning_ahead = not self._is_today()
        now_minutes = now.hour * 60 + now.minute
        stop = self._stop_minutes(now)
        result = today_flow.flow(items, now_minutes, stop, planning_ahead=planning_ahead)
        blocks = result["blocks"]
        if not blocks:
            return
        stop_shown = False
        marker_shown = not self._is_today()
        previous_end = None
        for entry in blocks:
            item = entry["item"]
            if not marker_shown and not item["done"] and entry["start"] >= now_minutes:
                marker_shown = True
                self._row("Now", _Rail(ACCENT, dashed=False), self._note_line(
                    today_flow.clock_text(now_minutes), ACCENT, True), time_color=ACCENT)
                previous_end = now_minutes
            if previous_end is not None and entry["start"] - previous_end >= 25 and not item["done"]:
                self._row("", _Rail("#D5DAE3", dot=False, dashed=True), self._note_line(
                    f"{today_flow.length_text(entry['start'] - previous_end)} free"))
            over = entry["end"] > stop
            if over and not stop_shown:
                stop_shown = True
                self._row("", _Rail(OVER_INK, dot=False, dashed=True), self._note_line(
                    f"You usually stop around {today_flow.clock_text(stop)}", OVER_INK, True))
            block = _Block(entry, self._is_today(), over, self._toggle, self._start, self._menu)
            self._row(today_flow.clock_text(entry["start"]), _Rail(_tag_colors(item.get("tags"))[1]), block)
            previous_end = entry["end"]
        if not stop_shown:
            gap = stop - (previous_end or stop)
            text = (f"Free until you usually stop, around {today_flow.clock_text(stop)}"
                    + (f"  ·  {today_flow.length_text(gap)} to spare" if gap >= 15 else ""))
            self._row("", _Rail("#D5DAE3", dot=False, dashed=True), self._note_line(text))

    def _draw_small(self, items):
        small = [i for i in items if i["kind"] != "task"]
        if not small:
            return
        heading = QLabel("Also on the list")
        heading.setStyleSheet(f"font-size:16px;font-weight:700;color:{INK};background:transparent;padding:18px 0 6px 0;")
        self._column.addWidget(heading)
        for item in small:
            holder = QWidget()
            holder.setStyleSheet("background:transparent;")
            lay = QVBoxLayout(holder)
            lay.setContentsMargins(0, 3, 0, 3)
            lay.addWidget(_SmallRow(item, self._toggle, self._menu))
            self._column.addWidget(holder)

    # --- actions ---------------------------------------------------------------------------------------------

    def _toggle(self, item, on):
        plan_store.set_done(self._view, item["id"], on)
        QTimer.singleShot(0, lambda: self.refresh(force=True))

    def _start(self, item):
        from qt_ui import picker_dialogs
        lock = config.load_config().get("last_lock_mode", "soft")
        error = picker_dialogs.start_manual_session(int(item.get("minutes") or plan_ml.DEFAULT_MINUTES), lock, parent=self)
        if error:
            self._sub.setText(error)

    def _menu(self, item, position):
        menu = QMenu(self)
        if item["kind"] == "task":
            times = menu.addMenu("Make it")
            for minutes in MINUTE_CHOICES:
                action = times.addAction(today_flow.length_text(minutes))
                action.triggered.connect(lambda _=False, m=minutes: self._set_minutes(item, m))
            unpin = menu.addAction("Let it flow (unpin)")
            unpin.setEnabled(item.get("start") is not None)
            unpin.triggered.connect(lambda: self._pin(item, None))
            pin = menu.addAction("Pin it to its time" if item.get("start") is None else "Pin to this time again")
            pin.triggered.connect(lambda: self._pin(item, self._time_of(item)))
            menu.addSeparator()
        kinds = menu.addMenu("This is really a…")
        for kind in plan_ml.CLASSES:
            if kind == item["kind"]:
                continue
            action = kinds.addAction(KIND_NAMES[kind])
            action.triggered.connect(lambda _=False, k=kind: self._reclassify(item, k))
        report = menu.addAction("Report a wrong guess…")
        report.triggered.connect(lambda: self._report(item))
        menu.addSeparator()
        remove = menu.addAction("Remove from the plan")
        remove.triggered.connect(lambda: self._remove(item))
        menu.exec(position)

    def _time_of(self, item):
        for entry in today_flow.flow(plan_store.load_day(self._view)["items"], datetime.now().hour * 60 + datetime.now().minute,
                                     24 * 60, planning_ahead=not self._is_today())["blocks"]:
            if entry["item"]["id"] == item["id"]:
                return entry["start"]
        return None

    def _pin(self, item, minutes):
        plan_store.set_start(self._view, item["id"], minutes)
        self.refresh(force=True)

    def _set_minutes(self, item, minutes):
        plan_store.set_minutes(self._view, item["id"], minutes)
        self.refresh(force=True)

    def _reclassify(self, item, kind):
        plan_ml.report(item["text"], item["kind"], kind, "from the Today page")
        plan_store.change_kind(self._view, item["id"], kind)
        self.refresh(force=True)

    def _report(self, item):
        note, ok = QInputDialog.getText(self, "Report a wrong guess", "What should it have been, or what went wrong?")
        if ok:
            plan_ml.report(item["text"], item["kind"], None, note)

    def _remove(self, item):
        plan_store.remove_item(self._view, item["id"])
        self.refresh(force=True)
