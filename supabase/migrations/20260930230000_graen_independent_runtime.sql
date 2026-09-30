create table if not exists private.graen_problems (
  problem_id uuid primary key default gen_random_uuid(),
  problem_key text not null unique,
  title text not null,
  statement text not null,
  domain text not null default 'GENERAL_RESEARCH',
  status text not null default 'QUEUED'
    check (status in ('QUEUED','RUNNING','WAITING','BLOCKED','SUCCEEDED','FAILED','CANCELLED')),
  priority integer not null default 50,
  source text not null default 'IREN',
  requested_by text,
  linked_iren_job_id uuid references private.iren_jobs(job_id) on delete set null,
  constraints jsonb not null default '{}'::jsonb
    check (jsonb_typeof(constraints) = 'object'),
  success_criteria jsonb not null default '{}'::jsonb
    check (jsonb_typeof(success_criteria) = 'object'),
  metadata jsonb not null default '{}'::jsonb
    check (jsonb_typeof(metadata) = 'object'),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  started_at timestamptz,
  completed_at timestamptz
);

create index if not exists graen_problems_queue_idx
  on private.graen_problems (status, priority desc, created_at);

create index if not exists graen_problems_iren_job_idx
  on private.graen_problems (linked_iren_job_id)
  where linked_iren_job_id is not null;

create table if not exists private.graen_runs (
  run_id uuid primary key default gen_random_uuid(),
  problem_id uuid not null references private.graen_problems(problem_id) on delete cascade,
  worker_id text not null,
  runtime_version text not null,
  source_commit text,
  deployment_id text,
  methodology_version text,
  status text not null
    check (status in ('RUNNING','WAITING','BLOCKED','SUCCEEDED','FAILED','CANCELLED')),
  input_snapshot jsonb not null default '{}'::jsonb
    check (jsonb_typeof(input_snapshot) = 'object'),
  result_summary jsonb not null default '{}'::jsonb
    check (jsonb_typeof(result_summary) = 'object'),
  model_usage jsonb not null default '{"invoked":false}'::jsonb
    check (jsonb_typeof(model_usage) = 'object'),
  started_at timestamptz not null default now(),
  completed_at timestamptz,
  created_at timestamptz not null default now()
);

create index if not exists graen_runs_problem_idx
  on private.graen_runs (problem_id, started_at desc);

create table if not exists private.graen_artifacts (
  artifact_id uuid primary key default gen_random_uuid(),
  problem_id uuid not null references private.graen_problems(problem_id) on delete cascade,
  run_id uuid references private.graen_runs(run_id) on delete set null,
  artifact_key text not null unique,
  artifact_type text not null,
  methodology_version text,
  source_commit text,
  content_hash text not null,
  content jsonb not null
    check (jsonb_typeof(content) = 'object'),
  created_at timestamptz not null default now()
);

create index if not exists graen_artifacts_problem_idx
  on private.graen_artifacts (problem_id, created_at desc);

create table if not exists private.graen_runtime_state (
  singleton boolean primary key default true check (singleton),
  worker_id text,
  runtime_version text,
  source_commit text,
  deployment_id text,
  started_at timestamptz,
  heartbeat_at timestamptz,
  last_claim_at timestamptz,
  last_completion_at timestamptz,
  active_problem_id uuid references private.graen_problems(problem_id) on delete set null,
  queue_depth integer not null default 0,
  last_error text,
  metadata jsonb not null default '{}'::jsonb
    check (jsonb_typeof(metadata) = 'object'),
  updated_at timestamptz not null default now()
);

insert into private.graen_runtime_state (singleton)
values (true)
on conflict (singleton) do nothing;

alter table private.graen_problems enable row level security;
alter table private.graen_runs enable row level security;
alter table private.graen_artifacts enable row level security;
alter table private.graen_runtime_state enable row level security;

revoke all on table private.graen_problems from public, anon, authenticated;
revoke all on table private.graen_runs from public, anon, authenticated;
revoke all on table private.graen_artifacts from public, anon, authenticated;
revoke all on table private.graen_runtime_state from public, anon, authenticated;
