-- CARMEN sync schema
--
-- One generic table, sync_records, holds every syncable record from every
-- local table/file (events, reminders, tasks, board, review_topics, etc.)
-- as a JSON blob, tagged with table_name + sync_id instead of one Supabase
-- table per local table. We picked this over nine mirrored tables because:
--   - schema and RLS policy surface stays small (one table, four policies)
--     instead of growing every time a new local table gets sync support
--   - a non-coder reviewing this file can read every rule that governs
--     access to user data in one place
--   - push/pull logic in sync_server stays generic (one upsert path, one
--     "changes since" query) instead of a table-specific branch per type
-- The tradeoff is no per-field constraints/foreign keys inside `data` --
-- that validation lives in the desktop app instead. Acceptable here since
-- Supabase is just sync storage, not the source of truth.

create table sync_records (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users(id),
  table_name text not null,      -- e.g. 'events', 'tasks', 'review_topics'
  sync_id text not null,          -- the local record's sync_id/uuid
  data jsonb not null,            -- the full record as JSON
  device_id text not null,
  updated_at timestamptz not null,
  is_deleted boolean not null default false,
  unique (user_id, table_name, sync_id)
);

-- index for pull queries (fetch changes since a timestamp, per user)
create index on sync_records (user_id, updated_at);

-- Row Level Security: a user can only ever see or touch their own rows.
-- Written out explicitly (not left to default RLS behavior) so every
-- permission granted here is visible in this file.
alter table sync_records enable row level security;

create policy "select own records"
  on sync_records for select
  using (auth.uid() = user_id);

create policy "insert own records"
  on sync_records for insert
  with check (auth.uid() = user_id);

create policy "update own records"
  on sync_records for update
  using (auth.uid() = user_id)
  with check (auth.uid() = user_id);

-- No delete policy: rows are tombstoned via UPDATE (is_deleted = true),
-- matching the desktop app's own soft-delete convention. Without a delete
-- policy, RLS blocks DELETE entirely for normal users.
