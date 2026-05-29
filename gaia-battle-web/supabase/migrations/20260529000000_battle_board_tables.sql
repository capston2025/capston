create table if not exists public.battle_records (
  id uuid primary key default gen_random_uuid(),
  session_id text not null,
  participant_id text not null,
  participant_name text not null,
  participant_type text not null check (participant_type in ('gaia', 'human')),
  scenario_id text not null default 'live-mission',
  scenario_label text,
  status text not null check (status in ('SUCCESS', 'FAIL', 'BLOCKED', 'RUNNING')),
  duration_seconds numeric,
  reason text,
  artifact_url text,
  metadata jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (session_id, participant_id, scenario_id)
);

create index if not exists battle_records_session_updated_idx
  on public.battle_records (session_id, updated_at desc);

create index if not exists battle_records_session_type_idx
  on public.battle_records (session_id, participant_type);

create table if not exists public.battle_session_states (
  session_id text primary key,
  scenario_id text not null default 'live-mission',
  scenario_label text not null,
  human_started_at timestamptz not null,
  updated_at timestamptz not null default now()
);

alter table public.battle_records enable row level security;
alter table public.battle_session_states enable row level security;

grant usage on schema public to service_role;
grant select, insert, update, delete on public.battle_records to service_role;
grant select, insert, update, delete on public.battle_session_states to service_role;
revoke all on public.battle_records from anon, authenticated;
revoke all on public.battle_session_states from anon, authenticated;
