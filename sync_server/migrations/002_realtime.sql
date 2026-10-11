-- CARMEN sync migration 002: lets the desktop app get changes pushed to it the
-- moment another computer writes them (Supabase Realtime), instead of waiting
-- for its next poll.
--
-- Run this ONCE by hand in the Supabase dashboard: SQL Editor -> New query ->
-- paste the whole file -> Run. Safe to run twice. It changes no data, and the
-- app keeps working (by polling) until you do.
--
-- Needs schema.sql (the sync_records table and its row-level-security policies)
-- to have been applied first. Realtime only ever delivers a row to the account
-- the row belongs to, because it applies the same "select own records" policy.

do $$
begin
  if not exists (
    select 1 from pg_publication_tables
    where pubname = 'supabase_realtime' and schemaname = 'public' and tablename = 'sync_records'
  ) then
    alter publication supabase_realtime add table public.sync_records;
  end if;
end
$$;
