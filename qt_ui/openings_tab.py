"""Openings: the gallery of twenty title sequences, shown inside Carmen.

The pages (qt_ui/openings/) are plain HTML and canvas, so they run in a web view. The view is
built the first time the tab is shown, so starting Carmen does not pay for a browser engine
nobody opened.
"""
import os
import sys
from urllib.parse import quote

import theme
from PySide6.QtCore import QUrl
from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

PAGE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "openings", "index.html")


def page_url():
    """The page with the chosen theme in its address, so its background is the same sky as Home."""
    top, middle, bottom = theme.current_sky()
    accent = theme.current_colours()["accent"]
    url = QUrl.fromLocalFile(PAGE)
    url.setQuery(f"sky={quote(top)},{quote(middle)},{quote(bottom)}&accent={quote(accent)}")
    return url


class OpeningsTab(QWidget):
    def __init__(self):
        super().__init__()
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._view = None
        self._themed = None

    def showEvent(self, event):
        if self._view is None:
            self._build()
        elif self._themed != page_url().toString() and hasattr(self._view, "setUrl"):
            self._view.setUrl(page_url())          # the theme changed since it was built
            self._themed = page_url().toString()
        super().showEvent(event)

    def _build(self):
        try:
            if "pytest" in sys.modules or os.environ.get("CARMEN_NO_WEBENGINE"):
                # Chromium aborts the whole process when a headless test or audit run
                # creates and drops one, which shows up as "Python quit unexpectedly".
                raise RuntimeError("web engine switched off for this run")
            from PySide6.QtWebEngineWidgets import QWebEngineView
            view = QWebEngineView()
            view.setUrl(page_url())
            self._themed = page_url().toString()
            self._view = view
        except Exception as exc:      # no web engine on this computer
            self._view = QLabel(f"The openings need Qt WebEngine, which is not available here.\n{exc}")
        self._layout.addWidget(self._view)
