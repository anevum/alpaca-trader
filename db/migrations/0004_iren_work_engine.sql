-- ANEVUM Foundation v2
-- Migration 0004: IREN durable work engine.
-- Extends canonical iren.commands / iren.jobs instead of creating duplicate stores.

begin;

create table if not exists iren.objectives (
    objective_key text primary key,
    parent_key text references iren.objectives(objective_key),
    title text not null,
    description text not null default '',
    status text not null,
    owner_system text not null default 'IREN',
    priority integer not null default 0,
    dependencies jsonb not null default '[]'::jsonb,
    success_criteria jsonb not null default '{}'::jsonb,
    protected_action boolean not null default false,
    metadata jsonb not null default '{}'::jsonb,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    completed_at timestamptz,
    constraint iren_objectives_dependencies_array
        check (jsonb_typeof(dependencies) = 'array'),
    constraint iren_objectives_success_object
        check (jsonb_typeof(success_criteria) = 'object'),
    constraint iren_objectives_metadata_object
        check (jsonb_typeof(metadata) = 'object')
);

create index if not exists iren_objectives_status_priority_idx
    on iren.objectives (status, priority desc, created_at);

alter table iren.commands
    add column if not exists command_text text,
    add column if not exists source text not null default 'iren',
    add column if not exists context jsonb not null default '{}'::jsonb,
    add column if not exists owner text,
    add column if not exists lease_until timestamptz,
    add column if not exists linked_job_id uuid,
    add column if not exists created_at timestamptz not null default now(),
    add column if not exists updated_at timestamptz not null default now();

update iren.commands
set command_text = command
where command_text is null;

alter table iren.jobs
    add column if not exists objective_key text references iren.objectives(objective_key),
    add column if not exists title text,
    add column if not exists instructions text not null default '',
    add column if not exists job_type text not null default 'AGENT_WORK',
    add column if not exists priority integer not null default 0,
    add column if not exists protected_action boolean not null default false,
    add column if not exists requires_human boolean not null default false,
    add column if not exists requested_by text,
    add column if not exists requested_via text not null default 'iren',
    add column if not exists started_at timestamptz,
    add column if not exists metadata jsonb not null default '{}'::jsonb;

create index if not exists iren_jobs_work_queue_idx
    on iren.jobs (status, priority desc, created_at)
    where status in ('QUEUED','RUNNING');

create table if not exists iren.job_events (
    event_id bigint generated always as identity primary key,
    job_id uuid not null references iren.jobs(job_id),
    event_type text not null,
    event jsonb not null default '{}'::jsonb,
    created_at timestamptz not null default now(),
    constraint iren_job_events_object
        check (jsonb_typeof(event) = 'object')
);

create index if not exists iren_job_events_job_idx
    on iren.job_events (job_id, created_at);

create table if not exists iren.settings (
    singleton boolean primary key default true check (singleton),
    autopilot_enabled boolean not null default false,
    autopilot_max_jobs_per_day integer not null default 3,
    model_execution_authorized boolean not null default false,
    updated_by text,
    updated_at timestamptz not null default now(),
    constraint iren_settings_job_cap
        check (autopilot_max_jobs_per_day between 1 and 12)
);

insert into iren.settings (singleton)
values (true)
on conflict (singleton) do nothing;

insert into iren.objectives (
    objective_key,
    title,
    description,
    status,
    owner_system,
    priority,
    dependencies,
    success_criteria,
    protected_action,
    metadata
)
values (
    'anevum.foundation.v2',
    'ANEVUM Foundation v2',
    'Complete the ground-up migration to GitHub + Railway PostgreSQL + Cloudflare, removing Supabase from required runtime paths while preserving production safety.',
    'ACTIVE',
    'IREN',
    100,
    '[]'::jsonb,
    '{"supabase_required_dependencies":0,"production_health_verified":true}'::jsonb,
    false,
    '{"classification":"ACTIVE","source":"master-plan-2026-10-01"}'::jsonb
)
on conflict (objective_key) do nothing;

commit;
