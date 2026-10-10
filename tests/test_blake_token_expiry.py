"""Supabase access tokens expire (1h by default). sync_server answers 401 for an
expired one. sync_client must refresh and retry instead of failing until the
process restarts."""
import httpx
import pytest

import auth_manager
import board_store
import calendar_store
import device_id
import review_store
import sync_client
import tasks_store
from fake_supabase import FakeSupabase

_RealClient = httpx.Client


@pytest.fixture
def device(tmp_path, monkeypatch):
    monkeypatch.setattr(tasks_store, "TASKS_PATH", str(tmp_path / "tasks.json"))
    monkeypatch.setattr(board_store, "BOARD_PATH", str(tmp_path / "board.json"))
    monkeypatch.setattr(calendar_store, "DB_PATH", str(tmp_path / "calendar.db"))
    monkeypatch.setattr(calendar_store, "_conn", None)
    monkeypatch.setattr(review_store, "_schema_ready", False)
    monkeypatch.setattr(review_store, "_active_sessions", {})
    monkeypatch.setattr(review_store, "PHOTOS_DIR", str(tmp_path / "review_photos"))
    monkeypatch.setattr(device_id, "DEVICE_ID_PATH", str(tmp_path / "device_id.txt"))
    monkeypatch.setattr(device_id, "_cached_id", None)
    monkeypatch.setattr(sync_client, "LAST_SYNC_PATH", str(tmp_path / "last_sync.txt"))
    monkeypatch.setattr(sync_client, "_cached_last_sync", None)
    monkeypatch.setattr(sync_client, "PULL_CURSOR_PATH", str(tmp_path / "pull_cursor.txt"))


def test_sync_recovers_after_access_token_expires(device, monkeypatch):
    refresh_calls = []

    # auth_manager state after the process has been up for > 1 hour: an in-memory
    # access token that the server now rejects, and a valid refresh token in the keyring.
    monkeypatch.setattr(auth_manager, "_access_token", "expired-token")
    monkeypatch.setattr(auth_manager, "_current_user", {"id": "u", "email": "e@x"})

    def fake_refresh():
        refresh_calls.append(1)
        auth_manager._access_token = "fresh-token"
        return True

    monkeypatch.setattr(auth_manager, "_refresh_session", fake_refresh)

    FakeSupabase(user_id="u", token="fresh-token").install(monkeypatch, sync_client)
    tasks_store.create_task({"name": "Alpha", "color": "#111111"})

    first = sync_client.sync_now()
    second = sync_client.sync_now()
    print("first:", first)
    print("second:", second)
    print("refresh attempts:", len(refresh_calls))
    assert first.success or second.success
