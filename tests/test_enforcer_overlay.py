"""Widget-level tests for the ported lock-overlay (qt_ui/enforcer_overlay.py)
-- the highest-priority Stage 1 port since it's the security-relevant
enforcement UI. Verifies window flags (frameless, always-on-top, non-modal),
the double-close guard, and the offending-process-name-gated Unblock
button, without needing a real session or the real polling thread."""
import pytest
from PySide6.QtCore import Qt

import qt_ui.enforcer_overlay as enforcer_overlay


@pytest.fixture(autouse=True)
def clear_open_overlays():
    """_open_windows is a module-level set enforcer_overlay.py relies on to
    count currently-open overlays for cascade positioning -- a previous
    test's overlay can still be sitting in it here since Qt only actually
    destroys a closed widget (firing .destroyed, which is what normally
    discards it) once deleteLater()'s event gets processed, not synchronously
    on close(). Without resetting this, one test's leftover overlay would
    silently shift where the next test's "first" overlay lands."""
    enforcer_overlay._open_windows.clear()
    yield
    enforcer_overlay._open_windows.clear()


def test_overlay_is_frameless_topmost_and_non_modal(qtbot, isolate_state):
    win = enforcer_overlay.build_overlay("test message", duration_ms=200)
    qtbot.addWidget(win)

    assert win.windowFlags() & Qt.FramelessWindowHint
    assert win.windowFlags() & Qt.WindowStaysOnTopHint
    # Never modal -- a system-wide input grab would freeze every other app,
    # not just the offending one (see enforcer.py's docstrings).
    assert win.windowModality() == Qt.NonModal

    win.close()


def test_overlay_close_is_idempotent(qtbot, isolate_state):
    win = enforcer_overlay.build_overlay("test message", duration_ms=200)
    qtbot.addWidget(win)

    win.close()
    win.close()  # must not raise, matches the Tk version's state["closed"] guard
    assert win._closed is True


def test_overlay_without_process_name_has_no_unblock_button(qtbot, isolate_state):
    win = enforcer_overlay.build_overlay("test message", duration_ms=200)
    qtbot.addWidget(win)

    from PySide6.QtWidgets import QPushButton
    buttons = win.findChildren(QPushButton)
    assert not any(b.text() == "Unblock" for b in buttons)
    win.close()


def test_overlay_with_process_name_has_unblock_button(qtbot, isolate_state):
    win = enforcer_overlay.build_overlay("test message", duration_ms=200, offending_process_name="bad.exe")
    qtbot.addWidget(win)

    from PySide6.QtWidgets import QPushButton
    buttons = win.findChildren(QPushButton)
    assert any(b.text() == "Unblock" for b in buttons)
    win.close()


def test_overlay_auto_closes_after_duration(qtbot, isolate_state):
    win = enforcer_overlay.build_overlay("test message", duration_ms=100)
    qtbot.addWidget(win)

    qtbot.waitUntil(lambda: win._closed, timeout=2000)


def test_second_overlay_cascades_away_from_first(qtbot, isolate_state):
    """Regression test: two overlays open at once (e.g. two different
    offending apps in quick succession) used to both land dead-center on top
    of each other, indistinguishable and each stealing focus back from the
    other every 50ms -- the same "flashing" symptom as the redirect-storm
    bug in window_tracker.py. The first stays centered; a second one open at
    the same time must land somewhere else."""
    first = enforcer_overlay.build_overlay("first", duration_ms=5000)
    qtbot.addWidget(first)
    second = enforcer_overlay.build_overlay("second", duration_ms=5000)
    qtbot.addWidget(second)

    assert first.pos() != second.pos()

    first.close()
    second.close()


def test_overlay_alone_is_still_centered(qtbot, isolate_state):
    win = enforcer_overlay.build_overlay("only one", duration_ms=200)
    qtbot.addWidget(win)

    from PySide6.QtWidgets import QApplication
    screen = QApplication.primaryScreen().availableGeometry()
    expected_x = screen.x() + (screen.width() - win.width()) // 2
    expected_y = screen.y() + (screen.height() - win.height()) // 2
    assert win.pos().x() == expected_x
    assert win.pos().y() == expected_y

    win.close()


def test_unblock_reason_dialog_requires_reason(qtbot, isolate_state):
    win = enforcer_overlay.build_unblock_reason_dialog("app.exe")
    qtbot.addWidget(win)

    win._confirm()
    assert "reason" in win._status_label.text().lower()
    assert win.isVisible()
    win.close()


def test_unblock_reason_dialog_confirm_calls_remove_process_from_blocklist(qtbot, isolate_state, monkeypatch):
    import enforcer
    import session_manager
    restore_calls = []
    monkeypatch.setattr(enforcer, "restore_window_for_process", lambda name: restore_calls.append(name))

    session_manager.start_session(25, "soft", ["app.exe"], [])

    win = enforcer_overlay.build_unblock_reason_dialog("app.exe")
    qtbot.addWidget(win)
    win._reason_edit.setText("needed for research")
    win._confirm()

    status = session_manager.get_status()
    assert "app.exe" not in status["processBlocklist"]
    assert restore_calls == ["app.exe"]
    assert "unblocked" in win._status_label.text().lower()
