"""Tripwire: while a test runs, no app module may still point at the owner's real
private/ folder. A path constant added later that the isolation fixtures forget
shows up here, instead of silently writing real data on the next full test run."""
import os
import sys

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REAL_PRIVATE = os.path.join(REPO, "private")

# Only ever read by the code under test.
READ_ONLY = {("auth_manager", "ENV_PATH"), ("sync_client", "ENV_PATH"), ("sync_server", "_ENV_PATH"),
             ("backup", "DATA_DIR")}  # backup only reads private/; it writes to ~/CarmenBackups
# Not data files: the log's handler is detached for the whole session, and
# sync_live._PRIVATE is only the base its (redirected) file constants were built from.
NOT_WRITTEN = {("calendar_log", "LOG_PATH"), ("sync_live", "_PRIVATE")}


def _own_modules():
    for name, module in list(sys.modules.items()):
        path = getattr(module, "__file__", None) or ""
        if module is None or not path.startswith(REPO) or "tests" in path.replace(REPO, ""):
            continue
        yield name, module


def test_every_data_path_is_redirected_during_tests():
    offenders = []
    for name, module in _own_modules():
        for attr, value in vars(module).items():
            if not isinstance(value, str) or (name, attr) in READ_ONLY | NOT_WRITTEN:
                continue
            if attr.isupper() and (attr.endswith(("_PATH", "_DIR", "PATH")) or attr.startswith("_PRIVATE")):
                if os.path.normcase(value).startswith(os.path.normcase(REAL_PRIVATE)):
                    offenders.append(f"{name}.{attr}")
    assert not offenders, f"still pointing at the real private/ folder: {offenders}"
