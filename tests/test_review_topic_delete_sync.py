import pytest

import calendar_store
import review_store
import sync_client


@pytest.mark.usefixtures("isolate_review_db")
def test_deleted_topic_produces_a_sync_tombstone_and_disappears_locally():
    topic = review_store.create_topic("Algorithms")
    review_store.delete_topic(topic["id"])

    assert review_store.list_topics() == []
    assert review_store.get_topic(topic["id"]) is None

    conn = calendar_store._get_conn()
    records = sync_client._gather_review_topics(conn, None)
    assert [(r["sync_id"], r["is_deleted"]) for r in records] == [
        (conn.execute("SELECT sync_id FROM review_topics").fetchone()["sync_id"], True)
    ]


@pytest.mark.usefixtures("isolate_review_db")
def test_applying_a_topic_tombstone_removes_it_and_its_children():
    topic = review_store.create_topic("Graphs")
    conn = calendar_store._get_conn()
    sync_id = conn.execute("SELECT sync_id FROM review_topics").fetchone()["sync_id"]
    review_store.create_subject(topic["id"], "Easy", "#112233")

    record = {
        "table_name": "review_topics", "sync_id": sync_id, "device_id": "other",
        "updated_at": "2999-01-01T00:00:00+00:00", "is_deleted": True,
        "data": {"name": "Graphs", "orderIndex": 0, "linkedTaskId": None},
    }
    sync_client._apply_review_topics(conn, [record])

    assert review_store.list_topics() == []
    assert review_store.list_subjects(topic["id"]) == []


@pytest.mark.usefixtures("isolate_review_db")
def test_topic_name_can_be_reused_after_delete():
    topic = review_store.create_topic("Dup")
    review_store.delete_topic(topic["id"])
    assert review_store.create_topic("dup")["name"] == "dup"
