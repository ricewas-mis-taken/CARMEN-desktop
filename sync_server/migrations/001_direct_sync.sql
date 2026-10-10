-- CARMEN sync migration 001: lets the desktop app talk straight to Supabase
-- (no sync_server to host) and fixes wrong-clock devices hiding rows.
--
-- Run this ONCE by hand in the Supabase dashboard: SQL Editor -> New query ->
-- paste the whole file -> Run. It is safe to run twice. It does not delete or
-- rewrite any existing record.
--
-- Needs schema.sql to have been applied first (the sync_records table).

-- 1. The server's own clock, stamped on every accepted write. Pulls use this
--    as their cursor instead of the writing device's (possibly wrong) clock.
--    Existing rows get the time this migration ran, so every device simply
--    re-pulls them once.
alter table sync_records
  add column if not exists server_updated_at timestamptz not null default clock_timestamp();

create index if not exists sync_records_user_server_updated_idx
  on sync_records (user_id, server_updated_at);

-- 2. Conflict rules, enforced inside the database so they are atomic:
--    - a write more than 10 minutes ahead of the server clock is treated as
--      "now" (a device whose clock runs fast can't win every later conflict);
--    - an update that is not newer than the stored row is silently ignored
--      (last write wins, by the record's own updated_at);
--    - every accepted write gets a fresh server_updated_at.
create or replace function sync_records_guard() returns trigger
language plpgsql as $$
begin
  if new.updated_at > now() + interval '10 minutes' then
    new.updated_at := now();
  end if;
  if tg_op = 'UPDATE' and new.updated_at <= old.updated_at then
    return null;
  end if;
  new.server_updated_at := clock_timestamp();
  return new;
end;
$$;

drop trigger if exists sync_records_guard_trg on sync_records;
create trigger sync_records_guard_trg
  before insert or update on sync_records
  for each row execute function sync_records_guard();

-- 3. Private photo storage. One folder per user ("<user id>/review/..." and
--    "<user id>/board/..."); a user can only read or add files in their own
--    folder. No update or delete policy: photo names are random per upload,
--    so a stored photo never changes.
insert into storage.buckets (id, name, public)
  values ('carmen-photos', 'carmen-photos', false)
  on conflict (id) do nothing;

drop policy if exists "own photos read" on storage.objects;
create policy "own photos read" on storage.objects for select to authenticated
  using (bucket_id = 'carmen-photos' and (storage.foldername(name))[1] = auth.uid()::text);

drop policy if exists "own photos add" on storage.objects;
create policy "own photos add" on storage.objects for insert to authenticated
  with check (bucket_id = 'carmen-photos' and (storage.foldername(name))[1] = auth.uid()::text);
