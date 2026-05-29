# GAIA Battle Web

Vercel 배포용 Human vs GAIA 점수판입니다. 발표장에서는 당일 고정 세션 `battle-live` 하나에 사람 QA와 GAIA 기록을 모읍니다.

Production: `https://gaia-battle-web.vercel.app`

## Local

```bash
npm install
npm run dev
```

- Scoreboard: `http://localhost:3000/battle/battle-live`
- Human input: `http://localhost:3000/battle/battle-live/human`
- Setup: `http://localhost:3000/battle/new`

## GAIA Runner Env

Human vs GAIA CLI mode asks once whether to connect to the deployed board. If accepted, it uses the hard-wired `https://gaia-battle-web.vercel.app` board and `battle-live` session. These variables are only needed when calling `scripts/run_goal_benchmark.py` directly.

```bash
GAIA_BATTLE_UPLOAD_URL=https://your-vercel-app.vercel.app/api/records
GAIA_BATTLE_SITE_URL=https://your-vercel-app.vercel.app
GAIA_BATTLE_UPLOAD_TOKEN=
GAIA_BATTLE_SESSION_ID=battle-live
GAIA_BATTLE_SCENARIO_LABEL="현장 QA 미션"
# Optional cap for inline screenshot evidence sent to the board.
GAIA_BATTLE_SCREENSHOT_MAX_BYTES=850000
```

When these are set, `scripts/run_goal_benchmark.py` uploads each scenario result to the board. If the GAIA run summary contains a final screenshot attachment, the uploader embeds it in record metadata so the live board can render image evidence even when local artifact files are not reachable from Vercel.

## Supabase Table

Vercel serverless storage is not persistent, so use Supabase for the real run.

```sql
create table if not exists battle_records (
  id uuid primary key default gen_random_uuid(),
  session_id text not null,
  participant_id text not null,
  participant_name text not null,
  participant_type text not null check (participant_type in ('gaia', 'human')),
  scenario_id text not null default 'live-mission',
  scenario_label text,
  status text not null,
  duration_seconds numeric,
  reason text,
  artifact_url text,
  metadata jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (session_id, participant_id, scenario_id)
);

create table if not exists battle_session_states (
  session_id text primary key,
  scenario_id text not null default 'live-mission',
  scenario_label text not null,
  human_started_at timestamptz not null,
  updated_at timestamptz not null default now()
);
```

Set these Vercel environment variables:

```bash
SUPABASE_URL=...
SUPABASE_SERVICE_ROLE_KEY=...
SUPABASE_BATTLE_TABLE=battle_records
SUPABASE_BATTLE_STATE_TABLE=battle_session_states
NEXT_PUBLIC_DEFAULT_SESSION_ID=battle-live
NEXT_PUBLIC_SUPABASE_URL=...
NEXT_PUBLIC_SUPABASE_ANON_KEY=...
NEXT_PUBLIC_SUPABASE_BATTLE_TABLE=battle_records
NEXT_PUBLIC_SUPABASE_BATTLE_STATE_TABLE=battle_session_states
```

Human submissions are intentionally open for fast presentation flow. GAIA uploads require `BATTLE_UPLOAD_TOKEN` only when that variable is configured.

The client subscribes to Supabase Realtime Postgres changes for `battle-live` using the public anon key. The Realtime migration adds `battle_records` and `battle_session_states` to `supabase_realtime`, grants anonymous read access only for the fixed live session, and falls back to short polling if the browser-side public Supabase variables are not configured.

## Run Flow

1. Open the presenter board: `/battle/battle-live`.
2. Open the human QA page: `/battle/battle-live/human`.
3. Start `python -m gaia.cli`, choose `GAIA_VS_HUMAN`, then accept the web board prompt.
4. Select one site, `기존 테스트 실행`, then `개별 실행`; the CLI posts the start signal to `/api/session` so the human timer starts.
5. Run one GAIA scenario; `scripts/run_goal_benchmark.py` posts the result to `/api/records`, and the board shows the GAIA result plus screenshot evidence.

Human timing is stored from server-side timestamps: `/api/session` sets the official start time, and `/api/records` computes human duration when the submission reaches the server. The browser timer uses the `/api/session` server clock as its display reference, but the saved score does not trust the client-side stopwatch value.

## Health Check

`/api/health` returns whether the app is using `supabase` or in-memory storage. For Vercel, `storage` should be `supabase` before the presentation.
