"""The real Qt main window: sidebar nav (Calendar / Focus / Tasks /
Finished) + QStackedWidget content, replacing calendar_gui.py's Tk
`.tkraise()` frame switching and Stage 1's placeholder stand-in.
"""
import ctypes
import ctypes.wintypes

import win32gui

from PySide6.QtCore import Qt
from PySide6.QtCore import QEvent
from PySide6.QtWidgets import (
    QButtonGroup,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from qt_ui.calendar_page import CalendarPage
from qt_ui.finished_tab import FinishedTab
from qt_ui.focus_tab import FocusTab
from qt_ui.review_tab import ReviewTab
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


def open_main_window():
    global _win
    if _win is not None:
        _win.show()
        _win.raise_()
        _win.activateWindow()
        return
    _win = _MainWindow()
    _win.show()


def refresh_calendar_views():
    """Called after an event is saved/deleted (qt_ui/event_editor.py) so
    any already-open calendar/finished views pick up the change -- the Qt
    equivalent of the Tk version's _refresh_all_calendar_views() walking
    _state["refresh_callbacks"]. A no-op if the main window isn't open."""
    if _win is None:
        return
    _win._pages["calendar"].refresh()
    _win._pages["finished"].refresh()


_NORMAL_SIZE = (800, 800)


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
        self.setMinimumSize(540, 540)
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
        self._add_page("calendar", CalendarPage())
        self._add_page("focus", FocusTab())
        self._add_page("tasks", TasksTab())
        self._add_page("review", ReviewTab())
        self._add_page("finished", FinishedTab())

        self._show_tab("calendar")

    def _add_page(self, key, widget):
        self._pages[key] = widget
        self._stack.addWidget(widget)

    def _build_sidebar(self):
        sidebar = QWidget()
        sidebar.setObjectName("Sidebar")
        sidebar.setFixedWidth(180)
        layout = QVBoxLayout(sidebar)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        title = QLabel("Carmen Focus")
        title.setObjectName("SidebarTitle")
        layout.addWidget(title)

        self._nav_group = QButtonGroup(self)
        self._nav_group.setExclusive(True)
        nav_items = [
            ("calendar", "📅  Calendar"),
            ("focus", "🔥  Focus"),
            ("tasks", "🎯  Tasks"),
            ("review", "🔁  Review"),
            ("finished", "✅  Finished"),
        ]
        for key, label in nav_items:
            button = QPushButton(label)
            button.setProperty("class", "NavButton")
            button.setCheckable(True)
            button.clicked.connect(lambda checked, k=key: self._show_tab(k))
            layout.addWidget(button)
            self._nav_group.addButton(button)
            if key == "calendar":
                button.setChecked(True)

        layout.addStretch(1)
        return sidebar

    def _show_tab(self, key):
        self._stack.setCurrentWidget(self._pages[key])

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

    def nativeEvent(self, eventType, message):
        if eventType == "windows_generic_MSG":
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
