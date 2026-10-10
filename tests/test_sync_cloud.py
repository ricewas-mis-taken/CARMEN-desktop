"""Direct-to-Supabase sync: server-stamped pull cursor (wrong device clocks),
photo transfer, and clear errors. Uses tests/fake_supabase.py -- no network."""
import inspect
import os
from datetime import datetime, timedelta, timezone

import httpx
import pytest

import auth_manager
import board_store
import calendar_store
import review_store
import sync_client
import tasks_store
from fake_supabase import FakeSupabase

from test_sync_client import fake_logged_in, isolate_device  # noqa: F401  (shared fixtures)


@pytest.fixture
def server(monkeypatch):
    s = FakeSupabase(user_id="user-1")
    s.install(monkeypatch, sync_client)
    return s


def _task_record(sync_id, name, updated_at):
    return {
        "table_name": "tasks", "sync_id": sync_id, "device_id": "other", "is_deleted": False,
        "updated_at": updated_at.isoformat(),
        "data": {"name": name, "color": "#123456"},
    }


def test_row_from_a_device_with_a_slow_clock_still_reaches_other_devices(isolate_device, fake_logged_in, server):
    """The old pull compared the writing device's clock to the reader's last
    sync time, so a row stamped before that time was never delivered."""
    isolate_device("a")
    assert sync_client.sync_now().success
    server["tasks", "late-1"] = _task_record(
        "late-1", "From a slow clock", datetime.now(timezone.utc) - timedelta(days=3)
    )
    result = sync_client.sync_now()
    assert result.success and result.pulled == 1
    assert [t["name"] for t in tasks_store.load_tasks()] == ["From a slow clock"]


def test_row_from_a_device_with_a_fast_clock_cannot_win_every_later_edit(server):
    server["tasks", "fast-1"] = _task_record(
        "fast-1", "Typed on a fast clock", datetime.now(timezone.utc) + timedelta(days=30)
    )
    stored = datetime.fromisoformat(server.rows[("tasks", "fast-1")]["updated_at"])
    assert stored <= server.clock  # clamped to the server's "now"
    server["tasks", "fast-1"] = _task_record(
        "fast-1", "Fixed afterwards", datetime.now(timezone.utc) + timedelta(seconds=5)
    )
    assert server.rows[("tasks", "fast-1")]["data"]["name"] == "Fixed afterwards"


def test_pull_cursor_is_the_servers_stamp_not_this_devices_clock(isolate_device, fake_logged_in, server):
    isolate_device("a")
    tasks_store.create_task({"name": "One", "color": "#111111"})
    assert sync_client.sync_now().success
    assert sync_client._load_pull_cursor() == max(r["server_updated_at"] for r in server.rows.values())


def test_overlap_rereads_are_harmless(isolate_device, fake_logged_in, server):
    isolate_device("a")
    tasks_store.create_task({"name": "One", "color": "#111111"})
    assert sync_client.sync_now().success
    again = sync_client.sync_now()
    assert again.success and again.failed == 0
    assert len(tasks_store.load_tasks()) == 1


def test_more_rows_than_one_page_all_arrive(isolate_device, fake_logged_in, server):
    server.pull_page_size = 3
    for i in range(8):
        server["tasks", f"t{i}"] = _task_record(f"t{i}", f"Task {i}", datetime.now(timezone.utc))
    isolate_device("a")
    assert sync_client.sync_now().pulled == 8


def test_missing_migration_gets_a_clear_error(isolate_device, fake_logged_in, monkeypatch):
    real = httpx.Client

    def handler(request):
        return httpx.Response(400, json={"message": "column sync_records.server_updated_at does not exist"})

    monkeypatch.setattr(sync_client.httpx, "Client", lambda **kw: real(transport=httpx.MockTransport(handler)))
    isolate_device("a")
    result = sync_client.sync_now()
    assert not result.success
    assert "001_direct_sync.sql" in result.error


def test_not_configured_is_a_message_not_a_crash(isolate_device, fake_logged_in, monkeypatch):
    monkeypatch.setattr(auth_manager, "SUPABASE_URL", None)
    isolate_device("a")
    result = sync_client.sync_now()
    assert not result.success and "isn't set up" in result.error


# --- photos ---

def _problem_with_photo(png=b"\x89PNG-bytes"):
    topic = review_store.create_topic("Photos")
    subject = review_store.create_subject(topic["id"], "S", "#ABCDEF")
    problem = review_store.create_problem(
        topic["id"], subject["id"], "Has a picture", 3, "photo", photo_bytes=png, photo_filename="x.png"
    )
    return topic, problem


def test_review_photo_travels_to_the_other_device(isolate_device, fake_logged_in, server):
    isolate_device("a")
    _, problem = _problem_with_photo(b"review-photo-bytes")
    a_path = problem["descriptionPhotoPath"]
    assert sync_client.sync_now().success

    wire = server.rows[[k for k in server.rows if k[0] == "review_problems"][0]]["data"]
    assert wire["descriptionPhotoFile"] == os.path.basename(a_path)
    assert "descriptionPhotoPath" not in wire  # no machine-specific absolute path in the cloud

    isolate_device("b")
    assert sync_client.sync_now().success
    topic = review_store.list_topics()[0]
    b_path = review_store.list_problems(topic["id"], due_only=False)[0]["descriptionPhotoPath"]
    assert os.path.dirname(b_path) == review_store.PHOTOS_DIR
    with open(b_path, "rb") as f:
        assert f.read() == b"review-photo-bytes"


def test_board_photo_travels_to_the_other_device(isolate_device, fake_logged_in, server):
    assert "photo_bytes" in inspect.signature(board_store.create_task).parameters
    isolate_device("a")
    board_store.create_task(name="With photo", importance=3, photo_bytes=b"board-bytes", photo_filename="b.png")
    assert sync_client.sync_now().success
    isolate_device("b")
    assert sync_client.sync_now().success
    pulled = board_store.load_board()[0]
    assert os.path.dirname(pulled["descriptionPhotoPath"]) == board_store.PHOTOS_DIR
    with open(pulled["descriptionPhotoPath"], "rb") as f:
        assert f.read() == b"board-bytes"


def _review_parent_ids():
    conn = calendar_store._get_conn()
    return (
        conn.execute("SELECT sync_id FROM review_topics").fetchone()["sync_id"],
        conn.execute("SELECT sync_id FROM review_subjects").fetchone()["sync_id"],
    )


def test_a_hostile_photo_name_from_the_cloud_never_becomes_a_path(isolate_device, fake_logged_in, server):
    isolate_device("a")
    topic = review_store.create_topic("T")
    review_store.create_subject(topic["id"], "S", "#ABCDEF")
    t_sync, s_sync = _review_parent_ids()
    evil_names = ["../../evil.png", "..\\..\\evil.png", "C:\\Windows\\x.png", "/etc/passwd", "a/b.png", "x.exe"]
    for i, evil in enumerate(evil_names):
        server["review_problems", f"p{i}"] = {
            "table_name": "review_problems", "sync_id": f"p{i}", "device_id": "other", "is_deleted": False,
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "data": {"name": f"P{i}", "stars": 3, "dateAdded": "2026-10-01", "nextReviewDate": "2026-10-02", "descriptionType": "photo", "descriptionPhotoFile": evil,
                     "topicSyncId": t_sync, "subjectSyncId": s_sync},
        }
    assert sync_client.sync_now().success
    problems = review_store.list_problems(topic["id"], due_only=False)
    assert len(problems) == len(evil_names)
    for p in problems:
        path = p["descriptionPhotoPath"]
        # a name that fails validation is stored as "no photo", never as a path
        assert path is None or os.path.dirname(path) == review_store.PHOTOS_DIR
    assert not [r for r in server.requests if "evil" in r[1] or "passwd" in r[1] or ".exe" in r[1]]


def test_photo_is_uploaded_before_its_record_is_pushed(isolate_device, fake_logged_in, server):
    isolate_device("a")
    _problem_with_photo()
    assert sync_client.sync_now().success
    first_photo = next(i for i, (m, p) in enumerate(server.requests) if p.startswith("/storage/"))
    first_row = next(i for i, (m, p) in enumerate(server.requests) if p == "/rest/v1/sync_records" and m == "POST")
    assert first_photo < first_row


def test_legacy_absolute_path_from_another_machine_becomes_a_local_path(isolate_device, fake_logged_in, server):
    isolate_device("a")
    topic = review_store.create_topic("T")
    review_store.create_subject(topic["id"], "S", "#ABCDEF")
    t_sync, s_sync = _review_parent_ids()
    server["review_problems", "old1"] = {
        "table_name": "review_problems", "sync_id": "old1", "device_id": "other", "is_deleted": False,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "data": {"name": "Old", "stars": 3, "dateAdded": "2026-10-01", "nextReviewDate": "2026-10-02", "descriptionType": "photo",
                 "descriptionPhotoPath": "C:\\Users\\Someone\\carmen\\private\\data\\review_photos\\abc123.png",
                 "topicSyncId": t_sync, "subjectSyncId": s_sync},
    }
    assert sync_client.sync_now().success
    p = review_store.list_problems(topic["id"], due_only=False)[0]
    assert p["descriptionPhotoPath"] == os.path.join(review_store.PHOTOS_DIR, "abc123.png")


# --- before the one-time database setup ---

def test_nothing_is_written_to_a_database_that_has_not_been_migrated(isolate_device, fake_logged_in, server):
    server.migrated = False
    isolate_device("a")
    tasks_store.create_task({"name": "Would overwrite", "color": "#111111"})
    _problem_with_photo()
    result = sync_client.sync_now()
    assert not result.success
    assert "one-time setup" in result.error
    assert server.writes == 0 and not server.rows
    assert not server.photos  # not even photos go up


# --- photos that were already there before cloud sync ---

def test_photo_of_an_old_untouched_record_still_uploads(isolate_device, fake_logged_in, server):
    isolate_device("a")
    _problem_with_photo(b"old-photo")
    # as on a real install: the watermark is already newer than every record
    sync_client._save_last_sync(datetime.now(timezone.utc).isoformat())
    assert sync_client.sync_now().success
    assert list(server.photos.values()) == [b"old-photo"]


def test_failed_photo_upload_is_retried_on_the_next_sync(isolate_device, fake_logged_in, server, monkeypatch):
    isolate_device("a")
    _problem_with_photo(b"flaky")
    real_handler = server.handler
    calls = {"fail": True}

    def flaky(request):
        if request.method == "POST" and request.url.path.startswith("/storage/") and calls["fail"]:
            return httpx.Response(500, json={"message": "boom"})
        return real_handler(request)

    server.handler = flaky
    server.install(monkeypatch, sync_client)
    assert sync_client.sync_now().success
    assert not server.photos
    calls["fail"] = False
    assert sync_client.sync_now().success
    assert list(server.photos.values()) == [b"flaky"]


def test_uploaded_photos_are_remembered_across_restarts(isolate_device, fake_logged_in, server, monkeypatch):
    import sync_photos
    isolate_device("a")
    _problem_with_photo(b"once")
    assert sync_client.sync_now().success
    uploads = [r for r in server.requests if r[0] == "POST" and r[1].startswith("/storage/")]
    monkeypatch.setattr(sync_photos, "_uploaded", None)  # a new process re-reads the saved list
    assert sync_client.sync_now().success
    assert [r for r in server.requests if r[0] == "POST" and r[1].startswith("/storage/")] == uploads
