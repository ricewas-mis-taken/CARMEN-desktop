"""SQLite-backed store for the Review tab's spaced-repetition problem
tracker: topics (the chrome-style tabs), subjects (color-coded tags within a
topic), problems, and logged review sessions.

Shares calendar_store.py's single sqlite3 connection to calendar.db instead
of opening a second connection of its own -- same file, same WAL mode,
same "one module-level connection reused everywhere" pattern the rest of
this app's storage already follows. Every write is wrapped in try/except and
logged, matching calendar_store.py's crash-must-never-propagate policy.

The interval math itself lives in review_scheduler.py (kept import-free of
sqlite/Flask/PySide6 so it's separately unit-testable); this module just
calls it and persists the result.
"""
import os
import sqlite3
import threading
import uuid
from datetime import date, datetime, timedelta

import calendar_store
import review_scheduler
from calendar_log import logger

PHOTOS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "review_photos")


# The same lock calendar_store.py serializes its own writes through, not a
# separate one -- both modules share calendar_store's single sqlite3
# connection (check_same_thread=False), which is reused across the GUI
# thread, the background scheduler thread, and Flask's worker threads. A
# lock of review_store's own would only serialize review_* writes against
# each other, not against a concurrent calendar_store write landing on the
# same connection at the same time.
_lock = calendar_store._lock
_schema_ready = False

# Review sessions in progress (Start clicked, Finish not yet) -- deliberately
# not a table. A "started but never finished" row would otherwise need its
# own cleanup story (crash, app closed mid-timer, user just never comes
# back); keeping it in memory means an abandoned start simply vanishes with
# the process, with nothing to reconcile later. review_sessions (the sqlite
# table) only ever gets a row once a session actually completes.
_active_sessions = {}


class DuplicateNameError(Exception):
    """Raised (not swallowed by the usual try/except-Exception-and-return-None
    pattern below) when a topic/subject/problem name collides, case-
    insensitively, with an existing one in the same scope: topics globally,
    subjects within a topic, problems within a subject. Callers -- the Qt
    dialogs -- catch this specifically to show a real message instead of the
    generic "could not save" one."""


def _get_conn():
    global _schema_ready
    conn = calendar_store._get_conn()
    if not _schema_ready:
        _init_schema(conn)
        _schema_ready = True
    return conn


def _init_schema(conn):
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS review_topics (
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            order_index INTEGER NOT NULL,
            linked_task_id TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS review_subjects (
            id INTEGER PRIMARY KEY,
            topic_id INTEGER NOT NULL REFERENCES review_topics(id),
            name TEXT NOT NULL,
            color TEXT NOT NULL,
            linked_task_id TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS review_problems (
            id INTEGER PRIMARY KEY,
            topic_id INTEGER NOT NULL REFERENCES review_topics(id),
            subject_id INTEGER NOT NULL REFERENCES review_subjects(id),
            name TEXT NOT NULL,
            stars INTEGER NOT NULL CHECK(stars BETWEEN 1 AND 5),
            description_type TEXT NOT NULL CHECK(description_type IN ('text','photo','link')),
            description_text TEXT,
            description_photo_path TEXT,
            description_link TEXT,
            date_added DATE NOT NULL DEFAULT CURRENT_DATE,
            review_count INTEGER NOT NULL DEFAULT 0,
            last_reviewed_at TIMESTAMP,
            fastest_time_seconds INTEGER,
            fastest_time_is_solved INTEGER,
            schedule_stage INTEGER NOT NULL DEFAULT 0,
            next_review_date DATE NOT NULL,
            first_attempt_seconds INTEGER,
            first_attempt_shakiness INTEGER,
            first_attempt_self_solved INTEGER,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS review_sessions (
            id INTEGER PRIMARY KEY,
            problem_id INTEGER NOT NULL REFERENCES review_problems(id),
            started_at TIMESTAMP NOT NULL,
            finished_at TIMESTAMP NOT NULL,
            duration_seconds INTEGER NOT NULL,
            self_solved INTEGER,
            shakiness INTEGER
        );

        CREATE INDEX IF NOT EXISTS idx_review_subjects_topic ON review_subjects(topic_id);
        CREATE INDEX IF NOT EXISTS idx_review_problems_topic ON review_problems(topic_id);
        CREATE INDEX IF NOT EXISTS idx_review_problems_next_review ON review_problems(next_review_date);
        """
    )
    conn.commit()

    # Add linked_task_id to review_subjects for DBs that predate this column.
    existing_cols = {row[1] for row in conn.execute("PRAGMA table_info(review_subjects)").fetchall()}
    if "linked_task_id" not in existing_cols:
        conn.execute("ALTER TABLE review_subjects ADD COLUMN linked_task_id TEXT")
        conn.commit()

    # Add linked_task_id to review_topics for DBs that predate this column.
    topic_cols = {row[1] for row in conn.execute("PRAGMA table_info(review_topics)").fetchall()}
    if "linked_task_id" not in topic_cols:
        conn.execute("ALTER TABLE review_topics ADD COLUMN linked_task_id TEXT")
        conn.commit()

    # Add first_attempt_* columns to review_problems for DBs that predate them
    # ("Start First Attempt" from the Add Problem dialog).
    problem_cols = {row[1] for row in conn.execute("PRAGMA table_info(review_problems)").fetchall()}
    for col in ("first_attempt_seconds", "first_attempt_shakiness", "first_attempt_self_solved", "fastest_time_is_solved"):
        if col not in problem_cols:
            conn.execute(f"ALTER TABLE review_problems ADD COLUMN {col} INTEGER")
            conn.commit()
            if col == "fastest_time_is_solved":
                # Backfill: before this column existed, fastest_time_seconds
                # was only ever set on a genuine solve (checked-answer
                # attempts never touched it), so every pre-existing row with
                # a fastest time on record is a real solve, not an estimate
                # -- without this they'd wrongly show the "(A)" marker.
                conn.execute(
                    "UPDATE review_problems SET fastest_time_is_solved = 1 "
                    "WHERE fastest_time_seconds IS NOT NULL AND fastest_time_is_solved IS NULL"
                )
                conn.commit()

    # Add self_solved/shakiness to review_sessions for DBs that predate them
    # -- every logged review's full outcome, not just its duration.
    session_cols = {row[1] for row in conn.execute("PRAGMA table_info(review_sessions)").fetchall()}
    for col in ("self_solved", "shakiness"):
        if col not in session_cols:
            conn.execute(f"ALTER TABLE review_sessions ADD COLUMN {col} INTEGER")
            conn.commit()


def _row_to_topic(row):
    return {
        "id": row["id"],
        "name": row["name"],
        "orderIndex": row["order_index"],
        "linkedTaskId": row["linked_task_id"],
    }


def _row_to_subject(row):
    return {
        "id": row["id"],
        "topicId": row["topic_id"],
        "name": row["name"],
        "color": row["color"],
        "linkedTaskId": row["linked_task_id"],
    }


def _row_to_problem(row):
    return {
        "id": row["id"],
        "topicId": row["topic_id"],
        "subjectId": row["subject_id"],
        "subjectName": row["subject_name"],
        "subjectColor": row["subject_color"],
        "name": row["name"],
        "stars": row["stars"],
        "descriptionType": row["description_type"],
        "descriptionText": row["description_text"],
        "descriptionPhotoPath": row["description_photo_path"],
        "descriptionLink": row["description_link"],
        "dateAdded": row["date_added"],
        "reviewCount": row["review_count"],
        "lastReviewedAt": row["last_reviewed_at"],
        "fastestTimeSeconds": row["fastest_time_seconds"],
        "fastestTimeIsSolved": bool(row["fastest_time_is_solved"]) if row["fastest_time_is_solved"] is not None else None,
        "scheduleStage": row["schedule_stage"],
        "nextReviewDate": row["next_review_date"],
        "firstAttemptSeconds": row["first_attempt_seconds"],
        "firstAttemptShakiness": row["first_attempt_shakiness"],
        "firstAttemptSelfSolved": bool(row["first_attempt_self_solved"]) if row["first_attempt_self_solved"] is not None else None,
    }


_PROBLEM_SELECT = """
    SELECT p.*, s.name AS subject_name, s.color AS subject_color
    FROM review_problems p
    JOIN review_subjects s ON s.id = p.subject_id
"""


def list_topics():
    with _lock:
        try:
            conn = _get_conn()
            rows = conn.execute(
                "SELECT * FROM review_topics ORDER BY order_index, id"
            ).fetchall()
            return [_row_to_topic(r) for r in rows]
        except Exception:
            logger.exception("review_store.list_topics failed")
            return []


def create_topic(name):
    with _lock:
        try:
            conn = _get_conn()
            if conn.execute(
                "SELECT id FROM review_topics WHERE LOWER(name) = LOWER(?)", (name,)
            ).fetchone():
                raise DuplicateNameError(f'A topic named "{name}" already exists.')
            max_order = conn.execute(
                "SELECT COALESCE(MAX(order_index), -1) AS m FROM review_topics"
            ).fetchone()["m"]
            cur = conn.execute(
                "INSERT INTO review_topics (name, order_index) VALUES (?, ?)",
                (name, max_order + 1),
            )
            conn.commit()
            row = conn.execute("SELECT * FROM review_topics WHERE id = ?", (cur.lastrowid,)).fetchone()
            return _row_to_topic(row)
        except DuplicateNameError:
            raise
        except Exception:
            logger.exception("review_store.create_topic failed for %s", name)
            return None


def get_topic(topic_id):
    with _lock:
        try:
            conn = _get_conn()
            row = conn.execute("SELECT * FROM review_topics WHERE id = ?", (topic_id,)).fetchone()
            return _row_to_topic(row) if row else None
        except Exception:
            logger.exception("review_store.get_topic failed for %s", topic_id)
            return None


def update_topic_link(topic_id, task_id):
    """Links or unlinks a topic tab to a task (task_id=None removes the link)."""
    with _lock:
        try:
            conn = _get_conn()
            conn.execute(
                "UPDATE review_topics SET linked_task_id = ? WHERE id = ?",
                (task_id, topic_id),
            )
            conn.commit()
        except Exception:
            logger.exception("review_store.update_topic_link failed for topic %s", topic_id)


def rename_topic(topic_id, name):
    with _lock:
        try:
            conn = _get_conn()
            if conn.execute(
                "SELECT id FROM review_topics WHERE LOWER(name) = LOWER(?) AND id != ?",
                (name, topic_id),
            ).fetchone():
                raise DuplicateNameError(f'A topic named "{name}" already exists.')
            conn.execute("UPDATE review_topics SET name = ? WHERE id = ?", (name, topic_id))
            conn.commit()
        except DuplicateNameError:
            raise
        except Exception:
            logger.exception("review_store.rename_topic failed for %s", topic_id)


def delete_topic(topic_id):
    """Deletes a topic and all its subjects and problems."""
    with _lock:
        try:
            conn = _get_conn()
            conn.execute(
                "DELETE FROM review_sessions WHERE problem_id IN "
                "(SELECT id FROM review_problems WHERE topic_id = ?)",
                (topic_id,),
            )
            conn.execute("DELETE FROM review_problems WHERE topic_id = ?", (topic_id,))
            conn.execute("DELETE FROM review_subjects WHERE topic_id = ?", (topic_id,))
            conn.execute("DELETE FROM review_topics WHERE id = ?", (topic_id,))
            conn.commit()
        except Exception:
            logger.exception("review_store.delete_topic failed for %s", topic_id)


def list_subjects(topic_id):
    with _lock:
        try:
            conn = _get_conn()
            rows = conn.execute(
                "SELECT * FROM review_subjects WHERE topic_id = ? ORDER BY name", (topic_id,)
            ).fetchall()
            return [_row_to_subject(r) for r in rows]
        except Exception:
            logger.exception("review_store.list_subjects failed for topic %s", topic_id)
            return []


def create_subject(topic_id, name, color, linked_task_id=None):
    with _lock:
        try:
            conn = _get_conn()
            if conn.execute(
                "SELECT id FROM review_subjects WHERE topic_id = ? AND LOWER(name) = LOWER(?)",
                (topic_id, name),
            ).fetchone():
                raise DuplicateNameError(f'A subject named "{name}" already exists in this topic.')
            cur = conn.execute(
                "INSERT INTO review_subjects (topic_id, name, color, linked_task_id) VALUES (?, ?, ?, ?)",
                (topic_id, name, color, linked_task_id),
            )
            conn.commit()
            row = conn.execute("SELECT * FROM review_subjects WHERE id = ?", (cur.lastrowid,)).fetchone()
            return _row_to_subject(row)
        except DuplicateNameError:
            raise
        except Exception:
            logger.exception("review_store.create_subject failed for topic %s", topic_id)
            return None


def update_subject_link(subject_id, task_id):
    """Links or unlinks a subject to a task (task_id=None removes the link)."""
    with _lock:
        try:
            conn = _get_conn()
            conn.execute(
                "UPDATE review_subjects SET linked_task_id = ? WHERE id = ?",
                (task_id, subject_id),
            )
            conn.commit()
        except Exception:
            logger.exception("review_store.update_subject_link failed for subject %s", subject_id)


def list_problems(topic_id, due_only=True):
    with _lock:
        try:
            conn = _get_conn()
            where = "WHERE p.topic_id = ?"
            params = [topic_id]
            if due_only:
                where += " AND p.next_review_date <= ?"
                params.append(date.today().isoformat())
            rows = conn.execute(
                f"{_PROBLEM_SELECT} {where} ORDER BY p.next_review_date ASC, p.stars DESC",
                params,
            ).fetchall()
            return [_row_to_problem(r) for r in rows]
        except Exception:
            logger.exception("review_store.list_problems failed for topic %s", topic_id)
            return []


def get_problem(problem_id):
    with _lock:
        try:
            conn = _get_conn()
            row = conn.execute(f"{_PROBLEM_SELECT} WHERE p.id = ?", (problem_id,)).fetchone()
            return _row_to_problem(row) if row else None
        except Exception:
            logger.exception("review_store.get_problem failed for %s", problem_id)
            return None


def save_photo_bytes(data, original_filename):
    """Copies an uploaded image's bytes into PHOTOS_DIR under a generated
    name, so the DB never depends on wherever the user originally picked the
    file from. Returns the saved file's absolute path."""
    os.makedirs(PHOTOS_DIR, exist_ok=True)
    ext = os.path.splitext(original_filename or "")[1].lower() or ".png"
    filename = f"{uuid.uuid4().hex}{ext}"
    path = os.path.join(PHOTOS_DIR, filename)
    with open(path, "wb") as f:
        f.write(data)
    return path


def create_problem(
    topic_id, subject_id, name, stars, description_type,
    description_text=None, description_link=None,
    photo_bytes=None, photo_filename=None,
):
    if description_type not in ("text", "photo", "link"):
        return None
    photo_path = None
    if description_type == "photo" and photo_bytes is not None:
        photo_path = save_photo_bytes(photo_bytes, photo_filename)

    schedule = review_scheduler.schedule_new_problem(stars)

    with _lock:
        try:
            conn = _get_conn()
            if conn.execute(
                "SELECT id FROM review_problems WHERE subject_id = ? AND LOWER(name) = LOWER(?)",
                (subject_id, name),
            ).fetchone():
                raise DuplicateNameError(f'A problem named "{name}" already exists in this subject.')
            cur = conn.execute(
                """
                INSERT INTO review_problems (
                    topic_id, subject_id, name, stars, description_type,
                    description_text, description_photo_path, description_link,
                    schedule_stage, next_review_date
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    topic_id, subject_id, name, stars, description_type,
                    description_text, photo_path, description_link,
                    schedule["schedule_stage"], schedule["next_review_date"].isoformat(),
                ),
            )
            conn.commit()
            problem_id = cur.lastrowid
        except DuplicateNameError:
            raise
        except Exception:
            logger.exception("review_store.create_problem failed for %s", name)
            return None
    return get_problem(problem_id)


def update_problem(
    problem_id, subject_id, name, stars, description_type,
    description_text=None, description_link=None,
    photo_bytes=None, photo_filename=None,
):
    """Edits a problem's editable fields (right-click > Edit Problem in the
    Review tab). Review history/schedule (review_count, last_reviewed_at,
    fastest_time_seconds, schedule_stage, next_review_date) is untouched --
    editing details isn't a review attempt. A new photo is only saved (and
    the old file left behind, matching create_problem/save_photo_bytes'
    never-delete-old-files behavior) when photo_bytes is given; otherwise
    the existing description_photo_path is kept as-is."""
    if description_type not in ("text", "photo", "link"):
        return None

    with _lock:
        try:
            conn = _get_conn()
            row = conn.execute(
                "SELECT description_photo_path FROM review_problems WHERE id = ?", (problem_id,)
            ).fetchone()
            if row is None:
                return None
            photo_path = row["description_photo_path"]
        except Exception:
            logger.exception("review_store.update_problem failed to load %s", problem_id)
            return None

    if description_type == "photo" and photo_bytes is not None:
        photo_path = save_photo_bytes(photo_bytes, photo_filename)
    elif description_type != "photo":
        photo_path = None

    with _lock:
        try:
            conn = _get_conn()
            if conn.execute(
                "SELECT id FROM review_problems WHERE subject_id = ? AND LOWER(name) = LOWER(?) AND id != ?",
                (subject_id, name, problem_id),
            ).fetchone():
                raise DuplicateNameError(f'A problem named "{name}" already exists in this subject.')
            conn.execute(
                """
                UPDATE review_problems SET
                    subject_id = ?, name = ?, stars = ?, description_type = ?,
                    description_text = ?, description_photo_path = ?, description_link = ?
                WHERE id = ?
                """,
                (
                    subject_id, name, stars, description_type,
                    description_text, photo_path, description_link,
                    problem_id,
                ),
            )
            conn.commit()
        except DuplicateNameError:
            raise
        except Exception:
            logger.exception("review_store.update_problem failed for %s", problem_id)
            return None

    return get_problem(problem_id)


def start_review(problem_id):
    """Logs a start timestamp for a review attempt in memory (not sqlite --
    see _active_sessions above) and hands back an opaque token the caller
    must pass to finish_review(). Returns None if the problem doesn't
    exist."""
    if get_problem(problem_id) is None:
        return None
    token = uuid.uuid4().hex
    _active_sessions[token] = {"problem_id": problem_id, "started_at": datetime.now()}
    return token


def abandon_review(session_token):
    """Discards a started review without logging anything: no review_sessions
    row, no stat updates. The elapsed task session time still counts because
    session_manager tracks it independently."""
    _active_sessions.pop(session_token, None)


def _apply_review_outcome(problem_id, duration_seconds, self_solved, shakiness, started_at=None):
    """Shared by finish_review() and record_first_attempt(): logs the
    session, bumps review_count/last_reviewed_at, updates fastest_time,
    reschedules, and (once, from review_count == 0) records first_attempt_*
    stats. `started_at` defaults to finished_at - duration_seconds when the
    caller (record_first_attempt) never had a start_review() token to carry
    a real one."""
    finished_at = datetime.now()
    if started_at is None:
        started_at = finished_at - timedelta(seconds=duration_seconds)

    with _lock:
        try:
            conn = _get_conn()
            row = conn.execute(
                "SELECT stars, schedule_stage, fastest_time_seconds, fastest_time_is_solved, review_count "
                "FROM review_problems WHERE id = ?",
                (problem_id,),
            ).fetchone()
            if row is None:
                return None

            conn.execute(
                """
                INSERT INTO review_sessions
                    (problem_id, started_at, finished_at, duration_seconds, self_solved, shakiness)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    problem_id, started_at.isoformat(), finished_at.isoformat(), duration_seconds,
                    int(self_solved), shakiness if self_solved else None,
                ),
            )

            # fastest_time_seconds tracks the fastest genuine solve once one
            # exists. Until then, it stands in with the fastest "checked the
            # answer" time instead of sitting blank -- a real solve always
            # takes over from an estimate, but further checked-answer
            # attempts never overwrite a genuine solve.
            fastest = row["fastest_time_seconds"]
            fastest_is_solved = row["fastest_time_is_solved"]
            if self_solved:
                if fastest is None or not fastest_is_solved or duration_seconds < fastest:
                    fastest = duration_seconds
                    fastest_is_solved = 1
            elif not fastest_is_solved:
                if fastest is None or duration_seconds < fastest:
                    fastest = duration_seconds
                    fastest_is_solved = 0

            if self_solved:
                schedule = review_scheduler.schedule_after_review(
                    row["schedule_stage"], row["stars"], shakiness=shakiness
                )
            else:
                schedule = review_scheduler.schedule_checked_answer(
                    row["schedule_stage"], row["stars"]
                )

            # is_first_attempt is captured once, from review_count == 0 --
            # subsequent UPDATEs never touch these columns again, so they
            # stay a permanent record of how the very first attempt went
            # even after later attempts change fastest_time_seconds/stage.
            # Shakiness only means anything for a solved attempt -- "checked
            # the answer" never collects it, so it must never be persisted
            # (or later displayed) as if it had been.
            is_first_attempt = row["review_count"] == 0
            first_attempt_seconds = duration_seconds if is_first_attempt else None
            first_attempt_shakiness = shakiness if (is_first_attempt and self_solved) else None
            first_attempt_self_solved = int(self_solved) if is_first_attempt else None

            conn.execute(
                """
                UPDATE review_problems SET
                    review_count = review_count + 1,
                    last_reviewed_at = ?,
                    fastest_time_seconds = ?,
                    fastest_time_is_solved = ?,
                    schedule_stage = ?,
                    next_review_date = ?,
                    first_attempt_seconds = COALESCE(first_attempt_seconds, ?),
                    first_attempt_shakiness = COALESCE(first_attempt_shakiness, ?),
                    first_attempt_self_solved = COALESCE(first_attempt_self_solved, ?)
                WHERE id = ?
                """,
                (
                    finished_at.isoformat(), fastest, fastest_is_solved,
                    schedule["schedule_stage"], schedule["next_review_date"].isoformat(),
                    first_attempt_seconds, first_attempt_shakiness, first_attempt_self_solved,
                    problem_id,
                ),
            )
            conn.commit()
        except Exception:
            logger.exception("review_store._apply_review_outcome failed for problem %s", problem_id)
            try:
                conn.rollback()
            except Exception:
                pass
            return None

    return get_problem(problem_id)


def finish_review(session_token, self_solved=True, shakiness=3):
    """Ends a review started via start_review(): logs the session, bumps
    review_count/last_reviewed_at, and reschedules the problem.

    self_solved=True  → advance schedule stage; update fastest_time if new best.
    self_solved=False → keep stage; schedule tomorrow; fastest_time unchanged.
    shakiness (1–5)   → 1 solid, 5 very shaky; higher shakiness shortens the
                        next interval (only applies when self_solved=True).

    Returns the updated problem dict, or None if the token is unknown/already used."""
    entry = _active_sessions.pop(session_token, None)
    if entry is None:
        return None

    started_at = entry["started_at"]
    duration_seconds = max(0, int((datetime.now() - started_at).total_seconds()))
    return _apply_review_outcome(
        entry["problem_id"], duration_seconds, self_solved, shakiness, started_at=started_at
    )


def list_sessions(problem_id):
    """Full log of every review ever finished for a problem -- start/finish
    time, duration, and outcome (self_solved/shakiness), newest first. This
    is the raw history behind the aggregate stats (fastest time, review
    count, first-attempt snapshot) shown elsewhere."""
    with _lock:
        try:
            conn = _get_conn()
            rows = conn.execute(
                "SELECT * FROM review_sessions WHERE problem_id = ? ORDER BY finished_at DESC",
                (problem_id,),
            ).fetchall()
            return [
                {
                    "id": row["id"],
                    "startedAt": row["started_at"],
                    "finishedAt": row["finished_at"],
                    "durationSeconds": row["duration_seconds"],
                    "selfSolved": bool(row["self_solved"]) if row["self_solved"] is not None else None,
                    "shakiness": row["shakiness"],
                }
                for row in rows
            ]
        except Exception:
            logger.exception("review_store.list_sessions failed for problem %s", problem_id)
            return []


def record_first_attempt(problem_id, duration_seconds, self_solved=True, shakiness=3):
    """Records a review that was timed *before* the problem's form was ever
    filled in -- the Add Problem dialog's "Start First Attempt" button opens
    a bare timer with no problem_id to attach a start_review() token to, so
    this takes the elapsed seconds directly once the problem has actually
    been saved. Same scoring/scheduling as finish_review()."""
    return _apply_review_outcome(problem_id, max(0, int(duration_seconds)), self_solved, shakiness)
