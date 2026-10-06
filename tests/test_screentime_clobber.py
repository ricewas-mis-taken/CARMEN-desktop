"""screentime.json holds the user's whole Screen Time history. An unreadable
file (briefly locked on Windows, or corrupt) must never be treated as 'empty'
and then overwritten by the next flush."""
import builtins
import json
from datetime import datetime

import pytest

import screentime_store

DAY = datetime(2026, 1, 1)


@pytest.fixture
def store(tmp_path, monkeypatch):
    path = tmp_path / "screentime.json"
    monkeypatch.setattr(screentime_store, "STATE_PATH", str(path))
    monkeypatch.setattr(screentime_store, "_data", {})
    monkeypatch.setattr(screentime_store, "_last_flush", 0.0)
    return path


def _history():
    return {"2025-12-31": {"apps": {"code.exe": 7200}, "domains": {}}}


def test_transient_read_error_does_not_wipe_history(store, monkeypatch):
    store.write_text(json.dumps(_history()), encoding="utf-8")
    real_open = builtins.open

    def locked_open(file, mode="r", *a, **kw):
        if str(file) == str(store) and "r" in mode:
            raise PermissionError("file is locked")
        return real_open(file, mode, *a, **kw)

    monkeypatch.setattr(builtins, "open", locked_open)
    screentime_store._load()
    screentime_store.add_app_seconds("chrome.exe", 5, when=DAY)
    screentime_store.flush()
    monkeypatch.setattr(builtins, "open", real_open)

    assert json.loads(store.read_text(encoding="utf-8")) == _history()  # untouched while unreadable
    screentime_store.flush()  # lock released: merges in-memory seconds with the real history
    on_disk = json.loads(store.read_text(encoding="utf-8"))
    assert on_disk["2025-12-31"]["apps"] == {"code.exe": 7200}
    assert on_disk["2026-01-01"]["apps"] == {"chrome.exe": 5}


def test_corrupt_file_is_preserved_not_silently_overwritten(store):
    store.write_text('{"2025-12-31": {"apps": {"code.exe": 72', encoding="utf-8")  # truncated
    screentime_store._load()
    screentime_store.add_app_seconds("chrome.exe", 5, when=DAY)
    screentime_store.flush()
    backups = list(store.parent.glob("screentime.json.corrupt*"))
    assert len(backups) == 1
    assert backups[0].read_text(encoding="utf-8").startswith('{"2025-12-31"')


def test_non_object_json_does_not_break_tracking(store):
    store.write_text("[1, 2, 3]", encoding="utf-8")
    screentime_store._load()
    screentime_store.add_app_seconds("chrome.exe", 5, when=DAY)
    assert screentime_store.get_day("2026-01-01")["apps"] == {"chrome.exe": 5}
