"""Tray menu: no pause/end/history; API token only once signed in. History is a tab."""
import auth_manager
import tray
import qt_ui.history_viewer as history_viewer


def _items(monkeypatch, signed_in):
    monkeypatch.setattr(auth_manager, "is_logged_in", lambda: signed_in)
    icon = tray.build_tray_icon(lambda: None)
    return {str(i.text): i for i in icon.menu.items}


def test_menu_has_no_pause_end_or_history(monkeypatch):
    names = " | ".join(_items(monkeypatch, True))
    for gone in ("Pause", "End Session", "Session History"):
        assert gone not in names


def test_api_token_only_when_signed_in(monkeypatch):
    out = _items(monkeypatch, False)
    assert not out["Copy API Token (for browser extension)"].visible and out["Sign in"].visible
    inn = _items(monkeypatch, True)
    assert inn["Copy API Token (for browser extension)"].visible and not inn["Sign in"].visible


def test_history_tab_lists_sessions(qtbot, monkeypatch):
    monkeypatch.setattr(history_viewer.session_history, "load_all", lambda: [{"startTime": "2026-01-01T10:00:00", "endTime": "2026-01-01T10:30:00"}])
    tab = history_viewer.HistoryTab()
    qtbot.addWidget(tab)
    tab._set_window(None)
    assert len(tab._cards) == 1 and tab._cards[0].time_label.text() == "10:00 – 10:30"
