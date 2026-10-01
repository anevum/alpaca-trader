-- ANEVUM Foundation v2
-- Migration 0003: IREN deterministic control state, incident outbox, and scheduler ledger.
-- Replaces Supabase scheduler-gateway persistence without changing workflow authority.

begin;

alter table iren.system_state
    add column if not exists observation_key text;

create unique index if not exists iren_system_state_observation_key_idx
    on iren.system_state (observation_key)
    where observation_key is not null;

create table if not exists iren.control_events (
    event_key text primary key,
    revision bigint not null,
    event jsonb not null,
    created_at timestamptz not null default now(),
    delivery_status text not null default 'PENDING',
    attempts integer not null default 0,
    owner uuid,
    lease_until timestamptz,
    constraint iren_control_events_event_object
        check (jsonb_typeof(event) = 'object')
);

create index if not exists iren_control_events_pending_idx
    on iren.control_events (created_at)
    where delivery_status not like 'delivered:%';

create table if not exists iren.scheduler_runs (
    run_id uuid primary key default gen_random_uuid(),
    job_key text not null unique,
    workflow_id text not null,
    workflow_version text not null,
    scheduler_version text not null,
    scheduled_at timestamptz not null,
    started_at timestamptz not null default now(),
    completed_at timestamptz,
    status text not null default 'RUNNING',
    trigger_type text not null,
    attempt integer not null default 1,
    max_attempts integer not null default 1,
    worker_identity text,
    source_commit text,
    input_identity text,
    output_identity text,
    error_classification text,
    error_summary jsonb not null default '{}'::jsonb,
    retry_state text,
    catchup_state text,
    slack_notification_status text,
    details jsonb not null default '{}'::jsonb,
    lease_until timestamptz,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    constraint iren_scheduler_runs_status_check
        check (status in ('RUNNING','SUCCEEDED','FAILED','MISSED','SKIPPED','STALE')),
    constraint iren_scheduler_runs_attempt_check
        check (attempt >= 1 and max_attempts >= 1 and attempt <= max_attempts),
    constraint iren_scheduler_runs_error_summary_check
        check (jsonb_typeof(error_summary) = 'object'),
    constraint iren_scheduler_runs_details_check
        check (jsonb_typeof(details) = 'object')
);

create index if not exists iren_scheduler_runs_workflow_scheduled_idx
    on iren.scheduler_runs (workflow_id, scheduled_at desc);

create index if not exists iren_scheduler_runs_status_scheduled_idx
    on iren.scheduler_runs (status, scheduled_at desc);

create index if not exists iren_scheduler_runs_lease_idx
    on iren.scheduler_runs (lease_until)
    where status = 'RUNNING';

commit;
