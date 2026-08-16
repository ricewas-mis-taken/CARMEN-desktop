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


def test_sync_now_reports_not_logged_in(isolate_device, monkeypatch):
    isolate_device("a")
    monkeypatch.setattr(auth_manager, "is_logged_in", lambda: False)
    result = sync_client.sync_now()
    assert result.success is False
    assert result.not_logged_in is True


def test_sync_now_handles_unreachable_server(isolate_device, fake_logged_in, monkeypatch):
    isolate_device("a")

    def handler(request):
        raise httpx.ConnectError("boom", request=request)

    monkeypatch.setattr(
        sync_client.httpx, "Client",
        lambda **kwargs: _RealClient(transport=httpx.MockTransport(handler)),
    )
    result = sync_client.sync_now()
    assert result.success is False
    assert result.not_logged_in is False
    assert "reach" in result.error.lower()


def test_push_then_pull_round_trip_for_task_and_event(isolate_device, fake_logged_in, fake_server):
    isolate_device("a")
    task = tasks_store.create_task({"name": "Alpha", "color": "#111111"})
    calendar_store.save_event({"title": "Standup", "start": "2026-08-17T09:00:00", "end": "2026-08-17T09:15:00",
                                "reminderOffsets": [10]})

    result_a = sync_client.sync_now()
    assert result_a.success is True
    assert result_a.pushed >= 2  # task + event (+ reminder)

    isolate_device("b")
    result_b = sync_client.sync_now()
    assert result_b.success is True
    assert result_b.pulled >= 2

    pulled_tasks = tasks_store.load_tasks()
    assert len(pulled_tasks) == 1
    assert pulled_tasks[0]["id"] == task["id"]
    assert pulled_tasks[0]["name"] == "Alpha"

    pulled_events = calendar_store.list_events()
    assert len(pulled_events) == 1
    assert pulled_events[0]["title"] == "Standup"
    assert pulled_events[0]["reminderOffsets"] == [10]


def test_review_chain_fk_translation_across_devices(isolate_device, fake_logged_in, fake_server):
    isolate_device("a")
    topic = review_store.create_topic("Math")
    subject = review_store.create_subject(topic["id"], "Algebra", "#ABCDEF")
    problem = review_store.create_problem(topic["id"], subject["id"], "Solve for x", 3, "text",
                                           description_text="x + 1 = 2")
    token = review_store.start_review(problem["id"])
    review_store.finish_review(token, self_solved=True, shakiness=1, duration_seconds=42)

    result_a = sync_client.sync_now()
    assert result_a.success is True
    assert result_a.pushed >= 4  # topic + subject + problem + session

    isolate_device("b")
    result_b = sync_client.sync_now()
    assert result_b.success is True

    topics = review_store.list_topics()
    assert len(topics) == 1
    b_topic = topics[0]
    subjects = review_store.list_subjects(b_topic["id"])
    assert len(subjects) == 1
    b_subject = subjects[0]
    problems = review_store.list_problems(b_topic["id"], due_only=False)
    assert len(problems) == 1
    b_problem = problems[0]
    assert b_problem["subjectId"] == b_subject["id"]
    assert b_problem["name"] == "Solve for x"
    assert b_problem["reviewCount"] == 1

    sessions = review_store.list_sessions(b_problem["id"])
    assert len(sessions) == 1
    assert sessions[0]["durationSeconds"] == 42


def test_last_write_wins_skips_older_incoming_task(isolate_device, fake_logged_in, fake_server):
    isolate_device("a")
    task = tasks_store.create_task({"name": "Original", "color": "#222222"})
    sync_client.sync_now()

    isolate_device("b")
    sync_client.sync_now()
    tasks_store.update_task(task["id"], {"name": "Renamed on B"})
    result = sync_client.sync_now()
    assert result.pushed == 1

    isolate_device("a")
    sync_client.sync_now()
    local = tasks_store.get_task(task["id"])
    assert local["name"] == "Renamed on B"
