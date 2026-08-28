"""Shared fixtures. Every test that touches session_manager/config/
session_history must use the isolate_state fixture — those modules write to
hardcoded paths inside the repo (session_state.json, config.json,
session_history.json), not an injectable temp dir, so tests redirect those
module-level path constants to a pytest tmp_path instead, to guarantee real
user data is never touched by a test run."""
import copy

import pytest

import calendar_store
import config
import review_store
import session_history
import session_manager


@pytest.fixture
def isolate_config(tmp_path, monkeypatch):
    """config.json lives at a hardcoded path -- redirect it so a test that
    touches config.py, directly or indirectly (e.g. api_server.py's
    _require_token calling config.get_api_token()), never touches the real
    file or generates a real apiToken into it."""
    monkeypatch.setattr(config, "CONFIG_PATH", str(tmp_path / "config.json"))
    yield


@pytest.fixture
def isolate_state(isolate_config, tmp_path, monkeypatch):
    monkeypatch.setattr(session_manager, "STATE_PATH", str(tmp_path / "session_state.json"))
    monkeypatch.setattr(session_history, "HISTORY_PATH", str(tmp_path / "session_history.json"))

    # session_manager._state is a module-level dict mutated in place across
    # the whole process's life -- reset it to a clean default so one test's
    # session doesn't leak into the next.
    fresh_state = {
        "isActive": False,
        "startTime": None,
        "endTime": None,
        "lockMode": "soft",
        "processBlocklist": [],
        "domainWhitelist": [],
        "violationCount": 0,
        "violationLog": [],
        "lastAcceptableProcess": None,
        "domainWhitelistAdditions": [],
        "processBlocklistExceptions": [],
        "isPaused": False,
        "pausedAt": None,
        "frozenSecondsRemaining": None,
        "source": "manual",
        "eventId": None,
        "eventTitle": None,
        "reviewProblemName": None,
        "reviewSubjectName": None,
        "reviewProblemId": None,
        "isBurnout": False,
    }
    monkeypatch.setattr(session_manager, "_state", copy.deepcopy(fresh_state))
    monkeypatch.setattr(session_manager, "_open_violation_index", {"process": None, "domain": None})
    monkeypatch.setattr(session_manager, "_pending_natural_end", {"value": None})

    yield


@pytest.fixture
def isolate_calendar_db(tmp_path, monkeypatch):
    """calendar_store.py caches its sqlite3 connection in a module-level
    _conn global -- redirecting DB_PATH alone isn't enough, since a
    connection opened against the real calendar.db in an earlier test (or
    an earlier run within this process) would still be reused. Reset both
    so every test gets a fresh, isolated on-disk database."""
    monkeypatch.setattr(calendar_store, "DB_PATH", str(tmp_path / "calendar.db"))
    monkeypatch.setattr(calendar_store, "_conn", None)
    yield


@pytest.fixture
def isolate_review_db(isolate_calendar_db, tmp_path, monkeypatch):
    """review_store.py shares calendar_store's connection (see its module
    docstring) rather than opening one of its own, so isolating it also
    means resetting review_store's own module-level state: _schema_ready
    (or the fresh calendar.db from isolate_calendar_db would never get its
    review_* tables created) and _active_sessions (in-memory start/finish
    tracking, which must not leak between tests)."""
    monkeypatch.setattr(review_store, "_schema_ready", False)
    monkeypatch.setattr(review_store, "_active_sessions", {})
    monkeypatch.setattr(review_store, "PHOTOS_DIR", str(tmp_path / "review_photos"))
    yield
