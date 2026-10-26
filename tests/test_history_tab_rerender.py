"""The Session History tab re-reads the log whenever it is shown; it should only
re-lay-out the page when a session was actually added."""
import qt_ui.history_viewer as history_viewer


def _entry(n):
    return {"sessionId": f"s{n}", "startTime": f"2026-01-0{n}T10:00:00", "endTime": f"2026-01-0{n}T10:30:00"}


def test_page_is_only_rebuilt_when_history_changes(qtbot, monkeypatch):
    sessions = [_entry(1)]
    monkeypatch.setattr(history_viewer.session_history, "load_all", lambda: list(sessions))
    tab = history_viewer.HistoryTab()
    qtbot.addWidget(tab)

    renders = []
    real_set_html = tab._text.setHtml
    tab._text.setHtml = lambda html: (renders.append(1), real_set_html(html))[1]

    tab.refresh()
    tab.refresh()
    assert renders == []                       # nothing new: no rebuild

    sessions.append(_entry(2))
    tab.refresh()
    assert renders == [1] and "2026-01-02" in tab._text.toPlainText()
