# sync_server

FastAPI backend for CARMEN's multi-device sync. Verifies Supabase-issued
access tokens (the same kind `auth_manager.py` produces) and stores/
retrieves records in the `sync_records` table (see `schema.sql`), using
the caller's own token against Supabase's PostgREST API so Supabase's
RLS policies enforce per-user isolation.

## Setup

```bash
pip install -r sync_server/requirements.txt
```

Env vars come from `private/.env` at the repo root -- the same file
`auth_manager.py` already reads. `sync_server/.env.example` documents
what's required:

- `SUPABASE_URL`
- `SUPABASE_PUBLISHABLE_KEY`
- `SUPABASE_JWKS_URL`

No new `.env` file is needed if `private/.env` is already set up for the
main app.

## Running locally

```bash
uvicorn sync_server.main:app --reload --port 8420
```

Port 8420 is deliberately different from the existing Flask app's 5847,
so both can run at the same time during testing.

## Endpoints

- `GET /health` -- no auth, returns `{"status": "ok"}`.
- `POST /sync/push` -- requires `Authorization: Bearer <token>`. Body is
  a list of records `{table_name, sync_id, data, device_id, updated_at,
  is_deleted}`. Upserts each into `sync_records` scoped to the
  authenticated user; a record is skipped (not overwritten) if the
  stored `updated_at` is already newer.
- `GET /sync/pull?since=<ISO timestamp>` -- requires auth. Returns the
  authenticated user's records with `updated_at > since` (or everything,
  if `since` is omitted).

See `test_sync_manual.md` for a full manual test walkthrough.

## Applying the schema

`schema.sql` is not applied automatically. Run it by hand in the
Supabase SQL editor.
