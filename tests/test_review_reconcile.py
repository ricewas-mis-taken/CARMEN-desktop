"""Review rows this computer is ahead on must reach the cloud even when their push
was dropped once (the normal push only sends rows newer than its watermark)."""
from datetime import datetime, timezone

import calendar_store
import review_store
import sync_client


def _problem(isolate_review_db):
    topic = review_store.create_topic("T")
    subject = review_store.create_subject(topic["id"], "S", "#112233")
    problem = review_store.create_problem(topic["id"], subject["id"], "Bovine Genomics", 3, "text", description_text="t")
    conn = calendar_store._get_conn()
    return conn, conn.execute("SELECT * FROM review_problems WHERE id = ?", (problem["id"],)).fetchone()


def _cloud(sync_id, count, last, updated="2026-08-16T11:20:34+00:00"):
    return {
        "table_name": "review_problems", "sync_id": sync_id, "device_id": "pc", "is_deleted": False,
        "updated_at": updated,
        "data": {"reviewCount": count, "lastReviewedAt": last, "scheduleStage": 1,
                 "nextReviewDate": "2026-08-23", "fastestTimeSeconds": None, "fastestTimeIsSolved": 1},
    }


def _set_local(conn, row, count, last, updated="2026-07-24T23:42:35"):
    conn.execute(
        "UPDATE review_problems SET review_count = ?, last_reviewed_at = ?, updated_at = ? WHERE id = ?",
        (count, last, updated, row["id"]),
    )
    conn.commit()


def _updated(conn, row):
    return conn.execute("SELECT updated_at FROM review_problems WHERE id = ?", (row["id"],)).fetchone()[0]


def test_a_problem_this_computer_is_ahead_on_is_marked_to_push(isolate_review_db):
    conn, row = _problem(isolate_review_db)
    _set_local(conn, row, 7, "2026-10-20T10:00:00")     # old updated_at: a normal push would skip it
    changed = sync_client._reconcile_with([_cloud(row["sync_id"], 3, "2026-08-12T10:53:54")])
    assert changed == 1
    assert _updated(conn, row) > "2026-10-01"           # now newer than any watermark


def test_a_problem_the_cloud_is_ahead_on_is_brought_here(isolate_review_db):
    conn, row = _problem(isolate_review_db)
    _set_local(conn, row, 3, "2026-08-12T10:53:54")
    assert sync_client._reconcile_with([_cloud(row["sync_id"], 7, "2026-10-20T10:00:00")]) == 1
    got = conn.execute("SELECT review_count, last_reviewed_at, next_review_date FROM review_problems WHERE id = ?", (row["id"],)).fetchone()
    assert (got[0], got[1], got[2]) == (7, "2026-10-20T10:00:00", "2026-08-23")


def test_matching_rows_are_left_alone(isolate_review_db):
    conn, row = _problem(isolate_review_db)
    _set_local(conn, row, 3, "2026-08-12T10:53:54")
    assert sync_client._reconcile_with([_cloud(row["sync_id"], 3, "2026-08-12T10:53:54")]) == 0
    assert _updated(conn, row) == "2026-07-24T23:42:35"


def test_a_problem_the_cloud_never_saw_is_marked_to_push(isolate_review_db):
    conn, row = _problem(isolate_review_db)
    _set_local(conn, row, 1, "2026-10-01T00:00:00")
    assert sync_client._reconcile_with([]) == 1
    assert _updated(conn, row) > "2026-10-01"


def test_a_session_the_cloud_never_saw_is_marked_to_push(isolate_review_db):
    conn, row = _problem(isolate_review_db)
    review_store.finish_review_for_problem(row["id"], 120, self_solved=True, shakiness=3)
    conn.execute("UPDATE review_sessions SET updated_at = '2026-01-01T00:00:00'")
    conn.commit()
    sync_client._reconcile_with([_cloud(row["sync_id"], 1, "2999-01-01T00:00:00")])
    assert conn.execute("SELECT updated_at FROM review_sessions").fetchone()[0] > "2026-10-01"


def test_reconcile_does_nothing_when_signed_out(monkeypatch):
    monkeypatch.setattr(sync_client.auth_manager, "is_logged_in", lambda: False)
    assert sync_client.reconcile_reviews() == 0
