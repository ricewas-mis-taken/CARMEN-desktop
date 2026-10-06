"""Regression: no test may run against the real <repo>/private data files by
default (see conftest.isolate_real_data_paths)."""
import os

import calendar_store
import config
import device_id
import review_store
import screentime_store

REAL_PRIVATE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "private")


def _inside_private(path):
    return os.path.abspath(path).startswith(os.path.abspath(REAL_PRIVATE) + os.sep)


def test_no_default_data_path_points_into_real_private():
    for name, path in {
        "config.CONFIG_PATH": config.CONFIG_PATH,
        "screentime_store.STATE_PATH": screentime_store.STATE_PATH,
        "device_id.DEVICE_ID_PATH": device_id.DEVICE_ID_PATH,
        "calendar_store.DB_PATH": calendar_store.DB_PATH,
        "review_store.ACTIVE_SESSION_PATH": review_store.ACTIVE_SESSION_PATH,
    }.items():
        assert not _inside_private(path), f"{name} points at the real data dir: {path}"
