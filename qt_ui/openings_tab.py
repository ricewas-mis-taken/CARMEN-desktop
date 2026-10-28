"""Openings: the gallery of twenty title sequences, shown inside Carmen.

The pages (qt_ui/openings/) are plain HTML and canvas, so they run in a web view. The view is
built the first time the tab is shown, so starting Carmen does not pay for a browser engine
nobody opened.
"""
import os

from PySide6.QtCore import QUrl
from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

PAGE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "openings", "index.html")


class OpeningsTab(QWidget):
    def __init__(self):
        super().__init__()
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._view = None

    def showEvent(self, event):
        if self._view is None:
            self._build()
        super().showEvent(event)

    def _build(self):
        try:
            from PySide6.QtWebEngineWidgets import QWebEngineView
            view = QWebEngineView()
            view.setUrl(QUrl.fromLocalFile(PAGE))
            self._view = view
        except Exception as exc:      # no web engine on this computer
            self._view = QLabel(f"The openings need Qt WebEngine, which is not available here.\n{exc}")
        self._layout.addWidget(self._view)
