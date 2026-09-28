-- MATH-001-D // durable dependence-plan registry.
-- Research-only metadata. No strategy, risk, sizing, execution, broker,
-- protected-stage, quarantine, methodology-freeze, or promotion authority.

create table private.trading_research_dependence_plans (
  plan_id uuid primary key,
  plan_hash text not null unique,
  plan_version text not null,
  proposal_id text not null,
  proposal_revision integer not null,
  proposal_hash text not null,
  family_id uuid not null
    references private.trading_research_hypothesis_families(family_id),
  source_agent_run_id uuid
    references private.trading_research_agent_runs(run_id),
  source_commit text,
  outcome_contract text not null,
  null_contract text not null,
  iid_required boolean not null default false,
  level_autocorrelation_policy text not null,
  heteroskedasticity_policy text not null,
  cross_candidate_dependence_policy jsonb not null default '{}'::jsonb,
  heavy_tail_policy text not null,
  overlap_policy text not null,
  required_checks text[] not null,
  present_checks text[] not null,
  missing_checks text[] not null,
  dependence_ready boolean not null,
  policy_status text not null,
  production_authority boolean not null default false,
  protected_stage_authority boolean not null default false,
  created_at timestamptz not null,
  recorded_at timestamptz not null default now(),
  constraint trading_research_dependence_plans_hash_check
    check (plan_hash ~ '^[0-9a-f]{64}$'),
  constraint trading_research_dependence_plans_proposal_hash_check
    check (proposal_hash ~ '^[0-9a-f]{64}$'),
  constraint trading_research_dependence_plans_version_check
    check (plan_version = 'math001-dependence-plan-v1'),
  constraint trading_research_dependence_plans_revision_check
    check (proposal_revision >= 1),
  constraint trading_research_dependence_plans_no_iid_requirement_check
    check (iid_required = false),
  constraint trading_research_dependence_plans_cross_policy_check
    check (jsonb_typeof(cross_candidate_dependence_policy) = 'object'),
  constraint trading_research_dependence_plans_ready_check
    check (dependence_ready = (cardinality(missing_checks) = 0)),
  constraint trading_research_dependence_plans_policy_status_check
    check (policy_status = 'STUDY_ONLY_UNFROZEN'),
  constraint trading_research_dependence_plans_no_production_authority_check
    check (production_authority = false),
  constraint trading_research_dependence_plans_no_stage_authority_check
    check (protected_stage_authority = false),
  constraint trading_research_dependence_plans_proposal_revision_unique
    unique (proposal_id, proposal_revision)
);

create index trading_research_dependence_plans_family_idx
  on private.trading_research_dependence_plans(
    family_id, created_at, plan_id
  );

create index trading_research_dependence_plans_ready_idx
  on private.trading_research_dependence_plans(
    dependence_ready, created_at, plan_id
  );

create index trading_research_dependence_plans_agent_run_idx
  on private.trading_research_dependence_plans(source_agent_run_id)
  where source_agent_run_id is not null;

alter table private.trading_research_dependence_plans
  enable row level security;

revoke all on private.trading_research_dependence_plans
  from anon, authenticated;

create trigger trading_research_dependence_plans_append_only
before update or delete on private.trading_research_dependence_plans
for each row
execute function private.rhen_research_append_only_guard();

create view private.rhen_research_dependence_state_v1
with (security_invoker = true)
as
select
  'math001-dependence-v1'::text as dependence_version,
  count(*) as plan_count,
  count(*) filter (where dependence_ready) as ready_plan_count,
  count(*) filter (where not dependence_ready) as blocked_plan_count,
  count(*) filter (
    where 'cross_candidate_dependence_stress' = any(required_checks)
  ) as cross_candidate_plan_count,
  count(*) filter (
    where cardinality(missing_checks) > 0
  ) as plans_with_missing_checks,
  max(created_at) as latest_plan_at,
  coalesce(bool_or(production_authority), false)
    as any_production_authority,
  coalesce(bool_or(protected_stage_authority), false)
    as any_protected_stage_authority
from private.trading_research_dependence_plans;

revoke all on private.rhen_research_dependence_state_v1
  from anon, authenticated;

create function private.rhen_research_record_dependence_plan(
  p_ledger jsonb
)
returns jsonb
language plpgsql
security invoker
set search_path = private, pg_temp
as $function$
declare
  v_plan jsonb;
  v_family jsonb;
  v_created_at timestamptz;
  v_existing_hash text;
  v_existing_proposal_hash text;
begin
  if jsonb_typeof(p_ledger) <> 'object'
     or p_ledger->>'ledger_version' <> 'math001-search-ledger-v1' then
    raise exception 'invalid MATH-001 search ledger payload';
  end if;

  v_plan := p_ledger->'dependence_plan';
  v_family := p_ledger->'family';

  if jsonb_typeof(v_plan) <> 'object'
     or v_plan->>'plan_version' <> 'math001-dependence-plan-v1'
     or coalesce(v_plan->>'plan_id','') = ''
     or coalesce(v_plan->>'plan_hash','') !~ '^[0-9a-f]{64}$'
     or coalesce(v_plan->>'proposal_hash','') !~ '^[0-9a-f]{64}$'
     or coalesce(v_plan->>'outcome_contract','') = ''
     or coalesce(v_plan->>'null_contract','') = ''
     or jsonb_typeof(v_plan->'cross_candidate_dependence_policy') <> 'object'
     or jsonb_typeof(v_plan->'required_checks') <> 'array'
     or jsonb_typeof(v_plan->'present_checks') <> 'array'
     or jsonb_typeof(v_plan->'missing_checks') <> 'array' then
    raise exception 'invalid dependence plan';
  end if;

  if v_plan->>'proposal_id' is distinct from p_ledger->>'proposal_id'
     or (v_plan->>'proposal_revision')::integer is distinct from
        (p_ledger->>'proposal_revision')::integer
     or v_plan->>'proposal_hash' is distinct from p_ledger->>'proposal_hash' then
    raise exception 'dependence plan is not bound to the search ledger proposal';
  end if;

  if coalesce((v_plan->>'iid_required')::boolean, true) then
    raise exception 'MATH-001-D may not impose an IID requirement';
  end if;

  if coalesce((v_plan->>'production_authority')::boolean, true)
     or coalesce((v_plan->>'protected_stage_authority')::boolean, true) then
    raise exception 'dependence plan cannot carry production or stage authority';
  end if;

  select min((item->>'created_at')::timestamptz)
    into v_created_at
  from jsonb_array_elements(p_ledger->'hypotheses') item;

  insert into private.trading_research_dependence_plans (
    plan_id,
    plan_hash,
    plan_version,
    proposal_id,
    proposal_revision,
    proposal_hash,
    family_id,
    source_agent_run_id,
    source_commit,
    outcome_contract,
    null_contract,
    iid_required,
    level_autocorrelation_policy,
    heteroskedasticity_policy,
    cross_candidate_dependence_policy,
    heavy_tail_policy,
    overlap_policy,
    required_checks,
    present_checks,
    missing_checks,
    dependence_ready,
    policy_status,
    production_authority,
    protected_stage_authority,
    created_at
  ) values (
    (v_plan->>'plan_id')::uuid,
    v_plan->>'plan_hash',
    v_plan->>'plan_version',
    v_plan->>'proposal_id',
    (v_plan->>'proposal_revision')::integer,
    v_plan->>'proposal_hash',
    (v_family->>'family_id')::uuid,
    nullif(p_ledger->>'source_agent_run_id','')::uuid,
    nullif(p_ledger->>'source_commit',''),
    v_plan->>'outcome_contract',
    v_plan->>'null_contract',
    (v_plan->>'iid_required')::boolean,
    v_plan->>'level_autocorrelation_under_null',
    v_plan->>'heteroskedasticity_policy',
    v_plan->'cross_candidate_dependence_policy',
    v_plan->>'heavy_tail_policy',
    v_plan->>'overlap_policy',
    array(
      select jsonb_array_elements_text(v_plan->'required_checks')
    ),
    array(
      select jsonb_array_elements_text(v_plan->'present_checks')
    ),
    array(
      select jsonb_array_elements_text(v_plan->'missing_checks')
    ),
    (v_plan->>'dependence_ready')::boolean,
    v_plan->>'policy_status',
    (v_plan->>'production_authority')::boolean,
    (v_plan->>'protected_stage_authority')::boolean,
    v_created_at
  )
  on conflict (plan_id) do nothing;

  select plan_hash, proposal_hash
    into v_existing_hash, v_existing_proposal_hash
  from private.trading_research_dependence_plans
  where plan_id = (v_plan->>'plan_id')::uuid;

  if v_existing_hash is distinct from v_plan->>'plan_hash'
     or v_existing_proposal_hash is distinct from v_plan->>'proposal_hash' then
    raise exception 'dependence plan identity collision';
  end if;

  return jsonb_build_object(
    'dependence_version', 'math001-dependence-v1',
    'plan_id', v_plan->>'plan_id',
    'plan_hash', v_plan->>'plan_hash',
    'dependence_ready', (v_plan->>'dependence_ready')::boolean,
    'missing_check_count', jsonb_array_length(v_plan->'missing_checks'),
    'policy_status', v_plan->>'policy_status',
    'production_authority', false,
    'protected_stage_authority', false
  );
end;
$function$;

revoke execute on function
  private.rhen_research_record_dependence_plan(jsonb)
  from public, anon, authenticated;
