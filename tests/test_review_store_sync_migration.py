"""Tests for the multi-device sync scaffolding migration (sync_id/
updated_at/device_id/is_deleted) added to review_store's four tables --
specifically that it's non-destructive against a DB that predates it, since
this runs automatically against the user's real calendar.db the next time
the app starts."""
import sqlite3

import calendar_store
import review_store


def test_review_topics_migrate_non_destructively(isolate_review_db):
    # Simulate a pre-migration DB: hand-create review_topics in its old
    # shape (no sync_id/updated_at/device_id/is_deleted) and insert a row
    # directly, bypassing review_store's own (already-migrated) schema.
    conn = sqlite3.connect(calendar_store.DB_PATH)
    conn.execute(
        """
        CREATE TABLE review_topics (
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            order_index INTEGER NOT NULL,
            linked_task_id TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    conn.execute(
        "INSERT INTO review_topics (id, name, order_index, created_at) "
        "VALUES (1, 'Math', 0, '2020-01-01T00:00:00')"
    )
    conn.commit()
    conn.close()

    # Any review_store call triggers _get_conn() -> _init_schema(), which
    # must migrate the table above in place without losing the row.
    topics = review_store.list_topics()

    assert len(topics) == 1
    assert topics[0]["name"] == "Math"
    assert topics[0]["id"] == 1

    conn = calendar_store._get_conn()
    row = conn.execute(
        "SELECT sync_id, updated_at, device_id, is_deleted FROM review_topics WHERE id = 1"
    ).fetchone()
    assert row["sync_id"] is not None and len(row["sync_id"]) == 32
    assert row["updated_at"] == "2020-01-01T00:00:00"  # backfilled from created_at
    assert row["device_id"] is None
    assert row["is_deleted"] == 0


def test_sync_id_is_unique_per_row_and_indexed(isolate_review_db):
    conn = sqlite3.connect(calendar_store.DB_PATH)
    conn.execute(
        """
        CREATE TABLE review_topics (
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            order_index INTEGER NOT NULL,
            linked_task_id TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    for i in range(1, 4):
        conn.execute(
            "INSERT INTO review_topics (id, name, order_index, created_at) VALUES (?, ?, ?, ?)",
            (i, f"Topic {i}", i, "2020-01-01T00:00:00"),
        )
    conn.commit()
    conn.close()

    review_store.list_topics()

    conn = calendar_store._get_conn()
    sync_ids = [
        row["sync_id"] for row in conn.execute("SELECT sync_id FROM review_topics ORDER BY id")
    ]
    assert len(sync_ids) == 3
    assert len(set(sync_ids)) == 3  # all distinct
    assert all(sync_ids)  # none blank/None

    index_names = {
        row["name"] for row in conn.execute("PRAGMA index_list(review_topics)").fetchall()
    }
    assert "idx_review_topics_sync_id" in index_names


def test_new_topics_have_no_sync_id_until_the_sync_module_assigns_one(isolate_review_db):
    """By design (per the confirmed sync_id decision): nothing outside the
    sync module -- not even review_store.create_topic() itself -- should
    read or write sync_id. A row created after this migration but before
    Phase 3's sync module exists (or before it's had a chance to see this
    row yet) simply has no sync_id assigned yet; the unique index on
    sync_id tolerates that since SQLite allows multiple NULLs in a UNIQUE
    column. is_deleted still defaults correctly on a fresh row either way."""
    topic = review_store.create_topic("Physics")
    conn = calendar_store._get_conn()
    row = conn.execute(
        "SELECT sync_id, updated_at, is_deleted FROM review_topics WHERE id = ?", (topic["id"],)
    ).fetchone()
    assert row["sync_id"] is None
    assert row["is_deleted"] == 0


def test_migration_is_idempotent(isolate_review_db):
    """Calling into review_store twice (schema init only actually runs
    once per process thanks to _schema_ready, but this simulates a second
    app start against an already-migrated DB) must not error or touch
    already-migrated data."""
    review_store.create_topic("Math")
    conn = calendar_store._get_conn()
    before = conn.execute("SELECT sync_id FROM review_topics").fetchone()["sync_id"]

    # Force a second _init_schema() pass, as if the app restarted.
    review_store._schema_ready = False
    review_store._get_conn()

    after = conn.execute("SELECT sync_id FROM review_topics").fetchone()["sync_id"]
    assert before == after
