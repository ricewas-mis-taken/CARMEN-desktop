"""append_entry() treated an unreadable/corrupt session_history.json as an
empty history and then overwrote the file with just the one new entry,
destroying every earlier session (tasks_store/board_store already refuse to
do this -- see TasksLoadError)."""
import builtins
import json

import session_history


def _entry(n):
    return {"startTime": f"2026-01-0{n}T10:00:00", "endTime": f"2026-01-0{n}T11:00:00", "endType": "manual"}


def test_transient_read_error_does_not_wipe_history(isolate_state, monkeypatch):
    path = session_history.HISTORY_PATH
    for n in (1, 2, 3):
        session_history.append_entry(_entry(n))
    before = open(path, encoding="utf-8").read()

    real_open = builtins.open
    state = {"failed": False}

    def flaky_open(file, mode="r", *a, **kw):
        if str(file) == path and "r" in mode and not state["failed"]:
            state["failed"] = True
            raise PermissionError("file locked by another process")
        return real_open(file, mode, *a, **kw)

    with monkeypatch.context() as m:
        m.setattr(builtins, "open", flaky_open)
        session_history.append_entry(_entry(4))

    assert len(json.loads(open(path, encoding="utf-8").read())) >= 3, "earlier sessions were wiped"


def test_corrupt_file_is_preserved_not_destroyed(isolate_state):
    path = session_history.HISTORY_PATH
    open(path, "w", encoding="utf-8").write('[{"startTime": "2026-01-01T10:00:00", "endType": "manual"}, {"trunc')
    session_history.append_entry(_entry(2))
    import glob
    backups = glob.glob(path + ".corrupt*")
    assert backups, "corrupt history was overwritten with no backup"
    assert "trunc" in open(backups[0], encoding="utf-8").read()
    assert len(session_history.load_all()) == 1
