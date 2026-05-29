grant usage on schema public to anon;
grant select on public.battle_records to anon;
grant select on public.battle_session_states to anon;

drop policy if exists "battle_live_records_read" on public.battle_records;
create policy "battle_live_records_read"
on public.battle_records
for select
to anon
using (session_id = 'battle-live');

drop policy if exists "battle_live_session_read" on public.battle_session_states;
create policy "battle_live_session_read"
on public.battle_session_states
for select
to anon
using (session_id = 'battle-live');

do $$
begin
  if not exists (
    select 1
    from pg_publication_tables
    where pubname = 'supabase_realtime'
      and schemaname = 'public'
      and tablename = 'battle_records'
  ) then
    alter publication supabase_realtime add table public.battle_records;
  end if;

  if not exists (
    select 1
    from pg_publication_tables
    where pubname = 'supabase_realtime'
      and schemaname = 'public'
      and tablename = 'battle_session_states'
  ) then
    alter publication supabase_realtime add table public.battle_session_states;
  end if;
end $$;
