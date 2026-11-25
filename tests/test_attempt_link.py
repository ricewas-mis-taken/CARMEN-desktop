"""Saving a session from the history as an attempt at a problem."""
import attempt_link
import review_store
import session_history


def _setup():
    topic = review_store.create_topic("USACO")
    subject = review_store.create_subject(topic["id"], "Complete Search", "#4A90D9")
    problem = review_store.create_problem(
        topic["id"], subject["id"], "Creating Strings", stars=1, description_type="text", description_text="x")
    return topic, subject, problem


def _first_attempt():
    return {"startTime": "2026-11-23T10:54:41", "endTime": "2026-11-23T11:20:41", "endType": "manual",
            "source": "review", "reviewProblemName": "First attempt", "sessionId": "abc", "violationLog": []}


def test_a_first_attempt_is_saved_onto_a_problem_and_marked(isolate_review_db):
    _topic, _subject, problem = _setup()
    entry = _first_attempt()
    session_history.append_entry(entry)
    assert attempt_link.linkable(entry)

    saved = attempt_link.link(entry, problem["id"], self_solved=True, shakiness=3)

    assert saved["reviewCount"] == 1
    assert review_store.list_sessions(problem["id"])[0]["durationSeconds"] == 26 * 60
    filed = session_history.load_all(include_beside=True)[0]
    assert filed["linkedProblemId"] == problem["id"] and filed["reviewProblemName"] == "Creating Strings"
    assert not attempt_link.linkable(filed)           # never saved twice
    assert attempt_link.link(filed, problem["id"]) is None


def test_a_plain_focus_session_can_be_saved_but_a_task_session_cannot(isolate_review_db):
    plain = {"startTime": "2026-11-23T09:00:00", "endTime": "2026-11-23T09:30:00", "source": None}
    assert attempt_link.linkable(plain)
    assert not attempt_link.linkable(dict(plain, source="task"))
    assert not attempt_link.linkable(dict(plain, source="review", reviewProblemName="Photoshoot"))


def test_a_missing_problem_saves_nothing(isolate_review_db):
    entry = _first_attempt()
    session_history.append_entry(entry)
    assert attempt_link.link(entry, 9999) is None
    assert "linkedProblemId" not in session_history.load_all(include_beside=True)[0]


def test_the_add_new_tab_saves_a_first_attempt_as_a_new_problem(qtbot, isolate_review_db):
    from qt_ui import attempt_link_dialog
    topic = review_store.create_topic("USACO")
    review_store.create_subject(topic["id"], "Complete Search", "#4A90D9")
    entry = _first_attempt()
    session_history.append_entry(entry)
    saved = []
    dialog = attempt_link_dialog.AttemptLinkDialog(entry, on_saved=saved.append)
    qtbot.addWidget(dialog)
    dialog._new_tab.click()
    assert dialog._stack.currentWidget() is dialog._new
    dialog._new.name.setText("Creating Strings")
    dialog._save()
    assert saved and saved[0]["name"] == "Creating Strings" and saved[0]["reviewCount"] == 1
    assert session_history.load_all(include_beside=True)[0]["reviewProblemName"] == "Creating Strings"


def test_the_add_to_existing_tab_saves_onto_a_problem_already_on_the_list(qtbot, isolate_review_db):
    from qt_ui import attempt_link_dialog
    _topic, _subject, problem = _setup()
    entry = _first_attempt()
    session_history.append_entry(entry)
    saved = []
    dialog = attempt_link_dialog.AttemptLinkDialog(entry, on_saved=saved.append)
    qtbot.addWidget(dialog)
    assert dialog._stack.currentWidget() is dialog._existing           # opens on this tab
    dialog._existing.picker.problem.setCurrentIndex(dialog._existing.picker.problem.findData(problem["id"]))
    dialog._save()
    assert saved[0]["id"] == problem["id"]


def test_each_tab_asks_for_what_it_needs(qtbot, isolate_review_db):
    from qt_ui import attempt_link_dialog
    topic = review_store.create_topic("USACO")
    review_store.create_subject(topic["id"], "Complete Search", "#4A90D9")
    dialog = attempt_link_dialog.AttemptLinkDialog(_first_attempt())
    qtbot.addWidget(dialog)
    dialog._save()
    assert "Pick a problem" in dialog._status.text()
    dialog._new_tab.click()
    assert dialog._status.text() == ""
    dialog._save()
    assert "Name the new problem" in dialog._status.text()


def test_saving_is_offered_only_on_right_click(qtbot, isolate_review_db, monkeypatch):
    from PySide6.QtGui import QContextMenuEvent
    from PySide6.QtWidgets import QMenu, QPushButton
    from PySide6.QtCore import QPoint
    from qt_ui import history_viewer
    seen = []

    class FakeMenu(QMenu):
        def exec(self, pos=None):          # never opens a real, blocking menu
            seen.append([a.text() for a in self.actions()])

    monkeypatch.setattr(history_viewer, "QMenu", FakeMenu)

    def right_click(card):
        card.contextMenuEvent(QContextMenuEvent(QContextMenuEvent.Mouse, QPoint(1, 1), QPoint(1, 1)))

    card = history_viewer._SessionCard(_first_attempt())
    qtbot.addWidget(card)
    card.toggle()
    assert not any("Save as an attempt" in b.text() for b in card.findChildren(QPushButton))   # no button
    right_click(card)
    assert any("Save as an attempt" in text for text in seen[-1])

    done = history_viewer._SessionCard(dict(_first_attempt(), linkedProblemId=3, reviewProblemName="X"))
    qtbot.addWidget(done)
    seen.clear()
    right_click(done)
    assert seen == []                                    # already saved: nothing to offer


def test_a_saved_attempt_reaches_the_other_computer(isolate_review_db):
    import sync_live
    entry = _first_attempt()
    session_history.append_entry(entry)
    watermark = max(e["endTime"] for e in session_history.load_all(include_beside=True))
    assert session_history.entries_ended_after(watermark) == []

    session_history.update_entry(session_history.entry_id(entry), {"linkedProblemId": 5, "reviewProblemName": "Creating Strings"})
    changed = session_history.entries_ended_after(watermark)
    assert [e["reviewProblemName"] for e in changed] == ["Creating Strings"]

    wire = sync_live.history_entry_from_wire(sync_live.history_entry_to_wire(changed[0]))
    stale = dict(_first_attempt())                     # what the other computer still has
    import json, os
    with open(session_history.HISTORY_PATH, "w", encoding="utf-8") as f:
        json.dump([stale], f)
    session_history._cache["key"] = None
    assert session_history.merge_remote([wire]) == 1
    assert session_history.load_all(include_beside=True)[0]["reviewProblemName"] == "Creating Strings"
    assert session_history.merge_remote([wire]) == 0   # nothing newer the second time
