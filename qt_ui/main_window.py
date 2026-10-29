"""The real Qt main window: sidebar nav (Focus / Tasks / The Board / ...) +
QStackedWidget content, replacing calendar_gui.py's Tk `.tkraise()` frame
switching and Stage 1's placeholder stand-in. The Calendar and Finished tabs
now live in archive/ (see archive/README.md).
"""
import ctypes
import sys

if sys.platform != "darwin":
    # ctypes.wintypes crashes at import time on non-Windows platforms
    # (VARIANT_BOOL's `_type_ = "v"` isn't a registered ctypes type code
    # off-Windows -- ValueError: _type_ 'v' not supported), so it's guarded
    # here alongside win32gui, not just the code that uses it below.
    import ctypes.wintypes
    import win32gui

from PySide6.QtCore import Qt
from PySide6.QtCore import QEvent, QSize, QTimer
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import (
    QFrame,
    QButtonGroup,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

import theme
from qt_ui import motion
from qt_ui.account_widget import AccountArea
from qt_ui.home_tab import HomeTab
from qt_ui.ui_kit import LogoMark, nav_icon
from qt_ui.board_tab import BoardTab
from qt_ui.focus_tab import FocusTab
from qt_ui.history_viewer import HistoryTab
from qt_ui.openings_tab import OpeningsTab
from qt_ui.review_tab import ReviewTab
from qt_ui.scribe_tab import ScribeTab
from qt_ui.screentime_tab import ScreenTimeTab
from qt_ui.tasks_tab import TasksTab

# Dragging the title bar could snap the window into a taller rectangle.
# Root cause: Qt's QStackedWidget aggregates sizeHint()/heightForWidth()
# across *all* pages it holds, not just the current one (see _PagesStack
# below) -- and separately, Qt's own QWidgetItemV2 layout-item cache can
# keep serving a stale page's value even after that's fixed. Both of those
# only change what number gets requested; they don't explain why a plain
# title-bar *move* (which should never touch size at all) was applying it.
# A reactive Python-side correction (resizeEvent below) can request the
# right size back, but loses the race: confirmed by logging that whatever
# imposes the oversized height re-fires on literally the next tick,
# undoing the correction within milliseconds, for as long as the drag
# continues.
#
# WM_MOVING is Windows' message for a pure move -- it hands the app a
# mutable RECT for the proposed window position *before* it's applied,
# specifically so the app can adjust it, and WM_ENTERSIZEMOVE/EXITSIZEMOVE
# bracket the whole interactive drag. Capturing the window's actual frame
# size (GetWindowRect -- physical pixels, no logical/physical or
# client/frame conversion needed) once at ENTERSIZEMOVE and re-asserting
# that exact width/height on every subsequent WM_MOVING intercepts
# synchronously, before any size change is ever applied, so there's no
# race to lose -- the window simply cannot resize while being moved.
WM_ENTERSIZEMOVE = 0x0231
WM_EXITSIZEMOVE = 0x0232
WM_MOVING = 0x0216


class _RECT(ctypes.Structure):
    _fields_ = [
        ("left", ctypes.c_long),
        ("top", ctypes.c_long),
        ("right", ctypes.c_long),
        ("bottom", ctypes.c_long),
    ]


_win = None


def _scrolling(page):
    """Wraps a page that has a size it cannot go below, so a small window
    scrolls it instead of crushing it."""
    from PySide6.QtWidgets import QScrollArea
    area = QScrollArea()
    area.setWidgetResizable(True)
    area.setFrameShape(QFrame.NoFrame)
    area.setStyleSheet("QScrollArea{background:transparent;}")     # the themed page colour shows through
    area.viewport().setAutoFillBackground(False)
    area.setWidget(page)
    page.setAutoFillBackground(False)                              # setWidget turns it on
    area.refresh = getattr(page, "refresh", lambda: None)
    return area


def open_main_window():
    global _win
    if _win is not None:
        _win.show()
        _win.raise_()
        _win.activateWindow()
        return
    _win = _MainWindow()
    _win.show()


_PAGE_FOR_TABLE = {
    "tasks": "tasks", "board": "board",
    "review_topics": "review", "review_subjects": "review",
    "review_problems": "review", "review_sessions": "review",
}


def refresh_after_pull(tables):
    """Called (on the GUI thread) when a sync pulled another computer's
    changes: re-reads the pages whose data changed, so a review added on the
    PC shows up on an already-open window instead of after the next restart."""
    if _win is None:
        return
    pages = {_PAGE_FOR_TABLE[t] for t in tables if t in _PAGE_FOR_TABLE}
    pages.add("home")
    for key in pages:
        page = _win._pages.get(key)
        refresh = getattr(page, "refresh", None)
        if refresh is None:
            continue
        try:
            refresh()
        except Exception:
            pass


def open_sign_in():
    """Tray "Sign in": the main window with the sign-in dialog on top."""
    open_main_window()
    _win._account_area._open_auth_dialog()


_NORMAL_SIZE = (800, 800)

# The window cannot be dragged smaller than its current page needs: an explicit
# setMinimumSize overrides what the layouts report, so without this the pages were
# squeezed until buttons and titles lost their text. Content widths, beside the sidebar.
_PAGE_MIN_WIDTH = {
    "home": 480, "focus": 630, "tasks": 520, "board": 600,
    "review": 640, "scribe": 640, "screentime": 480, "history": 900, "openings": 640,
}
_MIN_HEIGHT = 560
_SIDEBAR_FULL = 180
_SIDEBAR_COMPACT = 64
_COMPACT_BELOW = 900   # window width under which the sidebar shows icons only


_NO_ENTRANCE = {"scribe", "openings"}      # a canvas and a web view: nothing card-like to stagger


class _NavMarker(QWidget):
    """The accent bar beside the current tab. It glides to the next tab instead of jumping."""

    def __init__(self, parent):
        super().__init__(parent)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.setFixedWidth(4)
        self._anim = None
        self._colour = QColor("#9D8CE6")
        self.hide()

    def glide_to(self, y, height, animated=True):
        self._colour = QColor(theme.current_colours()["accent"]).lighter(135)
        start_y, start_h = (self.y(), self.height()) if self.isVisible() else (y, height)
        self.move(3, start_y)
        self.setFixedHeight(start_h)
        self.show()
        self.raise_()
        motion.stop(self._anim)
        if not animated or not motion.enabled() or (start_y, start_h) == (y, height):
            self.move(3, y)
            self.setFixedHeight(height)
            return
        self._anim = motion.tween(
            self, 0.0, 1.0, motion.MEDIUM_MS,
            lambda f: (self.move(3, round(start_y + (y - start_y) * f)), self.setFixedHeight(round(start_h + (height - start_h) * f))))

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        p.setBrush(self._colour)
        p.drawRoundedRect(self.rect(), 2, 2)


class _PagesStack(QStackedWidget):
    """QStackedWidget's sizeHint()/minimumSizeHint() default to the max
    across *all* pages it holds, not just the visible one -- so a tall
    hidden page could otherwise inflate the window's hint even while a
    different, shorter tab is showing. Reporting only the current page's
    hint keeps the window's size independent of whichever tab isn't on
    screen.

    Overriding sizeHint()/minimumSizeHint() turned out not to be enough on
    its own -- Qt separately aggregates hasHeightForWidth()/heightForWidth()
    across *all* pages too (confirmed by direct measurement: with Calendar
    current, stack.heightForWidth(688) still returned Finished's value,
    909, not Calendar's, 752), and that's what the parent layout actually
    uses once any page reports hasHeightForWidth() True. Overriding those
    the same way closes the same loophole for that separate code path."""

    def sizeHint(self):
        current = self.currentWidget()
        return current.sizeHint() if current else super().sizeHint()

    def minimumSizeHint(self):
        current = self.currentWidget()
        return current.minimumSizeHint() if current else super().minimumSizeHint()

    def hasHeightForWidth(self):
        current = self.currentWidget()
        return current.hasHeightForWidth() if current else super().hasHeightForWidth()

    def heightForWidth(self, width):
        current = self.currentWidget()
        return current.heightForWidth(width) if current else super().heightForWidth(width)


class _RootLayout(QHBoxLayout):
    """The top-level layout hasHeightForWidth() as soon as anything in the
    tree does -- here, NextUpLabel (qt_ui/next_up_widget.py), a word-wrapped
    QLabel that sits at the top of both Calendar and Focus (never Tasks or
    Finished, which is exactly the split earlier testing found: only
    NextUpLabel's tabs ever reproduced the drag-stretch). A hasHeightForWidth
    top-level widget
    gets special-cased by Qt's Windows platform code, which reasserts a
    heightForWidth-derived height right as an interactive move/resize
    finishes -- observed directly: WM_MOVING held the frame steady for the
    whole drag, then the instant EXITSIZEMOVE fired, resizeEvent reported
    the stretched height again, with no drag in progress to blame. Forcing
    False here means the top-level widget itself never reports
    hasHeightForWidth, regardless of what any child does, closing that off
    at the one place that actually matters instead of chasing each
    word-wrapped label individually."""

    def hasHeightForWidth(self):
        return False

    def heightForWidth(self, width):
        return -1


class _MainWindow(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Carmen Focus")
        self.resize(*_NORMAL_SIZE)
        self.setMinimumSize(_SIDEBAR_COMPACT + 480, _MIN_HEIGHT)
        self._compact = False
        self._was_maximized = False
        self._drag_frame_size = None  # (width, height) in physical px, set only during a title-bar move

        root_layout = _RootLayout(self)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        sidebar = self._build_sidebar()
        root_layout.addWidget(sidebar)

        content = QWidget()
        content.setObjectName("ContentArea")
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(0, 0, 0, 0)
        self._stack = _PagesStack()
        content_layout.addWidget(self._stack)
        root_layout.addWidget(content, 1)

        self._pages = {}
        self._add_page("home", HomeTab(on_navigate=self._show_tab))
        self._add_page("focus", FocusTab())
        self._add_page("tasks", TasksTab())
        self._add_page("board", BoardTab())
        self._add_page("review", ReviewTab())
        self._add_page("scribe", _scrolling(ScribeTab()))
        self._add_page("screentime", ScreenTimeTab())
        self._add_page("history", HistoryTab())
        self._add_page("openings", OpeningsTab())

        motion.install()
        self._show_tab("home")
        QTimer.singleShot(0, lambda: self._move_marker(animated=False))

    def showEvent(self, event):
        super().showEvent(event)
        QTimer.singleShot(0, lambda: self._move_marker(animated=False))

    def _add_page(self, key, widget):
        self._pages[key] = widget
        self._stack.addWidget(widget)

    def _build_sidebar(self):
        sidebar = QWidget()
        sidebar.setObjectName("Sidebar")
        sidebar.setFixedWidth(_SIDEBAR_FULL)
        self._sidebar = sidebar
        layout = QVBoxLayout(sidebar)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        brand = QWidget()
        brand_row = QHBoxLayout(brand)
        brand_row.setContentsMargins(20, 24, 18, 18)
        brand_row.setSpacing(10)
        brand_row.addWidget(LogoMark(22), 0, Qt.AlignVCenter)
        title = QLabel("Carmen Focus")
        title.setObjectName("SidebarTitle")
        brand_row.addWidget(title, 1)
        layout.addWidget(brand)
        self._brand_title = title
        self._brand_row = brand_row
        self._group_captions = []

        self._nav_buttons = {}
        self._nav_group = QButtonGroup(self)
        self._nav_group.setExclusive(True)
        # (page key, label) in groups; a group's heading is the line above it.
        nav_groups = [
            ("Today", [("home", "Home")]),
            ("Work", [("focus", "Focus"), ("tasks", "Tasks"), ("board", "The Board")]),
            ("Study", [("review", "Review"), ("scribe", "Scribe")]),
            ("Look back", [("screentime", "Screen Time"), ("history", "Session History")]),
            ("Extras", [("openings", "Openings")]),
        ]
        nav_items = [item for _heading, items in nav_groups for item in items]
        group_starts = {items[0][0]: heading for heading, items in nav_groups}
        for key, label in nav_items:
            if key in group_starts:
                caption = QLabel(group_starts[key])
                caption.setObjectName("SidebarGroup")
                layout.addWidget(caption)
                self._group_captions.append(caption)
            button = QPushButton(label)
            button.setProperty("class", "NavButton")
            button.setProperty("fullLabel", label)
            button.setIcon(nav_icon(key))
            button.setIconSize(QSize(20, 20))
            button.setCursor(Qt.PointingHandCursor)
            button.setCheckable(True)
            button.clicked.connect(lambda checked, k=key: self._show_tab(k))
            layout.addWidget(button)
            self._nav_group.addButton(button)
            self._nav_buttons[key] = button
            if key == "home":
                button.setChecked(True)

        layout.addStretch(1)
        self._marker = _NavMarker(sidebar)

        self._account_area = AccountArea()
        layout.addWidget(self._account_area)

        return sidebar

    def _show_tab(self, key):
        changed = getattr(self, "_current_key", None) != key
        self._stack.setCurrentWidget(self._pages[key])
        self._current_key = key
        button = self._nav_buttons.get(key)
        if button is not None:
            button.setChecked(True)
        self._apply_min_size()
        self._move_marker(animated=changed)
        if changed and key not in _NO_ENTRANCE:
            motion.reveal(self._pages[key])

    def _move_marker(self, animated=True):
        marker = getattr(self, "_marker", None)
        button = self._nav_buttons.get(getattr(self, "_current_key", ""))
        if marker is None or button is None or not button.isVisible():
            return
        marker.glide_to(button.y() + 9, max(8, button.height() - 18), animated)

    def _sidebar_width(self):
        return _SIDEBAR_COMPACT if self._compact else _SIDEBAR_FULL

    def _apply_min_size(self):
        """Window minimum = sidebar + what the current page needs."""
        key = getattr(self, "_current_key", "home")
        self.setMinimumSize(_SIDEBAR_COMPACT + _PAGE_MIN_WIDTH.get(key, 480), _MIN_HEIGHT)

    def _set_compact(self, compact):
        """Icon-only sidebar on narrow windows, so the page keeps its room."""
        if compact == self._compact:
            return
        self._compact = compact
        self._sidebar.setFixedWidth(self._sidebar_width())
        self._brand_title.setVisible(not compact)
        self._brand_row.setContentsMargins(*((0, 24, 0, 18) if compact else (20, 24, 18, 18)))
        for caption in self._group_captions:
            caption.setVisible(not compact)
        for button in self._nav_buttons.values():
            full = button.property("fullLabel")
            button.setText("" if compact else full)
            button.setToolTip(full if compact else "")
        self._account_area.set_compact(compact)
        self._sidebar.setStyleSheet(
            "QPushButton.NavButton { margin: 1px 8px; padding: 9px 0px; }" if compact else "")
        self._apply_min_size()
        QTimer.singleShot(0, lambda: self._move_marker(animated=False))

    def changeEvent(self, event):
        # Qt restores a "restore down" (un-maximize) click to whatever
        # normalGeometry() happened to be recorded as -- which drifts away
        # from square if the window was ever resized (by the user dragging
        # an edge, or a previous maximize/restore round-trip) while still
        # maximized, or if the OS snapped it during the maximize animation.
        # Re-asserting a fixed 800x800 right after the state flips back to
        # Normal keeps "restore" always landing on the same square size
        # instead of a stretched rectangle.
        if event.type() == QEvent.WindowStateChange:
            is_maximized = bool(self.windowState() & Qt.WindowMaximized)
            if self._was_maximized and not is_maximized:
                self.resize(*_NORMAL_SIZE)
            self._was_maximized = is_maximized
        super().changeEvent(event)

    def resizeEvent(self, event):
        # No corrective logic here anymore -- this used to force the window
        # back to _stable_size whenever it looked "too big" or had jumped in
        # height, as a backstop against the drag-stretch bug. That backstop
        # is what broke maximize: the first resizeEvent during a maximize
        # transition fires before windowState() actually reports
        # WindowMaximized, so it looked identical to the bug (a legitimate
        # full-screen size, width unchanged in some cases, well past
        # _MAX_REASONABLE_SIZE) and got shrunk right back down while the
        # window was already sitting at the maximize position (0,0) --
        # exactly the "stuck in the top-left corner" symptom. The actual
        # drag-stretch bug is now fixed structurally instead: WM_MOVING
        # (nativeEvent below) freezes the frame size for the whole drag, and
        # _RootLayout.hasHeightForWidth()==False stops Qt's Windows platform
        # code from reasserting a heightForWidth-derived height right as the
        # drag ends. Neither of those needs a reactive resize() call, so
        # there's nothing left for this backstop to correct.
        super().resizeEvent(event)
        self._set_compact(event.size().width() < _COMPACT_BELOW)

    def nativeEvent(self, eventType, message):
        # No macOS equivalent needed here -- this guards a Windows-specific
        # drag-stretch bug (see the WM_MOVING comment above); macOS's own
        # window manager doesn't have this problem, so there's nothing to
        # replace it with, just skip it on darwin.
        if sys.platform != "darwin" and eventType == "windows_generic_MSG":
            msg = ctypes.wintypes.MSG.from_address(int(message))
            if msg.message == WM_ENTERSIZEMOVE:
                try:
                    # Don't freeze the frame during a maximized-window drag --
                    # that's a "drag to restore" gesture and we want Windows'
                    # natural un-maximize behaviour to proceed unimpeded.
                    if self.isMaximized():
                        self._drag_frame_size = None
                    else:
                        left, top, right, bottom = win32gui.GetWindowRect(int(self.winId()))
                        self._drag_frame_size = (right - left, bottom - top)
                except Exception:
                    self._drag_frame_size = None
            elif msg.message == WM_EXITSIZEMOVE:
                self._drag_frame_size = None
            elif msg.message == WM_MOVING and self._drag_frame_size is not None:
                rect = _RECT.from_address(msg.lParam)
                target_w, target_h = self._drag_frame_size
                rect.right = rect.left + target_w
                rect.bottom = rect.top + target_h
                return True, 1
        return super().nativeEvent(eventType, message)
