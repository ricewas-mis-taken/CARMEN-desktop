"""The Session History page: it only reads session_history.json (through load_all) and shows
a summary, a two-week strip and a card per session. Nothing here writes the file."""
from datetime import date, datetime, timedelta

import pytest

import qt_ui.history_viewer as hv
import session_history


def _session(days_ago=0, hour=9, minutes=30, **over):
    start = (datetime.now() - timedelta(days=days_ago)).replace(hour=hour, minute=0, second=0, microsecond=0)
    base = {"startTime": start.isoformat(), "endTime": (start + timedelta(minutes=minutes)).isoformat(),
            "endType": "manual", "reason": None, "lockMode": "soft", "processBlocklist": ["a.exe"],
            "domainWhitelist": ["x.com"], "violationCount": 0, "violationLog": [], "source": "manual",
            "eventId": None, "eventTitle": None}
    base.update(over)
    return base


@pytest.fixture
def history(monkeypatch):
    data = []
    monkeypatch.setattr(session_history, "load_all", lambda: list(data))
    return data


def _tab(qtbot):
    tab = hv.HistoryTab()
    qtbot.addWidget(tab)
    return tab


# --- reading the file -----------------------------------------------------------------

def test_worked_time_leaves_out_pauses():
    start = datetime(2026, 10, 1, 9, 0)
    s = _session(minutes=60)
    s["startTime"], s["endTime"] = start.isoformat(), (start + timedelta(minutes=60)).isoformat()
    s["violationLog"] = [{"kind": "pause", "timestamp": (start + timedelta(minutes=20)).isoformat()},
                         {"kind": "resume", "timestamp": (start + timedelta(minutes=50)).isoformat()}]
    assert hv.worked_seconds(s) == 30 * 60


def test_a_session_without_times_counts_for_nothing():
    assert hv.worked_seconds({"startTime": None, "endTime": None}) == 0
    assert hv.worked_seconds({"startTime": "garbage", "endTime": "nonsense"}) == 0


def test_kinds_and_titles():
    assert hv.session_kind({"source": "task"}) == "task"
    assert hv.session_kind({"source": "review", "reviewProblemName": "P"}) == "review"
    assert hv.session_kind({"source": "calendar-event"}) == "calendar"
    assert hv.session_kind({}) == "manual"
    assert hv.session_title({"source": "task", "eventTitle": "Chinese"}) == "Chinese"
    assert hv.session_title({}) == "Focus session"
    assert hv.session_title({"source": "review", "reviewProblemName": "Tones", "isFirstAttempt": False}) == "Review: Tones"


def test_summary_counts_time_sessions_violations_and_clean_ones():
    sessions = [_session(minutes=30), _session(minutes=45, violationCount=3), _session(minutes=15)]
    assert hv.summarize(sessions) == {"seconds": 90 * 60, "sessions": 3, "violations": 3, "clean": 2}


def test_violations_fall_back_to_counting_the_log():
    s = _session(violationLog=[{"kind": "domain"}, {"kind": "process"}, {"kind": "pause"}])
    del s["violationCount"]
    assert hv.violation_count(s) == 2


def test_the_strip_has_a_bar_for_each_of_the_last_days():
    today = date(2026, 10, 25)
    start = datetime(2026, 10, 24, 10, 0)
    s = {"startTime": start.isoformat(), "endTime": (start + timedelta(minutes=40)).isoformat(), "violationLog": []}
    days = hv.minutes_by_day([s], days=14, today=today)
    assert len(days) == 14 and days[0][0] == today - timedelta(days=13) and days[-1][0] == today
    assert dict(days)[date(2026, 10, 24)] == pytest.approx(40)
    assert dict(days)[today] == 0


def test_days_are_grouped_newest_first_and_labelled():
    today = date.today()
    groups = hv.group_by_day([_session(2), _session(0, hour=8), _session(0, hour=15), _session(1)])
    assert [d for d, _s in groups] == [today, today - timedelta(days=1), today - timedelta(days=2)]
    assert [s["startTime"][11:13] for s in groups[0][1]] == ["15", "08"]      # newest first inside a day
    assert hv.day_label(today) == "Today" and hv.day_label(today - timedelta(days=1)) == "Yesterday"
    assert hv.day_label(date(2026, 10, 22), today=date(2026, 10, 25)) == "Thursday, Oct 22"


# --- the page ---------------------------------------------------------------------------

def test_an_empty_history_says_so(qtbot, history):
    tab = _tab(qtbot)
    assert tab._cards == []
    assert any("No completed sessions yet" in label.text() for label in tab.findChildren(hv.QLabel))
    assert tab.stat_sessions.value.text() == "0"


def test_the_page_shows_a_card_per_session_and_the_summary(qtbot, history):
    history += [_session(0, 8, 30, source="task", eventTitle="Chinese"), _session(0, 12, 45, violationCount=2)]
    tab = _tab(qtbot)
    assert len(tab._cards) == 2
    assert tab._cards[0].title_label.text() == "Focus session"                # newest first
    assert tab.stat_sessions.value.text() == "2" and tab.stat_violations.value.text() == "2"
    assert tab.stat_time.value.text() == "1h 15m" and tab.stat_clean.value.text() == "50%"
    assert tab._cards[0].violation_chip.text() == "2 violations"
    assert tab._cards[1].violation_chip.text() == "Clean"


def test_the_window_filter_hides_older_sessions(qtbot, history):
    history += [_session(20), _session(1)]
    tab = _tab(qtbot)
    assert len(tab._cards) == 1                       # 7 days by default
    tab._window_buttons[30].click()
    assert len(tab._cards) == 2
    tab._window_buttons[None].click()
    assert len(tab._cards) == 2


def test_the_kind_filter(qtbot, history):
    history += [_session(0, 8, source="task", eventTitle="T"), _session(0, 9, source="review", reviewProblemName="P"),
                _session(0, 10)]
    tab = _tab(qtbot)
    tab._kind_buttons["review"].click()
    assert [c.title_label.text() for c in tab._cards] == ["Review: P"]
    tab._kind_buttons["task"].click()
    assert [c.title_label.text() for c in tab._cards] == ["T"]
    tab._kind_buttons[None].click()
    assert len(tab._cards) == 3


def test_a_card_opens_to_its_details_and_closes_again(qtbot, history):
    history.append(_session(
        violationLog=[{"kind": "domain", "url": "https://youtube.com", "timestamp": datetime.now().isoformat(),
                       "durationSeconds": 30}],
        violationCount=1, endType="nuclear", reason="a call"))
    tab = _tab(qtbot)
    card = tab._cards[0]
    assert not card.expanded
    card.toggle()
    assert card.expanded
    text = " ".join(label.text() for label in card.findChildren(hv.QLabel))
    assert "a.exe" in text and "x.com" in text and "youtube.com" in text and "a call" in text
    card.toggle()
    assert not card.expanded


def test_show_more_adds_a_page_of_older_sessions(qtbot, history, monkeypatch):
    monkeypatch.setattr(hv, "PAGE_SIZE", 3)
    history += [_session(0, h) for h in range(8, 15)]    # 7 sessions today
    tab = _tab(qtbot)
    tab._shown = 3
    tab.refresh()
    tab.show()
    assert len(tab._cards) == 3 and tab._more_button.isVisible()
    assert "4 older" in tab._more_button.text()
    tab._more_button.click()
    assert len(tab._cards) == 6
    tab._more_button.click()
    assert len(tab._cards) == 7 and not tab._more_button.isVisible()


def test_the_page_never_writes_the_history_file(qtbot, history, monkeypatch):
    called = []
    for name in ("save", "append", "save_all", "add"):
        if hasattr(session_history, name):
            monkeypatch.setattr(session_history, name, lambda *a, _n=name, **k: called.append(_n))
    history.append(_session())
    tab = _tab(qtbot)
    tab._cards[0].toggle()
    tab.refresh()
    assert called == []
