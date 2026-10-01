-- ANEVUM Foundation v2
-- Migration 0001: canonical schema boundaries and core evidence/control tables.
-- Safe design principle: this migration creates new objects only.

begin;

create extension if not exists pgcrypto;

create schema if not exists anevum;
create schema if not exists iren;
create schema if not exists rhen;
create schema if not exists graen;
create schema if not exists velum;
create schema if not exists nostra;

create table if not exists anevum.systems (
    system_key text primary key,
    display_name text not null,
    authority text not null,
    enabled boolean not null default true,
    metadata jsonb not null default '{}'::jsonb,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

create table if not exists anevum.artifacts (
    artifact_id uuid primary key default gen_random_uuid(),
    system_key text references anevum.systems(system_key),
    artifact_type text not null,
    content_hash text not null,
    storage_uri text not null,
    content_type text,
    byte_size bigint,
    lineage jsonb not null default '{}'::jsonb,
    created_at timestamptz not null default now(),
    unique (content_hash, storage_uri)
);

create table if not exists anevum.audit_log (
    audit_id bigint generated always as identity primary key,
    occurred_at timestamptz not null default now(),
    actor_type text not null,
    actor_id text,
    system_key text,
    action text not null,
    object_type text,
    object_id text,
    correlation_id text,
    details jsonb not null default '{}'::jsonb
);

create index if not exists anevum_audit_log_occurred_at_idx
    on anevum.audit_log (occurred_at desc);
create index if not exists anevum_audit_log_system_idx
    on anevum.audit_log (system_key, occurred_at desc);

create table if not exists rhen.strategy_runs (
    run_id text primary key,
    strategy_version_id text not null,
    asset_class text not null,
    mode text not null,
    started_at timestamptz not null,
    ended_at timestamptz,
    source_commit text,
    configuration_hash text,
    configuration jsonb not null default '{}'::jsonb,
    status text not null default 'running',
    created_at timestamptz not null default now()
);

create table if not exists rhen.events (
    event_id bigint generated always as identity primary key,
    event_key text not null unique,
    run_id text references rhen.strategy_runs(run_id),
    strategy_version_id text,
    event_type text not null,
    occurred_at timestamptz not null,
    symbol text,
    correlation_id text,
    source text not null,
    payload jsonb not null default '{}'::jsonb,
    payload_hash text,
    ingested_at timestamptz not null default now()
);

create index if not exists rhen_events_occurred_at_idx
    on rhen.events (occurred_at desc);
create index if not exists rhen_events_type_time_idx
    on rhen.events (event_type, occurred_at desc);
create index if not exists rhen_events_run_time_idx
    on rhen.events (run_id, occurred_at);
create index if not exists rhen_events_correlation_idx
    on rhen.events (correlation_id)
    where correlation_id is not null;

create table if not exists rhen.orders (
    broker_order_id text primary key,
    run_id text references rhen.strategy_runs(run_id),
    client_order_id text,
    symbol text not null,
    side text,
    order_type text,
    status text not null,
    qty numeric,
    filled_qty numeric,
    limit_price numeric,
    stop_price numeric,
    submitted_at timestamptz,
    filled_at timestamptz,
    canceled_at timestamptz,
    broker_payload jsonb not null default '{}'::jsonb,
    observed_at timestamptz not null,
    updated_at timestamptz not null default now()
);

create index if not exists rhen_orders_run_idx on rhen.orders (run_id, submitted_at);
create index if not exists rhen_orders_symbol_idx on rhen.orders (symbol, submitted_at desc);

create table if not exists rhen.fills (
    fill_id text primary key,
    broker_order_id text references rhen.orders(broker_order_id),
    run_id text references rhen.strategy_runs(run_id),
    symbol text not null,
    side text,
    qty numeric not null,
    price numeric not null,
    filled_at timestamptz not null,
    broker_payload jsonb not null default '{}'::jsonb,
    observed_at timestamptz not null
);

create index if not exists rhen_fills_run_idx on rhen.fills (run_id, filled_at);
create index if not exists rhen_fills_symbol_idx on rhen.fills (symbol, filled_at desc);

create table if not exists iren.system_state (
    system_key text primary key,
    health text not null,
    state jsonb not null default '{}'::jsonb,
    revision bigint not null default 1,
    observed_at timestamptz not null,
    updated_at timestamptz not null default now()
);

create table if not exists iren.jobs (
    job_id uuid primary key default gen_random_uuid(),
    job_key text not null unique,
    owner_system text not null,
    workflow text not null,
    scheduled_for timestamptz,
    status text not null,
    attempt integer not null default 0,
    max_attempts integer not null default 1,
    lease_owner text,
    lease_until timestamptz,
    input jsonb not null default '{}'::jsonb,
    output jsonb,
    error jsonb,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    completed_at timestamptz
);

create index if not exists iren_jobs_due_idx
    on iren.jobs (status, scheduled_for)
    where status in ('queued','retry');

create table if not exists iren.commands (
    command_id uuid primary key default gen_random_uuid(),
    requested_at timestamptz not null default now(),
    requested_by text not null,
    target_system text not null,
    command text not null,
    arguments jsonb not null default '{}'::jsonb,
    status text not null default 'requested',
    result jsonb,
    approved_by text,
    approved_at timestamptz,
    completed_at timestamptz,
    correlation_id text
);

create table if not exists iren.incidents (
    incident_id uuid primary key default gen_random_uuid(),
    system_key text not null,
    severity text not null,
    incident_type text not null,
    status text not null default 'open',
    opened_at timestamptz not null default now(),
    resolved_at timestamptz,
    summary text not null,
    evidence jsonb not null default '{}'::jsonb,
    correlation_id text
);

create index if not exists iren_incidents_open_idx
    on iren.incidents (status, severity, opened_at desc);

create table if not exists graen.experiments (
    experiment_id uuid primary key default gen_random_uuid(),
    problem_id text not null,
    methodology_version text not null,
    hypothesis jsonb not null,
    corpus_spec jsonb not null,
    stage text not null,
    status text not null,
    source_commit text,
    created_at timestamptz not null default now(),
    completed_at timestamptz
);

create table if not exists graen.candidates (
    candidate_id text primary key,
    experiment_id uuid not null references graen.experiments(experiment_id),
    generation integer,
    specification jsonb not null,
    specification_hash text not null,
    created_at timestamptz not null default now(),
    unique (experiment_id, specification_hash)
);

create table if not exists graen.evaluations (
    evaluation_id uuid primary key default gen_random_uuid(),
    candidate_id text not null references graen.candidates(candidate_id),
    stage text not null,
    methodology_version text not null,
    result jsonb not null,
    evidence_hash text,
    evaluated_at timestamptz not null default now()
);

create table if not exists graen.decisions (
    decision_id uuid primary key default gen_random_uuid(),
    experiment_id uuid not null references graen.experiments(experiment_id),
    candidate_id text references graen.candidates(candidate_id),
    decision text not null,
    rationale jsonb not null,
    methodology_version text not null,
    decided_at timestamptz not null default now()
);

create table if not exists velum.replays (
    replay_id text primary key,
    asset_class text not null,
    methodology_version text not null,
    strategy_version_id text,
    range_start timestamptz not null,
    range_end timestamptz not null,
    dataset_hash text not null,
    assumptions jsonb not null,
    source_commit text,
    random_seed bigint,
    started_at timestamptz not null default now(),
    completed_at timestamptz,
    status text not null
);

create table if not exists velum.results (
    result_id uuid primary key default gen_random_uuid(),
    replay_id text not null references velum.replays(replay_id),
    result_type text not null,
    summary jsonb not null,
    evidence_hash text,
    artifact_id uuid references anevum.artifacts(artifact_id),
    created_at timestamptz not null default now()
);

create table if not exists nostra.forecasts (
    forecast_id uuid primary key default gen_random_uuid(),
    forecast_key text not null unique,
    model_version text not null,
    methodology_version text not null,
    subject text not null,
    horizon_start timestamptz not null,
    horizon_end timestamptz not null,
    issued_at timestamptz not null,
    prediction jsonb not null,
    features_hash text,
    source_commit text,
    created_at timestamptz not null default now()
);

create table if not exists nostra.outcomes (
    outcome_id uuid primary key default gen_random_uuid(),
    forecast_id uuid not null unique references nostra.forecasts(forecast_id),
    observed_at timestamptz not null,
    outcome jsonb not null,
    scoring jsonb,
    created_at timestamptz not null default now()
);

create table if not exists nostra.calibration_runs (
    calibration_id uuid primary key default gen_random_uuid(),
    model_version text not null,
    methodology_version text not null,
    range_start timestamptz not null,
    range_end timestamptz not null,
    sample_count integer not null,
    metrics jsonb not null,
    evidence_hash text,
    created_at timestamptz not null default now()
);

insert into anevum.systems (system_key, display_name, authority)
values
    ('IREN', 'IREN', 'orchestration and control plane'),
    ('RHEN', 'RHEN', 'market execution and trading evidence'),
    ('GRAEN', 'GRAEN', 'mathematical and research development'),
    ('VELUM', 'VELUM', 'replay and simulation'),
    ('NOSTRA', 'NOSTRA', 'forecasting and calibration')
on conflict (system_key) do update
set display_name = excluded.display_name,
    authority = excluded.authority,
    updated_at = now();

commit;
