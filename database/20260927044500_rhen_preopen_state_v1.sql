-- RHEN Pre-Open State v1.
-- Research/shadow-only market-state persistence. No execution, risk, sizing,
-- strategy-promotion, broker, or live authorization changes.

create table private.trading_preopen_snapshots (
  snapshot_id uuid primary key default gen_random_uuid(),
  snapshot_key text not null unique,
  source_event_id uuid not null unique
    references private.trading_events(event_id),
  trade_date date not null,
  checkpoint text not null,
  observed_at timestamptz not null,
  cutoff timestamptz not null,
  feature_set_version text not null,
  data_quality_state text not null,
  shadow_only boolean not null default true,
  source_summary jsonb not null default '{}'::jsonb,
  features jsonb not null default '{}'::jsonb,
  state_summary jsonb not null default '{}'::jsonb,
  forecast jsonb not null default '{}'::jsonb,
  source_catalog jsonb not null default '[]'::jsonb,
  created_at timestamptz not null default now(),
  constraint trading_preopen_snapshots_checkpoint_check
    check (checkpoint in ('08:00','08:30','09:00','09:15','09:25','09:29')),
  constraint trading_preopen_snapshots_quality_check
    check (data_quality_state in ('AVAILABLE','DEGRADED','INSUFFICIENT')),
  constraint trading_preopen_snapshots_shadow_only_check
    check (shadow_only = true),
  constraint trading_preopen_snapshots_shapes_check
    check (
      jsonb_typeof(source_summary) = 'object'
      and jsonb_typeof(features) = 'object'
      and jsonb_typeof(state_summary) = 'object'
      and jsonb_typeof(forecast) = 'object'
      and jsonb_typeof(source_catalog) = 'array'
    )
);

create index trading_preopen_snapshots_trade_date_idx
  on private.trading_preopen_snapshots(trade_date desc, checkpoint);
create index trading_preopen_snapshots_quality_idx
  on private.trading_preopen_snapshots(data_quality_state, trade_date desc);

alter table private.trading_preopen_snapshots enable row level security;
revoke all on private.trading_preopen_snapshots from anon, authenticated;

create table private.trading_preopen_outcomes (
  outcome_id uuid primary key default gen_random_uuid(),
  outcome_key text not null unique,
  source_event_id uuid not null unique
    references private.trading_events(event_id),
  trade_date date not null,
  horizon_minutes integer not null,
  observed_at timestamptz not null,
  target_at timestamptz not null,
  shadow_only boolean not null default true,
  targets jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  constraint trading_preopen_outcomes_horizon_check
    check (horizon_minutes in (5,30,60,120)),
  constraint trading_preopen_outcomes_shadow_only_check
    check (shadow_only = true),
  constraint trading_preopen_outcomes_targets_check
    check (jsonb_typeof(targets) = 'object')
);

create index trading_preopen_outcomes_trade_date_idx
  on private.trading_preopen_outcomes(trade_date desc, horizon_minutes);

alter table private.trading_preopen_outcomes enable row level security;
revoke all on private.trading_preopen_outcomes from anon, authenticated;

create table private.trading_preopen_model_artifacts (
  model_id uuid primary key default gen_random_uuid(),
  model_key text not null unique,
  target text not null,
  feature_names text[] not null,
  trained_through date not null,
  status text not null,
  checksum text not null unique,
  artifact jsonb not null,
  created_at timestamptz not null default now(),
  retired_at timestamptz,
  constraint trading_preopen_model_status_check
    check (status in ('RESEARCH_ONLY','SHADOW_APPROVED','REJECTED','RETIRED')),
  constraint trading_preopen_model_checksum_check
    check (checksum ~ '^[0-9a-f]{64}$'),
  constraint trading_preopen_model_artifact_check
    check (jsonb_typeof(artifact) = 'object')
);

create index trading_preopen_model_status_idx
  on private.trading_preopen_model_artifacts(status, trained_through desc);

alter table private.trading_preopen_model_artifacts enable row level security;
revoke all on private.trading_preopen_model_artifacts from anon, authenticated;

create function private.project_preopen_trading_event()
returns trigger
language plpgsql
security invoker
set search_path = private, pg_temp
as $function$
begin
  if new.event_type = 'preopen_state_snapshot' then
    insert into private.trading_preopen_snapshots (
      snapshot_key,
      source_event_id,
      trade_date,
      checkpoint,
      observed_at,
      cutoff,
      feature_set_version,
      data_quality_state,
      shadow_only,
      source_summary,
      features,
      state_summary,
      forecast,
      source_catalog
    ) values (
      new.payload->>'snapshot_key',
      new.event_id,
      (new.payload->>'trade_date')::date,
      new.payload->>'checkpoint',
      coalesce(nullif(new.payload->>'observed_at','')::timestamptz, new.occurred_at),
      (new.payload->>'cutoff')::timestamptz,
      new.payload->>'feature_set_version',
      new.payload->>'data_quality_state',
      coalesce((new.payload->>'shadow_only')::boolean, true),
      coalesce(new.payload->'source_summary','{}'::jsonb),
      coalesce(new.payload->'features','{}'::jsonb),
      coalesce(new.payload->'state_summary','{}'::jsonb),
      coalesce(new.payload->'forecast','{}'::jsonb),
      coalesce(new.payload->'source_catalog','[]'::jsonb)
    )
    on conflict (snapshot_key) do update
      set source_summary = excluded.source_summary,
          features = excluded.features,
          state_summary = excluded.state_summary,
          forecast = excluded.forecast,
          data_quality_state = excluded.data_quality_state;

  elsif new.event_type = 'preopen_state_outcome' then
    insert into private.trading_preopen_outcomes (
      outcome_key,
      source_event_id,
      trade_date,
      horizon_minutes,
      observed_at,
      target_at,
      shadow_only,
      targets
    ) values (
      new.payload->>'outcome_key',
      new.event_id,
      (new.payload->>'trade_date')::date,
      (new.payload->>'horizon_minutes')::integer,
      coalesce(nullif(new.payload->>'observed_at','')::timestamptz, new.occurred_at),
      (new.payload->>'target_at')::timestamptz,
      coalesce((new.payload->>'shadow_only')::boolean, true),
      coalesce(new.payload->'targets','{}'::jsonb)
    )
    on conflict (outcome_key) do update
      set targets = excluded.targets,
          observed_at = excluded.observed_at,
          target_at = excluded.target_at;
  end if;
  return new;
end;
$function$;

revoke execute on function private.project_preopen_trading_event()
  from public, anon, authenticated;

create trigger trading_events_project_preopen
after insert on private.trading_events
for each row
when (new.event_type in ('preopen_state_snapshot','preopen_state_outcome'))
execute function private.project_preopen_trading_event();

comment on table private.trading_preopen_snapshots is
  'RHEN research/shadow-only pre-open market-state snapshots. Never authorizes execution.';
comment on table private.trading_preopen_outcomes is
  'Forward outcomes for RHEN pre-open shadow research. Post-event evidence only.';
comment on table private.trading_preopen_model_artifacts is
  'Research/shadow model registry. Status alone never authorizes live trading.';
