-- NOSTRA FORWARD foundation: FWD-002 through FWD-006.
-- Immutable point-in-time snapshots, forecasts, outcomes, and scoring evidence.
-- Research-only. This migration grants no execution, risk, sizing, or strategy authority.

create table if not exists private.nostra_snapshots (
  snapshot_id text primary key,
  candidate_id bigint references private.trading_candidate_evaluations(candidate_id) on delete restrict,
  run_id uuid references private.trading_runs(run_id) on delete restrict,
  strategy_version_id text references private.trading_strategy_versions(version_id) on delete restrict,
  symbol text not null check (length(symbol) > 0),
  market_lane text not null check (length(market_lane) > 0),
  as_of_timestamp timestamptz not null,
  feature_set_version text not null check (length(feature_set_version) > 0),
  raw_features jsonb not null default '{}'::jsonb check (jsonb_typeof(raw_features) = 'object'),
  normalized_features jsonb not null default '{}'::jsonb check (jsonb_typeof(normalized_features) = 'object'),
  market_state jsonb not null default '{}'::jsonb check (jsonb_typeof(market_state) = 'object'),
  data_quality jsonb not null default '{}'::jsonb check (jsonb_typeof(data_quality) = 'object'),
  source jsonb not null default '{}'::jsonb check (jsonb_typeof(source) = 'object'),
  code_sha text,
  provenance jsonb not null default '{}'::jsonb check (jsonb_typeof(provenance) = 'object'),
  record jsonb not null check (jsonb_typeof(record) = 'object'),
  source_event_id uuid not null unique,
  source_event_key text not null,
  research_only boolean not null default true check (research_only = true),
  execution_authority boolean not null default false check (execution_authority = false),
  created_at timestamptz not null default now()
);

create table if not exists private.nostra_forecasts (
  forecast_id text primary key,
  snapshot_id text not null references private.nostra_snapshots(snapshot_id) on delete restrict,
  candidate_id bigint references private.trading_candidate_evaluations(candidate_id) on delete restrict,
  run_id uuid references private.trading_runs(run_id) on delete restrict,
  strategy_version_id text references private.trading_strategy_versions(version_id) on delete restrict,
  symbol text not null check (length(symbol) > 0),
  market_lane text not null check (length(market_lane) > 0),
  as_of_timestamp timestamptz not null,
  generated_at timestamptz not null,
  horizon_minutes integer not null check (horizon_minutes > 0),
  target_kind text not null check (target_kind in ('return','direction','regime','volatility','path')),
  model_id text not null check (length(model_id) > 0),
  model_version text not null check (length(model_version) > 0),
  feature_set_version text not null check (length(feature_set_version) > 0),
  calibration_version text,
  forecast_payload jsonb not null default '{}'::jsonb check (jsonb_typeof(forecast_payload) = 'object'),
  uncertainty jsonb not null default '{}'::jsonb check (jsonb_typeof(uncertainty) = 'object'),
  ood_score numeric check (ood_score is null or (ood_score >= 0 and ood_score <= 1)),
  authority_state text not null check (
    authority_state in ('NORMAL','LOW_SUPPORT','OOD','DATA_DEGRADED','MODEL_DEGRADED','ABSTAIN')
  ),
  methodology_version text not null check (length(methodology_version) > 0),
  code_sha text,
  provenance jsonb not null default '{}'::jsonb check (jsonb_typeof(provenance) = 'object'),
  supersedes_forecast_id text references private.nostra_forecasts(forecast_id) on delete restrict,
  record jsonb not null check (jsonb_typeof(record) = 'object'),
  source_event_id uuid not null unique,
  source_event_key text not null,
  research_only boolean not null default true check (research_only = true),
  execution_authority boolean not null default false check (execution_authority = false),
  created_at timestamptz not null default now(),
  check (generated_at >= as_of_timestamp),
  check (supersedes_forecast_id is null or supersedes_forecast_id <> forecast_id)
);

create table if not exists private.nostra_outcomes (
  outcome_id text primary key,
  forecast_id text not null references private.nostra_forecasts(forecast_id) on delete restrict,
  observed_at timestamptz not null,
  realized_payload jsonb not null default '{}'::jsonb check (jsonb_typeof(realized_payload) = 'object'),
  methodology_version text not null check (length(methodology_version) > 0),
  data_quality jsonb not null default '{}'::jsonb check (jsonb_typeof(data_quality) = 'object'),
  source_outcome_id bigint references private.trading_candidate_forward_outcomes(outcome_id) on delete restrict,
  provenance jsonb not null default '{}'::jsonb check (jsonb_typeof(provenance) = 'object'),
  supersedes_outcome_id text references private.nostra_outcomes(outcome_id) on delete restrict,
  record jsonb not null check (jsonb_typeof(record) = 'object'),
  source_event_id uuid not null unique,
  source_event_key text not null,
  research_only boolean not null default true check (research_only = true),
  execution_authority boolean not null default false check (execution_authority = false),
  created_at timestamptz not null default now(),
  check (supersedes_outcome_id is null or supersedes_outcome_id <> outcome_id)
);

create table if not exists private.nostra_scores (
  score_id text primary key,
  forecast_id text not null references private.nostra_forecasts(forecast_id) on delete restrict,
  outcome_id text not null references private.nostra_outcomes(outcome_id) on delete restrict,
  scoring_version text not null check (length(scoring_version) > 0),
  metrics jsonb not null default '{}'::jsonb check (jsonb_typeof(metrics) = 'object'),
  baseline_id text,
  baseline_version text,
  baseline_metrics jsonb not null default '{}'::jsonb check (jsonb_typeof(baseline_metrics) = 'object'),
  skill jsonb not null default '{}'::jsonb check (jsonb_typeof(skill) = 'object'),
  provenance jsonb not null default '{}'::jsonb check (jsonb_typeof(provenance) = 'object'),
  record jsonb not null check (jsonb_typeof(record) = 'object'),
  source_event_id uuid not null unique,
  source_event_key text not null,
  research_only boolean not null default true check (research_only = true),
  execution_authority boolean not null default false check (execution_authority = false),
  created_at timestamptz not null default now()
);

alter table private.nostra_snapshots enable row level security;
alter table private.nostra_forecasts enable row level security;
alter table private.nostra_outcomes enable row level security;
alter table private.nostra_scores enable row level security;

revoke all on table private.nostra_snapshots from anon, authenticated;
revoke all on table private.nostra_forecasts from anon, authenticated;
revoke all on table private.nostra_outcomes from anon, authenticated;
revoke all on table private.nostra_scores from anon, authenticated;

create index if not exists idx_nostra_snapshots_symbol_time
  on private.nostra_snapshots (market_lane, symbol, as_of_timestamp desc);
create index if not exists idx_nostra_snapshots_candidate
  on private.nostra_snapshots (candidate_id) where candidate_id is not null;
create index if not exists idx_nostra_snapshots_run
  on private.nostra_snapshots (run_id) where run_id is not null;
create index if not exists idx_nostra_snapshots_strategy
  on private.nostra_snapshots (strategy_version_id) where strategy_version_id is not null;

create index if not exists idx_nostra_forecasts_symbol_target_time
  on private.nostra_forecasts (market_lane, symbol, target_kind, horizon_minutes, generated_at desc);
create index if not exists idx_nostra_forecasts_snapshot
  on private.nostra_forecasts (snapshot_id);
create index if not exists idx_nostra_forecasts_candidate
  on private.nostra_forecasts (candidate_id) where candidate_id is not null;
create index if not exists idx_nostra_forecasts_run
  on private.nostra_forecasts (run_id) where run_id is not null;
create index if not exists idx_nostra_forecasts_strategy
  on private.nostra_forecasts (strategy_version_id) where strategy_version_id is not null;
create index if not exists idx_nostra_forecasts_supersedes
  on private.nostra_forecasts (supersedes_forecast_id) where supersedes_forecast_id is not null;

create index if not exists idx_nostra_outcomes_forecast
  on private.nostra_outcomes (forecast_id, observed_at desc);
create index if not exists idx_nostra_outcomes_source_outcome
  on private.nostra_outcomes (source_outcome_id) where source_outcome_id is not null;
create index if not exists idx_nostra_outcomes_supersedes
  on private.nostra_outcomes (supersedes_outcome_id) where supersedes_outcome_id is not null;

create index if not exists idx_nostra_scores_forecast
  on private.nostra_scores (forecast_id, scoring_version, created_at desc);
create index if not exists idx_nostra_scores_outcome
  on private.nostra_scores (outcome_id);

create or replace function private.reject_nostra_mutation()
returns trigger
language plpgsql
set search_path = private, pg_temp
as $$
begin
  raise exception 'NOSTRA_EVIDENCE_APPEND_ONLY: % on %.% is not allowed',
    tg_op, tg_table_schema, tg_table_name;
end;
$$;

create or replace function private.project_nostra_evidence()
returns trigger
language plpgsql
set search_path = private, pg_temp
as $$
declare
  p jsonb := coalesce(new.payload, '{}'::jsonb);
  v_forecast_generated_at timestamptz;
  v_outcome_forecast_id text;
begin
  if new.event_type = 'nostra_snapshot' then
    if nullif(p->>'snapshot_id','') is null then
      raise exception 'NOSTRA_SNAPSHOT_INVALID: snapshot_id is required';
    end if;
    if exists (
      select 1 from private.nostra_snapshots
      where snapshot_id = p->>'snapshot_id' and record <> p
    ) then
      raise exception 'NOSTRA_ID_COLLISION: snapshot % differs from existing record', p->>'snapshot_id';
    end if;

    insert into private.nostra_snapshots (
      snapshot_id, candidate_id, run_id, strategy_version_id, symbol, market_lane,
      as_of_timestamp, feature_set_version, raw_features, normalized_features,
      market_state, data_quality, source, code_sha, provenance, record,
      source_event_id, source_event_key, research_only, execution_authority
    ) values (
      p->>'snapshot_id',
      nullif(p->>'candidate_id','')::bigint,
      nullif(p->>'run_id','')::uuid,
      nullif(p->>'strategy_version_id',''),
      upper(p->>'symbol'),
      lower(p->>'market_lane'),
      (p->>'as_of_timestamp')::timestamptz,
      p->>'feature_set_version',
      coalesce(p->'raw_features','{}'::jsonb),
      coalesce(p->'normalized_features','{}'::jsonb),
      coalesce(p->'market_state','{}'::jsonb),
      coalesce(p->'data_quality','{}'::jsonb),
      coalesce(p->'source','{}'::jsonb),
      nullif(p->>'code_sha',''),
      coalesce(p->'provenance','{}'::jsonb),
      p,
      new.event_id,
      new.event_key,
      true,
      false
    )
    on conflict (snapshot_id) do nothing;

  elsif new.event_type = 'nostra_forecast' then
    if nullif(p->>'forecast_id','') is null or nullif(p->>'snapshot_id','') is null then
      raise exception 'NOSTRA_FORECAST_INVALID: forecast_id and snapshot_id are required';
    end if;
    if exists (
      select 1 from private.nostra_forecasts
      where forecast_id = p->>'forecast_id' and record <> p
    ) then
      raise exception 'NOSTRA_ID_COLLISION: forecast % differs from existing record', p->>'forecast_id';
    end if;

    insert into private.nostra_forecasts (
      forecast_id, snapshot_id, candidate_id, run_id, strategy_version_id,
      symbol, market_lane, as_of_timestamp, generated_at, horizon_minutes,
      target_kind, model_id, model_version, feature_set_version,
      calibration_version, forecast_payload, uncertainty, ood_score,
      authority_state, methodology_version, code_sha, provenance,
      supersedes_forecast_id, record, source_event_id, source_event_key,
      research_only, execution_authority
    ) values (
      p->>'forecast_id',
      p->>'snapshot_id',
      nullif(p->>'candidate_id','')::bigint,
      nullif(p->>'run_id','')::uuid,
      nullif(p->>'strategy_version_id',''),
      upper(p->>'symbol'),
      lower(p->>'market_lane'),
      (p->>'as_of_timestamp')::timestamptz,
      (p->>'generated_at')::timestamptz,
      (p->>'horizon_minutes')::integer,
      lower(p->>'target_kind'),
      p->>'model_id',
      p->>'model_version',
      p->>'feature_set_version',
      nullif(p->>'calibration_version',''),
      coalesce(p->'forecast_payload','{}'::jsonb),
      coalesce(p->'uncertainty','{}'::jsonb),
      nullif(p->>'ood_score','')::numeric,
      upper(p->>'authority_state'),
      p->>'methodology_version',
      nullif(p->>'code_sha',''),
      coalesce(p->'provenance','{}'::jsonb),
      nullif(p->>'supersedes_forecast_id',''),
      p,
      new.event_id,
      new.event_key,
      true,
      false
    )
    on conflict (forecast_id) do nothing;

  elsif new.event_type = 'nostra_outcome' then
    if nullif(p->>'outcome_id','') is null or nullif(p->>'forecast_id','') is null then
      raise exception 'NOSTRA_OUTCOME_INVALID: outcome_id and forecast_id are required';
    end if;

    select generated_at
    into v_forecast_generated_at
    from private.nostra_forecasts
    where forecast_id = p->>'forecast_id';

    if v_forecast_generated_at is null then
      raise exception 'NOSTRA_OUTCOME_INVALID: forecast % does not exist', p->>'forecast_id';
    end if;
    if (p->>'observed_at')::timestamptz < v_forecast_generated_at then
      raise exception 'NOSTRA_OUTCOME_INVALID: outcome precedes forecast generation';
    end if;
    if exists (
      select 1 from private.nostra_outcomes
      where outcome_id = p->>'outcome_id' and record <> p
    ) then
      raise exception 'NOSTRA_ID_COLLISION: outcome % differs from existing record', p->>'outcome_id';
    end if;

    insert into private.nostra_outcomes (
      outcome_id, forecast_id, observed_at, realized_payload,
      methodology_version, data_quality, source_outcome_id, provenance,
      supersedes_outcome_id, record, source_event_id, source_event_key,
      research_only, execution_authority
    ) values (
      p->>'outcome_id',
      p->>'forecast_id',
      (p->>'observed_at')::timestamptz,
      coalesce(p->'realized_payload','{}'::jsonb),
      p->>'methodology_version',
      coalesce(p->'data_quality','{}'::jsonb),
      nullif(p->>'source_outcome_id','')::bigint,
      coalesce(p->'provenance','{}'::jsonb),
      nullif(p->>'supersedes_outcome_id',''),
      p,
      new.event_id,
      new.event_key,
      true,
      false
    )
    on conflict (outcome_id) do nothing;

  elsif new.event_type = 'nostra_score' then
    if nullif(p->>'score_id','') is null
       or nullif(p->>'forecast_id','') is null
       or nullif(p->>'outcome_id','') is null then
      raise exception 'NOSTRA_SCORE_INVALID: score_id, forecast_id and outcome_id are required';
    end if;

    select forecast_id
    into v_outcome_forecast_id
    from private.nostra_outcomes
    where outcome_id = p->>'outcome_id';

    if v_outcome_forecast_id is null then
      raise exception 'NOSTRA_SCORE_INVALID: outcome % does not exist', p->>'outcome_id';
    end if;
    if v_outcome_forecast_id <> p->>'forecast_id' then
      raise exception 'NOSTRA_SCORE_INVALID: forecast/outcome lineage mismatch';
    end if;
    if exists (
      select 1 from private.nostra_scores
      where score_id = p->>'score_id' and record <> p
    ) then
      raise exception 'NOSTRA_ID_COLLISION: score % differs from existing record', p->>'score_id';
    end if;

    insert into private.nostra_scores (
      score_id, forecast_id, outcome_id, scoring_version, metrics,
      baseline_id, baseline_version, baseline_metrics, skill, provenance,
      record, source_event_id, source_event_key, research_only, execution_authority
    ) values (
      p->>'score_id',
      p->>'forecast_id',
      p->>'outcome_id',
      p->>'scoring_version',
      coalesce(p->'metrics','{}'::jsonb),
      nullif(p->>'baseline_id',''),
      nullif(p->>'baseline_version',''),
      coalesce(p->'baseline_metrics','{}'::jsonb),
      coalesce(p->'skill','{}'::jsonb),
      coalesce(p->'provenance','{}'::jsonb),
      p,
      new.event_id,
      new.event_key,
      true,
      false
    )
    on conflict (score_id) do nothing;
  end if;

  return new;
end;
$$;

revoke execute on function private.reject_nostra_mutation() from public, anon, authenticated;
revoke execute on function private.project_nostra_evidence() from public, anon, authenticated;

drop trigger if exists trg_nostra_snapshots_append_only on private.nostra_snapshots;
create trigger trg_nostra_snapshots_append_only
before update or delete on private.nostra_snapshots
for each row execute function private.reject_nostra_mutation();

drop trigger if exists trg_nostra_forecasts_append_only on private.nostra_forecasts;
create trigger trg_nostra_forecasts_append_only
before update or delete on private.nostra_forecasts
for each row execute function private.reject_nostra_mutation();

drop trigger if exists trg_nostra_outcomes_append_only on private.nostra_outcomes;
create trigger trg_nostra_outcomes_append_only
before update or delete on private.nostra_outcomes
for each row execute function private.reject_nostra_mutation();

drop trigger if exists trg_nostra_scores_append_only on private.nostra_scores;
create trigger trg_nostra_scores_append_only
before update or delete on private.nostra_scores
for each row execute function private.reject_nostra_mutation();

drop trigger if exists trg_project_nostra_evidence on private.trading_events;
create trigger trg_project_nostra_evidence
after insert on private.trading_events
for each row
when (new.event_type in ('nostra_snapshot','nostra_forecast','nostra_outcome','nostra_score'))
execute function private.project_nostra_evidence();

comment on table private.nostra_snapshots is
  'NOSTRA FWD-003 immutable point-in-time snapshots. Research-only; no execution authority.';
comment on table private.nostra_forecasts is
  'NOSTRA FWD-002 immutable forecast ledger. Corrections supersede; rows are never rewritten.';
comment on table private.nostra_outcomes is
  'NOSTRA immutable realized outcomes linked to pre-existing forecasts.';
comment on table private.nostra_scores is
  'NOSTRA FWD-006 immutable forecast scores and baseline-relative skill evidence.';
