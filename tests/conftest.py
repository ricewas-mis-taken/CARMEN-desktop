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
import device_id
import enforcer
import review_store
import board_store
import tasks_store
import auth_manager
import sync_client
import sync_cloud
import sync_photos
import screentime_store
import session_history
import session_manager
import sync_trigger


@pytest.fixture(autouse=True)
def disable_sync_trigger(monkeypatch):
    """sync_trigger.note_change() (Phase 4 Part 1, push-on-change) is now
    called from many tasks_store/board_store/calendar_store/review_store
    write paths. Without this, any test exercising those write paths
    would hit auth_manager.is_logged_in() -- which can reach the real
    Windows Credential Manager (and, on a cache miss, real Supabase) --
    during an ordinary automated test run. No-op it globally; tests that
    specifically exercise sync_trigger's own debounce/enable/disable
    behavior override this themselves."""
    monkeypatch.setattr(sync_trigger, "note_change", lambda: None)


@pytest.fixture(scope="session", autouse=True)
def no_real_log_files():
    """The app's two file loggers (calendar_errors.log, api_requests.log) write
    to private/ next to the real data. Every test's logged errors and every
    Flask test-client request landed in the owner's real logs when the suite
    ran from the real checkout. Detach the file handlers for the whole session."""
    import logging

    detached = []
    for name in ("carmen_calendar", "carmen_api_requests"):
        log = logging.getLogger(name)
        for handler in list(log.handlers):
            if isinstance(handler, logging.FileHandler):
                log.removeHandler(handler)
                detached.append((log, handler))
        log.addHandler(logging.NullHandler())
    yield
    for log, handler in detached:
        log.addHandler(handler)


class _NullNotifier:
    """Stands in for WinRT's ToastNotifier so the REAL show_toast() code runs
    in tests but nothing ever appears on the owner's screen."""

    def __init__(self):
        self.shown = []

    def show(self, toast):
        self.shown.append(toast)


@pytest.fixture(autouse=True)
def no_real_toasts(monkeypatch):
    """A scheduler test that fires an event start used to call the real
    calendar_toast.show_toast -- an "Exam is starting now." desktop
    notification on the owner's screen every time the suite ran. Tests that
    want to inspect toasts set their own _notifier and win (they run after)."""
    try:
        import calendar_toast
    except Exception:
        yield
        return
    if hasattr(calendar_toast, "_notifier"):
        monkeypatch.setattr(calendar_toast, "_notifier", _NullNotifier())
    yield


@pytest.fixture(autouse=True)
def no_real_installed_app_scans(monkeypatch):
    """The Qt pickers/editors call installed_apps.list_installed_apps(), which
    walks the Start Menu and shells out to PowerShell -- a slow, real scan of
    the machine the suite runs on, repeated in a dozen tests. Default to an
    empty list; tests that care patch their own."""
    try:
        import installed_apps
    except Exception:
        return
    if hasattr(installed_apps, "list_installed_apps"):
        monkeypatch.setattr(installed_apps, "list_installed_apps", lambda: [])


@pytest.fixture(autouse=True)
def isolate_real_data_paths(tmp_path_factory, monkeypatch):
    """Safety net: every module whose data file path is a module-level
    constant defaulting to <repo>/private/... is pointed at a throwaway dir
    for EVERY test, so a test that forgets its own isolation fixture can no
    longer create or mutate the real calendar.db, screentime.json,
    config.json, device_id.txt or active_review.json (running the suite in
    the owner's real checkout used to write phantom discord.exe screen time
    into the real screentime.json, among other things). Tests that need a
    specific location still override these with their own monkeypatch --
    those run after this autouse fixture."""
    base = tmp_path_factory.mktemp("isolated_private")
    monkeypatch.setattr(config, "CONFIG_PATH", str(base / "config.json"))
    monkeypatch.setattr(screentime_store, "STATE_PATH", str(base / "screentime.json"))
    monkeypatch.setattr(screentime_store, "_data", {})
    monkeypatch.setattr(device_id, "DEVICE_ID_PATH", str(base / "device_id.txt"))
    monkeypatch.setattr(device_id, "_cached_id", None)
    monkeypatch.setattr(calendar_store, "DB_PATH", str(base / "calendar.db"))
    monkeypatch.setattr(calendar_store, "_conn", None)
    monkeypatch.setattr(review_store, "_schema_ready", False)
    monkeypatch.setattr(review_store, "_active_sessions", {})
    monkeypatch.setattr(review_store, "_active_sessions_loaded", True)
    monkeypatch.setattr(review_store, "ACTIVE_SESSION_PATH", str(base / "active_review.json"))
    monkeypatch.setattr(review_store, "PHOTOS_DIR", str(base / "review_photos"))
    # sync_client's account-owner marker and watermark: a test that calls
    # sync_now() without isolating them used to write a fake user id into the
    # real private/sync_owner.txt, which would then lock the real app out of
    # its own account.
    monkeypatch.setattr(sync_client, "SYNC_OWNER_PATH", str(base / "sync_owner.txt"))
    monkeypatch.setattr(sync_client, "LAST_SYNC_PATH", str(base / "last_sync.txt"))
    monkeypatch.setattr(sync_client, "_cached_last_sync", None)
    monkeypatch.setattr(sync_client, "PULL_CURSOR_PATH", str(base / "pull_cursor.txt"))
    # A fixed fake project, so no test can ever reach the real Supabase URL
    # from private/.env even if it forgets to mock the transport.
    monkeypatch.setattr(auth_manager, "SUPABASE_URL", "https://test-project.supabase.co")
    monkeypatch.setattr(auth_manager, "SUPABASE_PUBLISHABLE_KEY", "test-publishable-key")
    monkeypatch.setattr(sync_photos, "_uploaded", None)
    monkeypatch.setattr(sync_photos, "UPLOADED_PATH", str(base / "uploaded_photos.json"))
    monkeypatch.setattr(sync_cloud, "_verified", False)
    monkeypatch.setattr(sync_photos, "_missing_until", {})
    monkeypatch.setattr(tasks_store, "TASKS_PATH", str(base / "tasks.json"))
    monkeypatch.setattr(board_store, "BOARD_PATH", str(base / "board.json"))
    monkeypatch.setattr(board_store, "PHOTOS_DIR", str(base / "board_photos"))
    yield


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
        "blockedBrowserProfiles": [],
        "hideTaskbarBadges": False,
        "stopTaskbarFlashing": False,
        "pomodoro": None,
        "isBreak": False,
        "parkedSessions": [],
    }
    monkeypatch.setattr(session_manager, "_state", copy.deepcopy(fresh_state))
    monkeypatch.setattr(session_manager, "_open_violation_index", {"process": None, "domain": None})
    monkeypatch.setattr(session_manager, "_pending_natural_end", {"value": None})
    monkeypatch.setattr(session_manager, "_pending_phase_change", {"value": None})

    # enforcer.record_violation_deduped()'s own cross-path cooldown registry
    # is module-level, real-wall-clock-keyed state too -- without resetting
    # it here, one test recording a violation for "discord.exe" (the common
    # example process name across these tests) can silently suppress another
    # test's own violation a few seconds later in the same run, since
    # nothing else ever clears it between tests.
    monkeypatch.setattr(enforcer, "_last_recorded_violation", {})

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
    monkeypatch.setattr(device_id, "DEVICE_ID_PATH", str(tmp_path / "device_id.txt"))
    monkeypatch.setattr(device_id, "_cached_id", None)
    yield


@pytest.fixture
def isolate_review_db(isolate_calendar_db, tmp_path, monkeypatch):
    """review_store.py shares calendar_store's connection (see its module
    docstring) rather than opening one of its own, so isolating it also
    means resetting review_store's own module-level state: _schema_ready
    (or the fresh calendar.db from isolate_calendar_db would never get its
    review_* tables created) and _active_sessions (in-memory start/finish
    tracking, which must not leak between tests). _active_sessions_loaded
    is forced True so _ensure_active_sessions_loaded() never attempts a
    real disk read even on the first test in a fresh process; redirecting
    ACTIVE_SESSION_PATH too is defense in depth in case anything ever saves."""
    monkeypatch.setattr(review_store, "_schema_ready", False)
    monkeypatch.setattr(review_store, "_active_sessions", {})
    monkeypatch.setattr(review_store, "_active_sessions_loaded", True)
    monkeypatch.setattr(review_store, "ACTIVE_SESSION_PATH", str(tmp_path / "active_review.json"))
    monkeypatch.setattr(review_store, "PHOTOS_DIR", str(tmp_path / "review_photos"))
    yield
