-- ANEVUM Foundation v2
-- Migration 0005: GRAEN durable gateway persistence.
-- Replaces the Supabase graen-gateway with canonical Railway PostgreSQL state.

begin;

create table if not exists graen.problems (
    problem_id uuid primary key default gen_random_uuid(),
    problem_key text not null unique,
    title text not null,
    statement text not null,
    domain text not null default 'GENERAL_RESEARCH',
    status text not null default 'QUEUED',
    priority integer not null default 50,
    source text not null default 'IREN',
    requested_by text,
    linked_iren_job_id uuid references iren.jobs(job_id),
    constraints jsonb not null default '{}'::jsonb,
    success_criteria jsonb not null default '{}'::jsonb,
    metadata jsonb not null default '{}'::jsonb,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    started_at timestamptz,
    completed_at timestamptz,
    constraint graen_problems_status_check
        check (status in ('QUEUED','RUNNING','WAITING','BLOCKED','SUCCEEDED','FAILED','CANCELLED')),
    constraint graen_problems_constraints_object
        check (jsonb_typeof(constraints)='object'),
    constraint graen_problems_success_object
        check (jsonb_typeof(success_criteria)='object'),
    constraint graen_problems_metadata_object
        check (jsonb_typeof(metadata)='object')
);

create index if not exists graen_problems_queue_idx
    on graen.problems (status, priority desc, created_at);

create index if not exists graen_problems_domain_idx
    on graen.problems (domain, status, priority desc, created_at);

create table if not exists graen.runs (
    run_id uuid primary key default gen_random_uuid(),
    problem_id uuid not null references graen.problems(problem_id),
    worker_id text not null,
    runtime_version text not null,
    source_commit text,
    deployment_id text,
    methodology_version text,
    status text not null,
    input_snapshot jsonb not null default '{}'::jsonb,
    result_summary jsonb not null default '{}'::jsonb,
    model_usage jsonb not null default '{}'::jsonb,
    started_at timestamptz not null default now(),
    completed_at timestamptz,
    created_at timestamptz not null default now(),
    constraint graen_runs_status_check
        check (status in ('QUEUED','RUNNING','WAITING','BLOCKED','SUCCEEDED','FAILED','CANCELLED')),
    constraint graen_runs_input_object
        check (jsonb_typeof(input_snapshot)='object'),
    constraint graen_runs_result_object
        check (jsonb_typeof(result_summary)='object'),
    constraint graen_runs_usage_object
        check (jsonb_typeof(model_usage)='object')
);

create index if not exists graen_runs_problem_idx
    on graen.runs (problem_id, started_at desc);

create index if not exists graen_runs_running_idx
    on graen.runs (problem_id)
    where status='RUNNING';

create table if not exists graen.artifacts (
    artifact_id uuid primary key default gen_random_uuid(),
    problem_id uuid not null references graen.problems(problem_id),
    run_id uuid references graen.runs(run_id),
    artifact_key text not null unique,
    artifact_type text not null,
    methodology_version text,
    source_commit text,
    content_hash text not null,
    content jsonb not null default '{}'::jsonb,
    created_at timestamptz not null default now(),
    constraint graen_artifacts_content_object
        check (jsonb_typeof(content)='object')
);

create index if not exists graen_artifacts_problem_idx
    on graen.artifacts (problem_id, created_at desc);

create index if not exists graen_artifacts_type_idx
    on graen.artifacts (artifact_type, created_at desc);

create table if not exists graen.runtime_state (
    singleton boolean primary key default true check (singleton),
    worker_id text,
    runtime_version text,
    source_commit text,
    deployment_id text,
    started_at timestamptz not null default now(),
    heartbeat_at timestamptz,
    last_claim_at timestamptz,
    last_completion_at timestamptz,
    active_problem_id uuid references graen.problems(problem_id),
    queue_depth integer not null default 0,
    last_error text,
    metadata jsonb not null default '{}'::jsonb,
    updated_at timestamptz not null default now(),
    constraint graen_runtime_metadata_object
        check (jsonb_typeof(metadata)='object')
);

insert into graen.runtime_state (singleton)
values (true)
on conflict (singleton) do nothing;

commit;
