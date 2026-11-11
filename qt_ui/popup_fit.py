"""Every popup (the windows named PopupBg) opens at a sensible size, inside the screen, and centred
on the window that opened it -- however much text or however big a picture it carries.

One application-wide filter instead of sizing each dialog by hand: a dialog that sizes itself from its
content (a long description, a photo) used to open far bigger than the screen allows."""
from qt_ui import filter_guard
from PySide6.QtCore import QEvent, QObject, QPoint, Qt
from PySide6.QtWidgets import QApplication, QFrame, QScrollArea, QVBoxLayout, QWidget

MIN_WIDTH = 380
MAX_WIDTH = 560          # wider than this is never needed for a form; long text wraps or scrolls
WIDE_WIDTH = 780         # for the few popups that really lay out a table or a timeline
SCREEN_SHARE = 0.9       # never taller or wider than this much of the screen


def scroll_content(widget):
    """Moves the popup's layout into a scroll area, so a form taller than the screen scrolls instead of
    being cut off (or opening bigger than the monitor)."""
    layout = widget.layout()
    if layout is None or widget.property("popupScrolls"):
        return
    widget.setProperty("popupScrolls", True)
    margins, spacing = layout.contentsMargins(), layout.spacing()
    holder = QWidget()
    holder.setObjectName("PopupBg")          # keeps the popup's own background
    moved = QVBoxLayout(holder)
    while layout.count():                      # every widget, spacer and sub-layout moves across as it is
        moved.addItem(layout.takeAt(0))
    moved.setContentsMargins(margins)
    moved.setSpacing(spacing)
    QWidget().setLayout(layout)                # the emptied old layout goes away with a throwaway owner
    outer = QVBoxLayout(widget)
    outer.setContentsMargins(0, 0, 0, 0)
    area = QScrollArea()
    area.setWidgetResizable(True)
    area.setFrameShape(QFrame.NoFrame)
    area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
    area.setWidget(holder)
    outer.addWidget(area)
    widget.setMinimumSize(0, 0)                # Qt had already raised it to the old layout's full height


def fit(widget, anchor=None):
    """Clamp widget to a comfortable size and centre it over anchor (or the screen)."""
    screen = widget.screen() or QApplication.primaryScreen()
    area = screen.availableGeometry()
    limit_h = int(area.height() * SCREEN_SHARE)
    if widget.minimumSizeHint().height() > limit_h:
        scroll_content(widget)
    wide = widget.minimumSizeHint().width() > MAX_WIDTH
    max_w = min(WIDE_WIDTH if wide else MAX_WIDTH, int(area.width() * SCREEN_SHARE))
    max_h = limit_h
    width = max(min(MIN_WIDTH, max_w), min(widget.width(), max_w))
    height = min(widget.height(), max_h)
    widget.resize(width, height)
    center = anchor.frameGeometry().center() if anchor is not None and anchor.isVisible() else area.center()
    top_left = QPoint(center.x() - widget.width() // 2, center.y() - widget.height() // 2)
    top_left.setX(max(area.left(), min(top_left.x(), area.right() - widget.width())))
    top_left.setY(max(area.top(), min(top_left.y(), area.bottom() - widget.height())))
    widget.move(top_left)


class _Fitter(QObject):
    def eventFilter(self, obj, event):
        if filter_guard.busy():
            return False
        with filter_guard.quiet():
            return self._filter(obj, event)

    def _filter(self, obj, event):
        if (event.type() == QEvent.Show and isinstance(obj, QWidget) and obj.isWindow()
                and obj.objectName() == "PopupBg" and not obj.property("popupFitted")
                and not (obj.windowFlags() & Qt.Popup) == Qt.Popup):
            obj.setProperty("popupFitted", True)
            active = QApplication.activeWindow()
            fit(obj, active if active is not obj else None)
        return False


_fitter = None


def install(app=None):
    global _fitter
    app = app or QApplication.instance()
    if _fitter is None and app is not None:
        _fitter = _Fitter(app)
        app.installEventFilter(_fitter)
