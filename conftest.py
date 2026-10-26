"""Repo-root fixtures: they apply to tests/ AND mac-os/tests/, which have their own
conftest files and used to be left out of the isolation tests/conftest.py provides.

Running `pytest` from the repo root collects both folders. The mac-os tests logged
their deliberate "boom" tracebacks into the real private/calendar_errors.log, and any
test that reached multi_device/session_manager/session_history/daily_summary_store
without its own isolation wrote into the owner's real private/ files.
"""
import logging
import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Before ANY app module is imported (this file is loaded first): every data path in
# the app is built from paths.PRIVATE, so pointing it at a throwaway folder means no
# test can reach the owner's real private/ folder, whatever it forgets to patch.
os.environ.setdefault("CARMEN_PRIVATE_DIR", tempfile.mkdtemp(prefix="carmen-test-private-"))


@pytest.fixture(scope="session", autouse=True)
def no_real_log_files():
    """Detach the app's file loggers (calendar_errors.log, api_requests.log) for the
    whole session so nothing a test logs lands in the real logs."""
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


@pytest.fixture(autouse=True)
def isolate_root_private_paths(tmp_path_factory, monkeypatch):
    """Points the data-file constants no other autouse fixture covers at a throwaway
    dir. Tests that need a specific location still override them afterwards."""
    import config
    import daily_summary_store
    import multi_device
    import session_history
    import session_manager
    import ui_watchdog

    base = tmp_path_factory.mktemp("root_isolated_private")
    monkeypatch.setattr(config, "CONFIG_PATH", str(base / "config.json"))
    monkeypatch.setattr(multi_device, "SEEN_PATH", str(base / "other_device_seen.txt"))
    monkeypatch.setattr(session_manager, "STATE_PATH", str(base / "session_state.json"))
    monkeypatch.setattr(session_history, "HISTORY_PATH", str(base / "session_history.json"))
    monkeypatch.setattr(daily_summary_store, "SUMMARY_PATH", str(base / "daily_summaries.json"))
    monkeypatch.setattr(ui_watchdog, "TRACE_PATH", str(base / "hang_trace.txt"))
    yield
