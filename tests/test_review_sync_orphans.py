"""A device that hadn't yet seen a topic's deletion can still push that
topic's subjects/problems/sessions. Those must not come back to life as
invisible rows hanging under the deleted topic."""
from datetime import datetime, timezone

import calendar_store
import review_store
import sync_client


def _record(table, sync_id, data):
    return {
        "table_name": table, "sync_id": sync_id, "device_id": "other", "is_deleted": False,
        "updated_at": datetime.now(timezone.utc).isoformat(), "data": data,
    }


def _deleted_topic_with_sync_id():
    topic = review_store.create_topic("Gone")
    subject = review_store.create_subject(topic["id"], "Old subject", "#112233")
    conn = calendar_store._get_conn()
    topic_sync = conn.execute("SELECT sync_id FROM review_topics").fetchone()["sync_id"]
    subject_sync = conn.execute("SELECT sync_id FROM review_subjects").fetchone()["sync_id"]
    review_store.delete_topic(topic["id"])
    return conn, topic_sync, subject_sync


def _count(conn, table):
    return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


def test_a_subject_for_a_deleted_topic_is_not_resurrected(isolate_review_db):
    conn, topic_sync, _ = _deleted_topic_with_sync_id()
    applied, skipped, failed = sync_client._apply_review_subjects(conn, [
        _record("review_subjects", "late-subject", {"name": "Late", "color": "#445566", "topicSyncId": topic_sync}),
    ])
    assert (applied, skipped, failed) == (0, 1, 0)
    assert _count(conn, "review_subjects") == 0


def test_a_problem_for_a_deleted_topic_is_not_resurrected(isolate_review_db):
    conn, topic_sync, subject_sync = _deleted_topic_with_sync_id()
    applied, skipped, failed = sync_client._apply_review_problems(conn, [
        _record("review_problems", "late-problem", {
            "name": "Late", "stars": 3, "descriptionType": "text", "dateAdded": "2026-10-01",
            "nextReviewDate": "2026-10-02", "topicSyncId": topic_sync, "subjectSyncId": subject_sync,
        }),
    ])
    assert (applied, skipped, failed) == (0, 1, 0)
    assert _count(conn, "review_problems") == 0


def test_a_session_for_a_deleted_problem_is_not_resurrected(isolate_review_db):
    topic = review_store.create_topic("Live")
    subject = review_store.create_subject(topic["id"], "S", "#112233")
    problem = review_store.create_problem(topic["id"], subject["id"], "P", 3, "text", description_text="t")
    conn = calendar_store._get_conn()
    problem_sync = conn.execute("SELECT sync_id FROM review_problems").fetchone()["sync_id"]
    conn.execute("UPDATE review_problems SET is_deleted = 1 WHERE id = ?", (problem["id"],))
    conn.commit()
    applied, skipped, failed = sync_client._apply_review_sessions(conn, [
        _record("review_sessions", "late-session", {
            "startedAt": "2026-10-01T10:00:00", "finishedAt": "2026-10-01T10:05:00",
            "durationSeconds": 300, "problemSyncId": problem_sync,
        }),
    ])
    assert (applied, skipped, failed) == (0, 1, 0)
    assert _count(conn, "review_sessions") == 0
