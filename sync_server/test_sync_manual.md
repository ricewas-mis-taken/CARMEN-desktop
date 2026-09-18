# Manual test sequence for sync_server

Run `uvicorn sync_server.main:app --reload --port 8420` first, in a
separate terminal, from the repo root.

## 1. Health check (no auth)

```bash
curl http://127.0.0.1:8420/health
```

Expect `200` and `{"status":"ok"}`.

## 2. Get a real access token

```bash
python scripts/print_access_token.py
```

Enter an existing test account's email/password (create one first with
`python scripts/manual_auth_check.py` if you don't have one). Copy the
printed access token -- you'll paste it into the commands below in place
of `PASTE_TOKEN_HERE`.

## 3. Push some test records

Create `body.json` in the repo root:

```json
[
  {
    "table_name": "tasks",
    "sync_id": "manual-test-task-1",
    "data": {"title": "manual test task", "importance": "medium"},
    "device_id": "manual-test-device",
    "updated_at": "2026-08-16T12:00:00Z",
    "is_deleted": false
  },
  {
    "table_name": "events",
    "sync_id": "manual-test-event-1",
    "data": {"title": "manual test event"},
    "device_id": "manual-test-device",
    "updated_at": "2026-08-16T12:00:00Z",
    "is_deleted": false
  }
]
```

```powershell
curl -X POST http://127.0.0.1:8420/sync/push `
  -H "Authorization: Bearer PASTE_TOKEN_HERE" `
  -H "Content-Type: application/json" `
  -d "@body.json"
```

Expect `200` with both records listed under `"accepted"`.

Re-running the exact same command should list both under `"skipped"`
instead (same `updated_at`, not newer -- last-write-wins keeps the
stored copy).

## 4. Pull and confirm they come back

```bash
curl "http://127.0.0.1:8420/sync/pull" -H "Authorization: Bearer PASTE_TOKEN_HERE"
```

Expect both records back, with `data`/`device_id`/`updated_at` matching
what was pushed.

Try `since` with a timestamp after the push, e.g.:

```bash
curl "http://127.0.0.1:8420/sync/pull?since=2026-08-16T13:00:00Z" -H "Authorization: Bearer PASTE_TOKEN_HERE"
```

Expect an empty list.

## 5. Confirm auth is actually enforced

```bash
curl -X POST http://127.0.0.1:8420/sync/push -H "Content-Type: application/json" -d "@body.json"
curl http://127.0.0.1:8420/sync/pull
```

Both should return `401`, not `200` and not a server error.

Delete `body.json` once done -- it's a scratch file, not meant to be
committed.
