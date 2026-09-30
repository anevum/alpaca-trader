create table private.anevum_scheduler_runs (
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
  constraint anevum_scheduler_runs_status_check
    check (status in ('RUNNING','SUCCEEDED','FAILED','MISSED','SKIPPED','STALE')),
  constraint anevum_scheduler_runs_attempt_check
    check (attempt >= 1 and max_attempts >= 1 and attempt <= max_attempts),
  constraint anevum_scheduler_runs_error_summary_check
    check (jsonb_typeof(error_summary) = 'object'),
  constraint anevum_scheduler_runs_details_check
    check (jsonb_typeof(details) = 'object')
);

create index anevum_scheduler_runs_workflow_scheduled_idx
  on private.anevum_scheduler_runs(workflow_id, scheduled_at desc);

create index anevum_scheduler_runs_status_scheduled_idx
  on private.anevum_scheduler_runs(status, scheduled_at desc);

create index anevum_scheduler_runs_lease_idx
  on private.anevum_scheduler_runs(lease_until)
  where status = 'RUNNING';

alter table private.anevum_scheduler_runs enable row level security;
revoke all on private.anevum_scheduler_runs from anon, authenticated;

comment on table private.anevum_scheduler_runs is
  'Canonical ANEVUM recurring-work execution ledger. Idempotent job keys prevent duplicate scheduled work; rows carry no live trading authority.';
