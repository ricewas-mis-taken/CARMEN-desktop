"""Motion: off in tests by default, and when on it must finish, clean up, and keep every tab on
the one themed background."""
import os

import pytest
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QApplication

import auth_manager
import qt_ui.main_window as mw
from qt_ui import motion
from qt_ui.ui_kit import Ring


@pytest.fixture
def moving(monkeypatch):
    """Turn motion on for one test (it is off under pytest)."""
    monkeypatch.setattr(motion, "FORCE", True)
    yield


@pytest.fixture
def window(monkeypatch, isolate_state):
    monkeypatch.setattr(auth_manager, "is_logged_in", lambda: False)
    monkeypatch.setattr(auth_manager, "may_be_logged_in", lambda: False)
    app = QApplication.instance() or QApplication([])
    import main
    old = app.styleSheet()
    main._load_stylesheet(app)                     # the real stylesheet, so backgrounds are the real ones
    win = mw._MainWindow()
    win.show()
    app.processEvents()
    yield win
    win.close()
    app.setStyleSheet(old)


def test_motion_is_off_under_pytest():
    assert "PYTEST_CURRENT_TEST" in os.environ
    assert motion.enabled() is False


def test_tween_jumps_to_the_end_when_motion_is_off():
    seen = []
    assert motion.tween(None, 0, 5, 300, seen.append) is None
    assert seen == [5]


def test_the_ring_eases_to_its_new_value(qtbot, moving):
    ring = Ring()
    qtbot.addWidget(ring)
    ring.set_state("#5B8DEF", 0.6, "10", "min")
    assert ring._shown < 0.6                     # on its way, not there yet
    qtbot.waitUntil(lambda: ring._shown == pytest.approx(0.6, abs=0.001), timeout=2000)


def test_the_ring_is_exact_straight_away_when_motion_is_off(qtbot):
    ring = Ring()
    qtbot.addWidget(ring)
    ring.set_state("#5B8DEF", 0.6, "10", "min")
    assert ring._shown == 0.6


def test_a_page_entrance_leaves_no_veils_behind(qtbot, window, moving):
    window._show_tab("tasks")
    window._show_tab("history")
    page = window._pages["history"]
    veils = lambda: [w for w in page.findChildren(motion._Veil)]
    qtbot.waitUntil(lambda: not page._motion_veils and not veils(), timeout=3000)


def test_switching_tabs_quickly_does_not_stack_veils(qtbot, window, moving):
    for key in ("tasks", "board", "tasks", "board", "tasks"):
        window._show_tab(key)
    for page in window._pages.values():
        qtbot.waitUntil(lambda page=page: not page.findChildren(motion._Veil), timeout=3000)


def test_the_nav_marker_ends_on_the_current_tab(qtbot, window, moving):
    window._show_tab("board")
    button = window._nav_buttons["board"]
    qtbot.waitUntil(lambda: window._marker.y() == button.y() + 9, timeout=2000)


def test_every_tab_shares_the_themed_page_background(window):
    """One page colour behind every tab. Home has its own sky and the openings are a web page,
    so those two are checked separately."""
    expected = None
    for key, widget in window._pages.items():
        if key in ("home", "openings"):
            continue
        window._show_tab(key)
        QApplication.processEvents()
        image = window.grab().toImage()
        colour = image.pixelColor(window.width() - 3, 3)
        expected = expected or colour.name()
        assert colour.name() == expected, key
