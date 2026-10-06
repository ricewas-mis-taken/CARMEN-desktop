"""Tests for sync_client.py. Never talks to a real network or a real
sync_server -- httpx.Client is monkeypatched to a MockTransport backed by
a small in-memory fake modeling sync_server's own last-write-wins upsert
and "changed since" semantics, so these tests exercise the *client's*
gather/push/pull/apply logic (including review table FK-by-sync_id
translation) without needing sync_server running.

Two isolated "devices" (isolate_device fixture, parametrized via a
suffix) are used for the round-trip tests, sharing one fake server dict,
to catch bugs that a single-device test would hide -- e.g. FK translation
only breaks once a device is applying a pulled row it doesn't already
have locally.
"""
import board_store
import calendar_store
import device_id
import httpx
import pytest
import review_store
import tasks_store

import auth_manager
import sync_client

_RealClient = httpx.Client


@pytest.fixture
def isolate_device(tmp_path, monkeypatch):
    def _make(suffix):
        base = tmp_path / suffix
        base.mkdir(exist_ok=True)
        monkeypatch.setattr(tasks_store, "TASKS_PATH", str(base / "tasks.json"))
        monkeypatch.setattr(board_store, "BOARD_PATH", str(base / "board.json"))
        monkeypatch.setattr(calendar_store, "DB_PATH", str(base / "calendar.db"))
        monkeypatch.setattr(calendar_store, "_conn", None)
        monkeypatch.setattr(review_store, "_schema_ready", False)
        monkeypatch.setattr(review_store, "_active_sessions", {})
        monkeypatch.setattr(review_store, "PHOTOS_DIR", str(base / "review_photos"))
        monkeypatch.setattr(device_id, "DEVICE_ID_PATH", str(base / "device_id.txt"))
        monkeypatch.setattr(device_id, "_cached_id", None)
        monkeypatch.setattr(sync_client, "LAST_SYNC_PATH", str(base / "last_sync.txt"))
        monkeypatch.setattr(sync_client, "_cached_last_sync", None)
    return _make


@pytest.fixture
def fake_logged_in(monkeypatch):
    monkeypatch.setattr(auth_manager, "is_logged_in", lambda: True)
    monkeypatch.setattr(auth_manager, "get_access_token", lambda: "fake-token")
    monkeypatch.setattr(auth_manager, "get_current_user", lambda: {"id": "user-1", "email": "a@example.com"})


@pytest.fixture
def fake_server(monkeypatch):
    """{(table_name, sync_id): record} with sync_server's own last-write-
    wins upsert and updated_at > since pull filtering."""
    store = {}

    def handler(request):
        if request.url.path == "/sync/push":
            import json as _json
            records = _json.loads(request.content)
            accepted, skipped = [], []
            for record in records:
                key = (record["table_name"], record["sync_id"])
                existing = store.get(key)
                if existing and existing["updated_at"] >= record["updated_at"]:
                    skipped.append(key)
                    continue
                store[key] = record
                accepted.append(key)
            return httpx.Response(200, json={"accepted": accepted, "skipped": skipped})
        if request.url.path == "/sync/pull":
            since = request.url.params.get("since")
            result = [r for r in store.values() if not since or r["updated_at"] > since]
            return httpx.Response(200, json=result)
        raise AssertionError(f"unexpected request: {request.url}")

    def install():
        monkeypatch.setattr(
            sync_client.httpx, "Client",
            lambda **kwargs: _RealClient(transport=httpx.MockTransport(handler)),
        )

    install()
    return store




def test_disabling_focus_profile_on_one_device_reaches_the_other(isolate_device, fake_logged_in, fake_server):
    isolate_device("a")
    ev = {"title": "Study", "start": "2030-06-10T09:00:00", "end": "2030-06-10T10:00:00",
          "focusProfile": {"enabled": True, "lockMode": "hard", "processBlocklist": ["game.exe"],
                           "domainWhitelist": [], "warningMinutes": 5}}
    event_id = calendar_store.save_event(ev)
    assert sync_client.sync_now().success

    isolate_device("b")
    assert sync_client.sync_now().success
    assert calendar_store.get_event(event_id)["focusProfile"]["enabled"] is True

    # user turns the focus lock OFF for this event on device A
    isolate_device("a")
    ev["id"] = event_id
    ev["focusProfile"] = None
    calendar_store.save_event(ev)
    print("A after disable:", calendar_store.get_event(event_id)["focusProfile"])
    assert sync_client.sync_now().success

    isolate_device("b")
    assert sync_client.sync_now().success
    b_profile = calendar_store.get_event(event_id)["focusProfile"]
    print("B after sync:", b_profile)
    assert b_profile is None or b_profile["enabled"] is False
