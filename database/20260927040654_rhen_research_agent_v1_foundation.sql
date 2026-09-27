-- RHEN Research Agent v1 deterministic foundation.
-- Additive only: no live strategy, execution, risk, sizing, market-data, or broker changes.

create table private.trading_research_agent_runs (
  run_id uuid primary key default gen_random_uuid(),
  run_key text not null unique,
  agent_version text not null,
  source_commit text not null,
  trigger text not null,
  trigger_reference text,
  started_at timestamptz not null,
  completed_at timestamptz,
  evidence_cutoff timestamptz,
  input_artifacts jsonb not null default '[]'::jsonb,
  input_fingerprint text not null,
  open_question_ids text[] not null default '{}'::text[],
  proposed_actions jsonb not null default '[]'::jsonb,
  actions_taken jsonb not null default '[]'::jsonb,
  tools_invoked jsonb not null default '[]'::jsonb,
  experiment_ids uuid[] not null default '{}'::uuid[],
  decision_ids uuid[] not null default '{}'::uuid[],
  output_artifact jsonb,
  approval_required boolean not null default false,
  authorization_reference text,
  status text not null,
  error_summary jsonb,
  rationale_summary jsonb,
  llm_usage jsonb not null default '{"invoked":false}'::jsonb,
  operator_identity text,
  created_at timestamptz not null default now(),
  constraint trading_research_agent_runs_status_check
    check (status in ('RUNNING','COMPLETED','NOOP','BLOCKED','FAILED')),
  constraint trading_research_agent_runs_completion_check
    check (
      (status = 'RUNNING' and completed_at is null)
      or (status <> 'RUNNING' and completed_at is not null)
    ),
  constraint trading_research_agent_runs_json_shape_check
    check (
      jsonb_typeof(input_artifacts) = 'array'
      and jsonb_typeof(proposed_actions) = 'array'
      and jsonb_typeof(actions_taken) = 'array'
      and jsonb_typeof(tools_invoked) = 'array'
      and (output_artifact is null or jsonb_typeof(output_artifact) in ('object','array'))
      and (error_summary is null or jsonb_typeof(error_summary) = 'object')
      and (rationale_summary is null or jsonb_typeof(rationale_summary) = 'object')
      and jsonb_typeof(llm_usage) = 'object'
      and llm_usage ? 'invoked'
      and jsonb_typeof(llm_usage->'invoked') = 'boolean'
    )
);

create index trading_research_agent_runs_started_idx
  on private.trading_research_agent_runs(started_at desc);
create index trading_research_agent_runs_status_started_idx
  on private.trading_research_agent_runs(status, started_at desc);
create index trading_research_agent_runs_trigger_fingerprint_idx
  on private.trading_research_agent_runs(trigger, input_fingerprint, started_at desc);

alter table private.trading_research_agent_runs enable row level security;
revoke all on private.trading_research_agent_runs from anon, authenticated;

create table private.trading_research_leases (
  lease_key text primary key,
  holder_run_id uuid not null
    references private.trading_research_agent_runs(run_id),
  operator_identity text,
  acquired_at timestamptz not null,
  heartbeat_at timestamptz not null,
  expires_at timestamptz not null,
  scope jsonb not null default '{}'::jsonb,
  takeover_from_run_id uuid
    references private.trading_research_agent_runs(run_id),
  constraint trading_research_leases_time_check
    check (heartbeat_at >= acquired_at and expires_at > heartbeat_at),
  constraint trading_research_leases_scope_check
    check (jsonb_typeof(scope) = 'object'),
  constraint trading_research_leases_takeover_check
    check (takeover_from_run_id is null or takeover_from_run_id <> holder_run_id)
);

create index trading_research_leases_holder_idx
  on private.trading_research_leases(holder_run_id);
create index trading_research_leases_expires_idx
  on private.trading_research_leases(expires_at);

alter table private.trading_research_leases enable row level security;
revoke all on private.trading_research_leases from anon, authenticated;

alter table private.trading_research_questions
  alter column source_weekly_report_id drop not null,
  add column category text,
  add column priority_score integer,
  add column evidence_cutoff timestamptz,
  add column next_action text,
  add column last_reviewed_at timestamptz,
  add column source_agent_run_id uuid
    references private.trading_research_agent_runs(run_id);

alter table private.trading_research_questions
  add constraint trading_research_questions_category_check
    check (
      category is null or category in (
        'OPERATIONAL_DEFECT',
        'DATA_QUALITY',
        'STRATEGY_HYPOTHESIS',
        'RISK_SIZING_OBSERVATION',
        'NOISE_INSUFFICIENT'
      )
    ),
  add constraint trading_research_questions_priority_score_check
    check (priority_score is null or priority_score between 0 and 100),
  add constraint trading_research_questions_source_check
    check (source_weekly_report_id is null or source_agent_run_id is null);

create index trading_research_questions_agent_run_idx
  on private.trading_research_questions(source_agent_run_id)
  where source_agent_run_id is not null;
create unique index trading_research_questions_agent_snapshot_uidx
  on private.trading_research_questions(source_agent_run_id, research_question_id)
  where source_agent_run_id is not null;
create index trading_research_questions_queue_idx
  on private.trading_research_questions(status, priority_score desc, last_reviewed_at, created_at desc);

alter table private.trading_experiments
  add column workflow_state text;

alter table private.trading_experiments
  add constraint trading_experiments_workflow_state_check
    check (
      workflow_state is null or workflow_state in (
        'FROZEN',
        'DEVELOPMENT_RUNNING',
        'DEVELOPMENT_COMPLETE',
        'VALIDATION_RUNNING',
        'VALIDATION_COMPLETE',
        'HOLDOUT_RUNNING',
        'HISTORICAL_COMPLETE',
        'CHALLENGER_CANDIDATE',
        'ARCHIVED'
      )
    );

create index trading_experiments_workflow_state_idx
  on private.trading_experiments(workflow_state, created_at desc)
  where workflow_state is not null;

create function private.rhen_research_lease_acquire(
  p_lease_key text,
  p_holder_run_id uuid,
  p_operator_identity text default null,
  p_ttl interval default interval '30 minutes',
  p_scope jsonb default '{}'::jsonb,
  p_takeover_from_run_id uuid default null
)
returns setof private.trading_research_leases
language plpgsql
security invoker
set search_path = private, pg_temp
as $function$
declare
  v_now timestamptz := clock_timestamp();
begin
  if coalesce(btrim(p_lease_key), '') = '' then
    raise exception 'lease_key is required';
  end if;
  if p_holder_run_id is null then
    raise exception 'holder_run_id is required';
  end if;
  if p_ttl <= interval '0 seconds' then
    raise exception 'lease ttl must be positive';
  end if;
  if jsonb_typeof(coalesce(p_scope, '{}'::jsonb)) <> 'object' then
    raise exception 'lease scope must be a JSON object';
  end if;

  return query
  insert into private.trading_research_leases as lease (
    lease_key, holder_run_id, operator_identity, acquired_at,
    heartbeat_at, expires_at, scope, takeover_from_run_id
  ) values (
    p_lease_key, p_holder_run_id, p_operator_identity, v_now,
    v_now, v_now + p_ttl, coalesce(p_scope, '{}'::jsonb), null
  )
  on conflict (lease_key) do update
  set holder_run_id = excluded.holder_run_id,
      operator_identity = excluded.operator_identity,
      acquired_at = excluded.acquired_at,
      heartbeat_at = excluded.heartbeat_at,
      expires_at = excluded.expires_at,
      scope = excluded.scope,
      takeover_from_run_id = case
        when lease.holder_run_id = excluded.holder_run_id then null
        else coalesce(p_takeover_from_run_id, lease.holder_run_id)
      end
  where lease.expires_at <= v_now
    and (
      p_takeover_from_run_id is null
      or p_takeover_from_run_id = lease.holder_run_id
    )
  returning lease.*;
end;
$function$;

create function private.rhen_research_lease_heartbeat(
  p_lease_key text,
  p_holder_run_id uuid,
  p_ttl interval default interval '30 minutes'
)
returns boolean
language plpgsql
security invoker
set search_path = private, pg_temp
as $function$
declare
  v_now timestamptz := clock_timestamp();
  v_rows integer;
begin
  if p_ttl <= interval '0 seconds' then
    raise exception 'lease ttl must be positive';
  end if;
  update private.trading_research_leases
  set heartbeat_at = v_now,
      expires_at = v_now + p_ttl
  where lease_key = p_lease_key
    and holder_run_id = p_holder_run_id
    and expires_at > v_now;
  get diagnostics v_rows = row_count;
  return v_rows = 1;
end;
$function$;

create function private.rhen_research_lease_renew(
  p_lease_key text,
  p_holder_run_id uuid,
  p_ttl interval default interval '30 minutes'
)
returns boolean
language sql
volatile
security invoker
set search_path = private, pg_temp
as $function$
  select private.rhen_research_lease_heartbeat(
    p_lease_key,
    p_holder_run_id,
    p_ttl
  );
$function$;

create function private.rhen_research_lease_release(
  p_lease_key text,
  p_holder_run_id uuid
)
returns boolean
language plpgsql
security invoker
set search_path = private, pg_temp
as $function$
declare
  v_rows integer;
begin
  delete from private.trading_research_leases
  where lease_key = p_lease_key
    and holder_run_id = p_holder_run_id;
  get diagnostics v_rows = row_count;
  return v_rows = 1;
end;
$function$;

revoke execute on function private.rhen_research_lease_acquire(text,uuid,text,interval,jsonb,uuid)
  from public, anon, authenticated;
revoke execute on function private.rhen_research_lease_heartbeat(text,uuid,interval)
  from public, anon, authenticated;
revoke execute on function private.rhen_research_lease_renew(text,uuid,interval)
  from public, anon, authenticated;
revoke execute on function private.rhen_research_lease_release(text,uuid)
  from public, anon, authenticated;
