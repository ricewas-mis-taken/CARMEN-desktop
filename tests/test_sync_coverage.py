"""Guards against a feature quietly missing from cross-device sync.

Every column of every synced calendar.db table, and every field of a task or
board item, is filled with a non-default value on device A, synced through the
fake cloud, and compared on device B. Add a column or a table without wiring it
into sync_client and one of these fails with the name of what was dropped.

Things that are NOT synced on purpose (session state, session history,
screen time, config, the in-progress review) live in local-only files and are
listed in LOCAL_ONLY_FILES; a new data file has to be added to one list or the
other."""
import copy
import os

import board_store
import calendar_store
import review_store
import sync_client
import tasks_store
from fake_supabase import FakeSupabase

from test_sync_client import fake_logged_in, isolate_device  # noqa: F401  (shared fixtures)

import pytest

# table -> column that identifies a row the same way on every device
SYNCED_TABLES = {
    "events": "id",
    "reminders": "id",
    "focus_profiles": "event_id",
    "review_topics": "sync_id",
    "review_subjects": "sync_id",
    "review_problems": "sync_id",
    "review_sessions": "sync_id",
}
LOCAL_ONLY_TABLES = set()  # add a table here only after deciding it must stay on one device

# Columns that are identity/plumbing, not user data: local integer keys and
# the foreign keys that point at them (parents are matched through sync ids),
# sync bookkeeping, and creation stamps the receiving device writes itself.
NOT_USER_DATA = {
    "id", "topic_id", "subject_id", "problem_id", "event_id",
    "sync_id", "updated_at", "device_id", "is_deleted", "deleted_at",
}
RECEIVER_STAMPED = {
    ("review_topics", "created_at"), ("review_subjects", "created_at"), ("review_problems", "created_at"),
    # a path on one machine, rewritten to the receiver's own photo folder; covered by test_sync_cloud.py
    ("review_problems", "description_photo_path"),
}

# Files under private/ that hold data. Synced ones are covered by the JSON
# tests below or the SQLite tests above; the rest stay on the device by design
# (see FEATURE_IMPLEMENT.txt: session-level state is local-machine-only).
SYNCED_FILES = {"tasks.json", "board.json", "calendar.db"}
LOCAL_ONLY_FILES = {
    "config.json", "screentime.json", "session_history.json", "session_state.json",
    "daily_summaries.json", "active_review.json", "device_id.txt", "last_sync.txt",
    "pull_cursor.txt", "sync_owner.txt",
}


@pytest.fixture
def server(monkeypatch):
    s = FakeSupabase(user_id="user-1")
    s.install(monkeypatch, sync_client)
    return s


def _columns(conn, table):
    return [(r["name"], (r["type"] or "").upper()) for r in conn.execute(f"PRAGMA table_info({table})").fetchall()]


def test_every_calendar_db_table_is_either_synced_or_listed_as_local_only(isolate_device):
    isolate_device("a")
    review_store._get_conn()
    conn = calendar_store._get_conn()
    tables = {r["name"] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
              if not r["name"].startswith("sqlite_")}
    unknown = tables - set(SYNCED_TABLES) - LOCAL_ONLY_TABLES
    assert not unknown, f"new table(s) {sorted(unknown)}: wire them into sync_client or list them as local-only"


def test_every_data_file_is_either_synced_or_listed_as_local_only():
    import glob
    root = os.path.join(os.path.dirname(os.path.abspath(sync_client.__file__)), "private")
    # The real private/ folder may not exist in a fresh checkout; what matters
    # is the names modules write, so look at the module-level path constants.
    import config, device_id, screentime_store, session_manager
    written = {
        os.path.basename(p) for p in (
            config.CONFIG_PATH, screentime_store.STATE_PATH, session_manager.STATE_PATH,
            device_id.DEVICE_ID_PATH, review_store.ACTIVE_SESSION_PATH, tasks_store.TASKS_PATH,
            board_store.BOARD_PATH, calendar_store.DB_PATH, sync_client.LAST_SYNC_PATH,
            sync_client.PULL_CURSOR_PATH, sync_client.SYNC_OWNER_PATH,
        )
    }
    unknown = written - SYNCED_FILES - LOCAL_ONLY_FILES
    assert not unknown, f"new data file(s) {sorted(unknown)}: decide whether they sync"


def _sample(table, column, ctype):
    if column in ("start", "end"):
        return "2026-09-09T09:09:09"
    if column == "stars":
        return 4
    if column == "order_index":
        return 3
    if column == "description_type":
        return "link"
    if column == "lock_mode":
        return "hard"
    if column in ("process_blocklist", "domain_whitelist"):
        return '["sample.exe"]'
    if "INT" in ctype:
        return 1
    return f"sample-{column}" if ctype in ("TEXT", "") else "2026-09-09T09:09:09"


def _seed_rows_with_every_column_set():
    """Create one linked row per synced table through the public APIs, then
    overwrite every user-data column with a non-default sample."""
    event_id = calendar_store.save_event({
        "title": "Seed", "start": "2026-09-09T09:00:00", "end": "2026-09-09T10:00:00", "reminderOffsets": [5],
    })
    event_id = event_id["id"] if isinstance(event_id, dict) else event_id
    topic = review_store.create_topic("Seed topic")
    subject = review_store.create_subject(topic["id"], "Seed subject", "#112233")
    problem = review_store.create_problem(topic["id"], subject["id"], "Seed problem", 3, "text", description_text="t")
    token = review_store.start_review(problem["id"])
    review_store.finish_review(token, self_solved=True, shakiness=2, duration_seconds=33)

    conn = calendar_store._get_conn()
    conn.execute("INSERT OR IGNORE INTO focus_profiles (event_id) VALUES (?)", (event_id,))
    for table in SYNCED_TABLES:
        for column, ctype in _columns(conn, table):
            if column in NOT_USER_DATA or (table, column) in RECEIVER_STAMPED:
                continue
            conn.execute(f"UPDATE {table} SET {column} = ?", (_sample(table, column, ctype),))
    conn.commit()


def _snapshot(table):
    conn = calendar_store._get_conn()
    key = SYNCED_TABLES[table]
    columns = [c for c, _ in _columns(conn, table)
               if c not in NOT_USER_DATA and (table, c) not in RECEIVER_STAMPED]
    rows = conn.execute(f"SELECT {key}, {', '.join(columns)} FROM {table}").fetchall()
    return {r[key]: {c: r[c] for c in columns} for r in rows}


def test_every_calendar_db_column_survives_a_round_trip_to_another_device(isolate_device, fake_logged_in, server):
    isolate_device("a")
    _seed_rows_with_every_column_set()
    sent = {t: _snapshot(t) for t in SYNCED_TABLES}
    assert all(sent[t] for t in SYNCED_TABLES), {t: bool(v) for t, v in sent.items()}
    assert sync_client.sync_now().success

    isolate_device("b")
    result = sync_client.sync_now()
    assert result.success and result.failed == 0
    for table in SYNCED_TABLES:
        got = _snapshot(table)
        assert got == sent[table], f"{table}: a column was not synced -> {_diff(sent[table], got)}"


def _diff(sent, got):
    out = []
    for key, row in sent.items():
        for column, value in row.items():
            have = got.get(key, {}).get(column, "<row missing>")
            if have != value:
                out.append(f"{column}: sent {value!r} got {have!r}")
    return out


def _non_default(key, default):
    special = {
        "weekdays": ["MO", "TU"], "processBlocklist": ["a.exe"], "domainWhitelist": ["a.com"],
        "targetMinutesHistory": [{"date": "2026-01-01", "minutes": 11}], "recurringDays": ["MO"],
        "tags": ["quick"], "color": "#112233", "importance": 9,
    }
    if key in special:
        return special[key]
    if default is None:
        return "2026-09-09T09:09:09"
    if isinstance(default, bool):
        return not default
    if isinstance(default, int):
        return default + 7
    if isinstance(default, str):
        return f"sample-{key}"
    if isinstance(default, dict):
        return {"2026-01-01": 5}
    if isinstance(default, list):
        return ["sample"]
    raise AssertionError(f"don't know how to sample {key!r}")


SYNC_BOOKKEEPING = {"id", "updatedAt", "deviceId", "isDeleted"}


def _round_trip_json_store(isolate_device, create, load, save, defaults, skip=()):
    isolate_device("a")
    item = create()
    items = load()
    full = copy.deepcopy(items[0])
    for key, default in defaults.items():
        if key in SYNC_BOOKKEEPING or key in skip:
            continue
        full[key] = _non_default(key, default)
    items[0] = full
    save(items)
    sent = {k: v for k, v in load()[0].items() if k not in SYNC_BOOKKEEPING and k not in skip}
    # every default field must now differ from its default, or the check is toothless
    for key, default in defaults.items():
        if key not in SYNC_BOOKKEEPING and key not in skip:
            assert sent[key] != default, key
    assert sync_client.sync_now().success
    isolate_device("b")
    assert sync_client.sync_now().success
    got = {k: v for k, v in load()[0].items() if k not in SYNC_BOOKKEEPING and k not in skip}
    missing = {k: (sent[k], got.get(k)) for k in sent if got.get(k) != sent[k]}
    assert not missing, f"fields lost in sync: {missing}"


def test_every_task_field_syncs(isolate_device, fake_logged_in, server):
    _round_trip_json_store(
        isolate_device,
        lambda: tasks_store.create_task({"name": "Seed", "color": "#111111"}),
        tasks_store.load_tasks, tasks_store.save_tasks, tasks_store.DEFAULT_TASK,
    )


def test_every_board_field_syncs(isolate_device, fake_logged_in, server):
    _round_trip_json_store(
        isolate_device,
        lambda: board_store.create_task(name="Seed", importance=3),
        board_store.load_board, board_store.save_board, board_store.DEFAULT_BOARD_TASK,
        skip=("descriptionPhotoPath",),  # a path on one machine; photos have their own tests
    )
