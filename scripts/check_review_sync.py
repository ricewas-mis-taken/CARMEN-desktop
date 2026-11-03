"""Compares this computer's review data with what the cloud holds, problem by problem.

    python scripts/check_review_sync.py            report differences
    python scripts/check_review_sync.py --repair   also make both sides agree

Run it on each computer. The cloud is the meeting point, so if this computer and
the cloud agree here and the other computer's run agrees with the cloud too, the
two computers agree. Differences are listed by problem name with the fields that
differ (next review date, review count, last reviewed, stars).

--repair, per differing record, keeps whichever side was changed last:
  * this computer newer, or the cloud lacks it -> its updated_at is bumped so the
    next sync pushes it
  * the cloud newer -> the pull cursor is reset so the next sync re-reads it
Nothing is deleted. Children of deleted topics are ignored (they are skipped on
purpose and never shown).
"""
import os
import sqlite3
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import httpx

import auth_manager
import paths
import sync_client
import sync_cloud

FIELDS = (
    ("nextReviewDate", "next_review_date"),
    ("reviewCount", "review_count"),
    ("lastReviewedAt", "last_reviewed_at"),
    ("stars", "stars"),
    ("scheduleStage", "schedule_stage"),
)


def _norm(value):
    return "" if value is None else str(value)[:19]


def load_cloud():
    token = auth_manager.get_access_token()
    if not token:
        sys.exit("Not signed in. Open Carmen and sign in first.")
    with httpx.Client(timeout=30.0) as client:
        sync_cloud.ensure_ready(client, token)
        records, _ = sync_cloud.pull(client, token, None, exclude=sync_cloud.FAST_LANE_TABLES)
    return {
        table: {r["sync_id"]: r for r in records if r["table_name"] == table}
        for table in ("review_topics", "review_subjects", "review_problems")
    }


def main():
    repair = "--repair" in sys.argv
    cloud = load_cloud()
    db = sqlite3.connect(paths.private_path("calendar.db"))
    db.row_factory = sqlite3.Row
    live_topics = {k for k, r in cloud["review_topics"].items() if not r["is_deleted"]}
    local_topics = {
        r["sync_id"]: r["is_deleted"] for r in db.execute("SELECT sync_id, is_deleted FROM review_topics")
    }
    local = {r["sync_id"]: dict(r) for r in db.execute("SELECT * FROM review_problems WHERE is_deleted = 0")}
    cloud_problems = {
        k: r for k, r in cloud["review_problems"].items()
        if not r["is_deleted"] and r["data"].get("topicSyncId") in live_topics
    }

    only_cloud = [r["data"]["name"] for k, r in cloud_problems.items() if k not in local]
    only_local = [l["name"] for k, l in local.items() if k not in cloud_problems]
    differ, push, pull = [], [], []
    for k, r in cloud_problems.items():
        row = local.get(k)
        if row is None:
            continue
        bad = [(a, r["data"].get(a), row.get(b)) for a, b in FIELDS if _norm(r["data"].get(a)) != _norm(row.get(b))]
        if bad:
            differ.append((r["data"]["name"], bad))
            cloud_ts = sync_client._from_wire_ts(r["updated_at"])
            (push if (row["updated_at"] or "") > cloud_ts else pull).append(k)

    print(f"cloud: {len(cloud_problems)} live problems   this computer: {len(local)}")
    print(f"only in the cloud ({len(only_cloud)}): {only_cloud}")
    print(f"only on this computer ({len(only_local)}): {only_local}")
    for name, bad in differ:
        print(f"differs: {name}")
        for field, cloud_value, local_value in bad:
            print(f"    {field}: cloud={cloud_value!r}  here={local_value!r}")
    if not (only_cloud or only_local or differ):
        print("In sync.")
        return

    if repair:
        now = datetime.now().isoformat()
        stale = [k for k, l in local.items() if k not in cloud_problems or k in push]
        for k in stale:
            db.execute("UPDATE review_problems SET updated_at = ? WHERE sync_id = ?", (now, k))
        db.commit()
        if pull or only_cloud:
            try:
                os.remove(sync_client.PULL_CURSOR_PATH)
            except OSError:
                pass
        print(f"Repair: queued {len(stale)} to push, reset the pull cursor"
              f"{' (re-reading the cloud)' if (pull or only_cloud) else ''}. Open Carmen to sync.")


if __name__ == "__main__":
    main()
