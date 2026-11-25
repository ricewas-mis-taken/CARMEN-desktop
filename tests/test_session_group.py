"""Reviews and timers grouped under the Pomodoro they happened during."""
import session_group
import session_history


def _pomodoro():
    return {"startTime": "2026-11-23T10:00:00", "endTime": "2026-11-23T11:00:00", "source": "task",
            "eventId": "t1", "eventTitle": "usaco", "sessionId": "pomo", "pomodoro": {"currentCycle": 1, "totalCycles": 4}}


def _beside():
    return {"startTime": "2026-11-23T10:10:00", "endTime": "2026-11-23T10:25:00", "source": "review",
            "eventId": "t1", "reviewProblemName": "Photoshoot", "beside": True, "sessionId": "beside-1"}


def _earlier_review():                      # began before the Pomodoro, ran on its own
    return {"startTime": "2026-11-23T09:40:00", "endTime": "2026-11-23T09:58:00", "source": "review",
            "eventId": "t1", "reviewProblemName": "Out of Place", "sessionId": "own-1"}


def test_a_review_timed_beside_a_pomodoro_is_grouped_under_it_by_time():
    everything = [_pomodoro(), _beside(), _earlier_review()]
    assert [m["sessionId"] for m in session_group.members_of(_pomodoro(), everything)] == ["beside-1"]
    assert session_group.parent_of(_beside(), everything)["sessionId"] == "pomo"
    assert session_group.parent_of(_earlier_review(), everything) is None


def test_an_earlier_review_can_be_linked_by_hand_and_unlinked(isolate_state):
    for entry in (_pomodoro(), _beside(), _earlier_review()):
        session_history.append_entry(entry)
    everything = session_history.load_all(include_beside=True)
    review = next(e for e in everything if e["sessionId"] == "own-1")
    parent = next(e for e in everything if e["sessionId"] == "pomo")
    assert parent in session_group.candidates(review, everything)

    assert session_group.link(review, parent)
    everything = session_history.load_all(include_beside=True)
    assert {m["sessionId"] for m in session_group.members_of(parent, everything)} == {"beside-1", "own-1"}

    linked = next(e for e in everything if e["sessionId"] == "own-1")
    assert session_group.unlink(linked)
    everything = session_history.load_all(include_beside=True)
    assert {m["sessionId"] for m in session_group.members_of(parent, everything)} == {"beside-1"}


def test_a_task_session_cannot_be_grouped_under_another():
    assert not session_group.groupable(_pomodoro())
    assert not session_group.link(_pomodoro(), _pomodoro())


def test_the_card_lists_what_is_inside_a_pomodoro(qtbot, isolate_state):
    from PySide6.QtWidgets import QLabel
    from qt_ui import history_viewer
    for entry in (_pomodoro(), _beside()):
        session_history.append_entry(entry)
    card = history_viewer._SessionCard(_pomodoro())
    qtbot.addWidget(card)
    card.toggle()
    text = " ".join(label.text() for label in card.findChildren(QLabel))
    assert "Inside this session" in text and "Photoshoot" in text


def test_the_history_page_redraws_after_a_session_is_linked(qtbot, isolate_state):
    from qt_ui import history_viewer
    for entry in (_pomodoro(), _earlier_review()):
        session_history.append_entry(entry)
    tab = history_viewer.HistoryTab()
    qtbot.addWidget(tab)
    tab.refresh()
    before = tab._signature
    session_history.update_entry("own-1", {"groupedUnder": "pomo"})
    tab.refresh()
    assert tab._signature != before
