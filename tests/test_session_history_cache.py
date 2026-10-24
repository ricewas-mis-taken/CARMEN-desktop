"""load_all() is called every second by the Tasks tab, so it keeps the parsed file
until the file changes -- and must still show every change."""
import builtins
import json
import os

import session_history


def _entry(n):
    return {"startTime": f"2026-01-0{n}T10:00:00", "endTime": f"2026-01-0{n}T11:00:00", "endType": "manual"}


def _count_reads(monkeypatch):
    reads = []
    real_open = builtins.open

    def counting_open(file, mode="r", *a, **kw):
        if str(file) == session_history.HISTORY_PATH and "r" in mode:
            reads.append(1)
        return real_open(file, mode, *a, **kw)

    monkeypatch.setattr(builtins, "open", counting_open)
    return reads


def test_unchanged_file_is_parsed_once(isolate_state, monkeypatch):
    session_history.append_entry(_entry(1))
    session_history.load_all()
    reads = _count_reads(monkeypatch)
    for _ in range(5):
        assert len(session_history.load_all()) == 1
    assert reads == []


def test_a_new_entry_shows_up(isolate_state):
    session_history.append_entry(_entry(1))
    assert len(session_history.load_all()) == 1
    session_history.append_entry(_entry(2))
    assert len(session_history.load_all()) == 2


def test_an_outside_edit_shows_up(isolate_state):
    session_history.append_entry(_entry(1))
    session_history.load_all()
    path = session_history.HISTORY_PATH
    with open(path, "w", encoding="utf-8") as f:
        json.dump([_entry(1), _entry(2), _entry(3)], f)
    assert len(session_history.load_all()) == 3


def test_callers_cannot_change_the_cached_list(isolate_state):
    session_history.append_entry(_entry(1))
    first = session_history.load_all()
    first.clear()
    assert len(session_history.load_all()) == 1


def test_a_deleted_file_reads_as_empty(isolate_state):
    session_history.append_entry(_entry(1))
    session_history.load_all()
    os.remove(session_history.HISTORY_PATH)
    assert session_history.load_all() == []
