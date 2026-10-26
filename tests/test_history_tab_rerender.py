"""The Session History tab re-reads the log whenever it is shown; it should only
re-lay-out the page when a session was actually added or the view changed."""
from datetime import date

import qt_ui.history_viewer as history_viewer


def _entry(n):
    day = date.today().isoformat()
    return {"sessionId": f"s{n}", "startTime": f"{day}T1{n}:00:00", "endTime": f"{day}T1{n}:30:00"}


def test_page_is_only_rebuilt_when_history_changes(qtbot, monkeypatch):
    sessions = [_entry(1)]
    monkeypatch.setattr(history_viewer.session_history, "load_all", lambda: list(sessions))
    tab = history_viewer.HistoryTab()
    qtbot.addWidget(tab)

    renders = []
    real_filtered = tab._filtered          # called once per full page layout
    tab._filtered = lambda everything: (renders.append(1), real_filtered(everything))[1]

    tab.refresh()
    tab.refresh()
    assert renders == []                       # nothing new: no rebuild

    sessions.append(_entry(2))
    tab.refresh()
    assert renders == [1] and len(tab._cards) == 2

    tab._set_kind("manual")                    # changing the view does rebuild
    assert len(renders) == 2
