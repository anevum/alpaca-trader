-- ANEVUM Foundation v2
-- Migration 0006: RHEN research-agent canonical evidence and search ledger.
-- Additive only. No live strategy, risk, sizing, or broker behavior changes.

begin;

create table if not exists rhen.research_questions (
    question_record_id uuid primary key default gen_random_uuid(),
    research_question_id text not null,
    created_on date,
    question text not null,
    why_it_matters text not null default '',
    status text not null default 'OPEN',
    evidence_summary jsonb not null default '{}'::jsonb,
    sample_size integer not null default 0,
    required_data jsonb not null default '[]'::jsonb,
    source_weekly_report_id uuid,
    source_agent_run_id uuid,
    linked_experiment_id uuid,
    category text,
    priority_score integer,
    evidence_cutoff timestamptz,
    next_action text,
    last_reviewed_at timestamptz,
    created_at timestamptz not null default now(),
    constraint research_questions_evidence_object
        check (jsonb_typeof(evidence_summary)='object'),
    constraint research_questions_required_array
        check (jsonb_typeof(required_data)='array')
);

create index if not exists rhen_research_questions_latest_idx
    on rhen.research_questions (research_question_id, created_at desc);

create table if not exists rhen.research_experiments (
    experiment_id uuid primary key default gen_random_uuid(),
    experiment_key text not null,
    payload jsonb not null,
    created_at timestamptz not null default now(),
    constraint research_experiments_payload_object
        check (jsonb_typeof(payload)='object')
);

create index if not exists rhen_research_experiments_latest_idx
    on rhen.research_experiments (experiment_key, created_at desc);

create table if not exists rhen.research_decisions (
    decision_id uuid primary key default gen_random_uuid(),
    decision_key text not null,
    status text not null,
    decided_at timestamptz not null,
    payload jsonb not null,
    created_at timestamptz not null default now(),
    constraint research_decisions_payload_object
        check (jsonb_typeof(payload)='object')
);

create index if not exists rhen_research_decisions_latest_idx
    on rhen.research_decisions (decision_key, decided_at desc);

create table if not exists rhen.research_agent_runs (
    run_id uuid primary key,
    run_key text not null unique,
    agent_version text not null,
    source_commit text not null,
    trigger text not null,
    trigger_reference text,
    started_at timestamptz not null,
    completed_at timestamptz not null,
    evidence_cutoff timestamptz,
    input_artifacts jsonb not null default '[]'::jsonb,
    input_fingerprint text not null,
    proposed_actions jsonb not null default '[]'::jsonb,
    actions_taken jsonb not null default '[]'::jsonb,
    tools_invoked jsonb not null default '[]'::jsonb,
    output_artifact jsonb,
    approval_required boolean not null default false,
    authorization_reference text,
    status text not null,
    error_summary jsonb,
    rationale_summary jsonb,
    llm_usage jsonb not null default '{}'::jsonb,
    operator_identity text,
    created_at timestamptz not null default now(),
    constraint research_agent_runs_status_check
        check (status in ('COMPLETED','NOOP','BLOCKED','FAILED')),
    constraint research_agent_runs_inputs_array
        check (jsonb_typeof(input_artifacts)='array'),
    constraint research_agent_runs_proposals_array
        check (jsonb_typeof(proposed_actions)='array'),
    constraint research_agent_runs_actions_array
        check (jsonb_typeof(actions_taken)='array'),
    constraint research_agent_runs_tools_array
        check (jsonb_typeof(tools_invoked)='array'),
    constraint research_agent_runs_llm_object
        check (jsonb_typeof(llm_usage)='object')
);

create index if not exists rhen_research_agent_runs_started_idx
    on rhen.research_agent_runs (started_at desc);

create table if not exists rhen.research_search_ledgers (
    ledger_id uuid primary key default gen_random_uuid(),
    ledger_version text not null,
    proposal_id text not null,
    proposal_revision integer not null,
    proposal_hash text not null,
    source_agent_run_id uuid,
    source_commit text,
    family_id uuid,
    candidate_variant_count integer not null default 0,
    search_generation integer not null default 0,
    production_authority boolean not null default false,
    protected_stage_authority boolean not null default false,
    ledger_hash text not null unique,
    payload jsonb not null,
    recorded_at timestamptz not null default now(),
    unique (proposal_id, proposal_revision),
    constraint research_search_ledgers_payload_object
        check (jsonb_typeof(payload)='object'),
    constraint research_search_ledgers_no_production_authority
        check (production_authority=false),
    constraint research_search_ledgers_no_stage_authority
        check (protected_stage_authority=false)
);

create index if not exists rhen_research_search_ledgers_recorded_idx
    on rhen.research_search_ledgers (recorded_at desc);

commit;
