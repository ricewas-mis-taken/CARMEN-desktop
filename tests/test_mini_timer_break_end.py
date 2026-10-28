"""The mini timer keeps showing something sensible while a pomodoro goes focus -> break -> focus -> done."""
from datetime import datetime, timedelta

import enforcer
import review_store
import session_manager as sm
import qt_ui.mini_timer as mini_timer


def _expire():
    sm._state["endTime"] = (datetime.now() - timedelta(seconds=1)).isoformat()


def test_mini_timer_survives_every_pomodoro_phase_flip(qtbot, isolate_state, monkeypatch):
    monkeypatch.setattr(enforcer, "restore_all_taskbar_previews", lambda: None)
    monkeypatch.setattr(review_store, "auto_pause_for_break", lambda: None)
    monkeypatch.setattr(review_store, "auto_resume_from_break", lambda: None)
    sm.start_pomodoro_session(25, 5, 2, "soft", [], [])
    win = mini_timer.MiniTimer()
    qtbot.addWidget(win)
    win.show()
    seen = []
    for _ in range(5):                       # focus, break, focus, break/last, then done
        _expire()
        sm.get_status()
        win.refresh()
        win._keep_in_view()
        win.repaint()
        seen.append(mini_timer.describe(sm.get_status())[0])
        assert win.isVisible()
    assert seen[-1] == "No session"
