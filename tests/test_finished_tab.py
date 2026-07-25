"""Widget-level tests for the ported Finished tab (qt_ui/finished_tab.py):
the read-only log of completed focus sessions. Session start/status/
pause/nuclear-end controls now live in their own tab -- see
tests/test_focus_tab.py."""
from datetime import datetime, timedelta

import qt_ui.finished_tab as finished_tab


def _fake_session(title=None, end_type="manual", start=None, minutes=30):
    start = start or datetime(2026, 6, 1, 10, 0)
    end = start + timedelta(minutes=minutes)
    return {
        "startTime": start.isoformat(), "endTime": end.isoformat(),
        "endType": end_type, "reason": None, "lockMode": "soft",
        "processWhitelist": [], "domainWhitelist": [], "violationCount": 0,
        "violationLog": [], "domainWhitelistAdditions": [], "processWhitelistAdditions": [],
        "source": "manual", "eventId": None, "eventTitle": title,
    }


def test_session_title_falls_back_to_focus_session():
    assert finished_tab._session_title(_fake_session(title=None)) == "Focus session"
    assert finished_tab._session_title(_fake_session(title="Deep Work")) == "Deep Work"


def test_session_color_by_end_type():
    assert finished_tab._session_color(_fake_session(end_type="manual")) == "#5B8DEF"
    assert finished_tab._session_color(_fake_session(end_type="nuclear")) == "#e53935"
    assert finished_tab._session_color(_fake_session(end_type="timeout")) == "#fb8c00"
    assert finished_tab._session_color(_fake_session(end_type="unknown-type")) == "#5B8DEF"


def test_finished_tab_no_sessions_shows_empty_message(qtbot, isolate_state):
    tab = finished_tab.FinishedTab()
    qtbot.addWidget(tab)
    assert "no finished sessions" in tab._last_session_label.text().lower()


def test_finished_tab_shows_last_session_summary(qtbot, isolate_state):
    import session_history
    session_history.append_entry(_fake_session(title="Reading", minutes=45))

    tab = finished_tab.FinishedTab()
    qtbot.addWidget(tab)
    text = tab._last_session_label.text()
    assert "Reading" in text
    assert "45m" in text


def test_month_view_search_filters_by_title(qtbot, isolate_state):
    import session_history
    session_history.append_entry(_fake_session(title="Writing"))
    session_history.append_entry(_fake_session(title="Coding"))

    tab = finished_tab.FinishedTab()
    qtbot.addWidget(tab)
    month_view = tab._month_view
    assert len(month_view._matching_sessions()) == 2

    month_view._search_edit.setText("writ")
    assert len(month_view._matching_sessions()) == 1
    assert month_view._matching_sessions()[0]["eventTitle"] == "Writing"


def test_day_view_hides_label_only_for_sub_minute_sessions(qtbot, isolate_state):
    # Regression test for the text-cutoff bug: clusters of very short
    # sessions used to render blocks too short for their label text to fit,
    # clipping it. The fix drops the label entirely for sub-minute sessions
    # (this test) while every longer session keeps a label, now rendered
    # vertically centered so it isn't clipped even at MIN_BLOCK_HEIGHT.
    import session_history
    target_day = datetime(2026, 6, 20, 9, 0)
    session_history.append_entry(_fake_session(title="Blip", start=target_day, minutes=0.1))
    session_history.append_entry(
        _fake_session(title="Quick Task", start=target_day + timedelta(minutes=1), minutes=5)
    )

    tab = finished_tab.FinishedTab()
    qtbot.addWidget(tab)
    tab._day_view.show_date(target_day.date())

    items = [
        item for item in tab._day_view._scene.items()
        if isinstance(item, finished_tab._SessionBlockItem)
    ]
    assert len(items) == 2

    blip = next(i for i in items if i._session.get("eventTitle") == "Blip")
    quick_task = next(i for i in items if i._session.get("eventTitle") == "Quick Task")
    assert blip._text == ""
    assert quick_task._text != ""


def test_day_view_shows_sessions_for_selected_date(qtbot, isolate_state):
    import session_history
    target_day = datetime(2026, 6, 15, 9, 0)
    session_history.append_entry(_fake_session(title="Target Session", start=target_day))
    session_history.append_entry(_fake_session(title="Other Day", start=target_day + timedelta(days=1)))

    tab = finished_tab.FinishedTab()
    qtbot.addWidget(tab)
    tab._day_view.show_date(target_day.date())

    day_sessions = [
        s for s in tab._day_view._matching_sessions()
        if finished_tab._parse_session_dt(s.get("startTime")).date() == target_day.date()
    ]
    assert len(day_sessions) == 1
    assert day_sessions[0]["eventTitle"] == "Target Session"
