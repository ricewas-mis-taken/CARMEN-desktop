"""The main window must never be smaller than its page needs, and must trade
its sidebar for icons before a page is squeezed."""
import pytest
from PySide6.QtWidgets import QApplication

import auth_manager
import qt_ui.main_window as mw


@pytest.fixture
def window(monkeypatch, isolate_state):
    monkeypatch.setattr(auth_manager, "is_logged_in", lambda: False)
    monkeypatch.setattr(auth_manager, "may_be_logged_in", lambda: False)
    app = QApplication.instance() or QApplication([])
    win = mw._MainWindow()
    win.show()
    app.processEvents()
    yield win
    win.close()


def test_every_page_has_a_minimum_width(window):
    assert set(mw._PAGE_MIN_WIDTH) == set(window._pages)


@pytest.mark.parametrize("key", list(mw._PAGE_MIN_WIDTH))
def test_window_cannot_be_smaller_than_the_page_needs(window, key):
    window._show_tab(key)
    window.resize(10, 10)
    assert window.width() >= mw._SIDEBAR_COMPACT + mw._PAGE_MIN_WIDTH[key]
    assert window.height() >= mw._MIN_HEIGHT


def test_sidebar_collapses_to_icons_when_narrow_and_comes_back(window):
    window.resize(2000, 900)
    QApplication.processEvents()
    assert window._sidebar.width() == mw._SIDEBAR_FULL
    assert window._nav_buttons["home"].text() == "Home"

    window._set_compact(True)
    assert window._sidebar.width() == mw._SIDEBAR_COMPACT
    assert window._nav_buttons["home"].text() == ""
    assert window._nav_buttons["home"].toolTip() == "Home"

    window._set_compact(False)
    assert window._nav_buttons["home"].text() == "Home"
