-- MATH-001-C // durable multiplicity-plan registry.
-- Research-only metadata. No strategy, risk, sizing, execution, broker,
-- protected-stage, quarantine, methodology-freeze, or promotion authority.

create table private.trading_research_multiplicity_plans (
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
  method text not null,
  alpha numeric,
  family_scope text not null,
  family_size integer not null,
  input_type text not null,
  error_metric text not null,
  dependence_scope text not null,
  method_configuration jsonb not null default '{}'::jsonb,
  search_generation integer not null default 0,
  policy_status text not null,
  production_authority boolean not null default false,
  protected_stage_authority boolean not null default false,
  created_at timestamptz not null,
  recorded_at timestamptz not null default now(),
  constraint trading_research_multiplicity_plans_hash_check
    check (plan_hash ~ '^[0-9a-f]{64}$'),
  constraint trading_research_multiplicity_plans_proposal_hash_check
    check (proposal_hash ~ '^[0-9a-f]{64}$'),
  constraint trading_research_multiplicity_plans_version_check
    check (plan_version = 'math001-multiplicity-plan-v1'),
  constraint trading_research_multiplicity_plans_revision_check
    check (proposal_revision >= 1),
  constraint trading_research_multiplicity_plans_method_check
    check (method in ('none','bonferroni','holm','bh','by','e_bh')),
  constraint trading_research_multiplicity_plans_alpha_check
    check (
      (method = 'none' and alpha is null)
      or
      (method <> 'none' and alpha > 0 and alpha < 1)
    ),
  constraint trading_research_multiplicity_plans_family_size_check
    check (family_size >= 0),
  constraint trading_research_multiplicity_plans_input_type_check
    check (input_type in ('NONE','P_VALUE','E_VALUE')),
  constraint trading_research_multiplicity_plans_error_metric_check
    check (error_metric in ('NONE','FWER','FDR')),
  constraint trading_research_multiplicity_plans_dependence_check
    check (dependence_scope in (
      'NOT_APPLICABLE',
      'ARBITRARY',
      'INDEPENDENCE_OR_PRDS'
    )),
  constraint trading_research_multiplicity_plans_config_check
    check (jsonb_typeof(method_configuration) = 'object'),
  constraint trading_research_multiplicity_plans_generation_check
    check (search_generation >= 0),
  constraint trading_research_multiplicity_plans_policy_status_check
    check (policy_status = 'STUDY_ONLY_UNFROZEN'),
  constraint trading_research_multiplicity_plans_no_production_authority_check
    check (production_authority = false),
  constraint trading_research_multiplicity_plans_no_stage_authority_check
    check (protected_stage_authority = false),
  constraint trading_research_multiplicity_plans_proposal_revision_unique
    unique (proposal_id, proposal_revision)
);

create index trading_research_multiplicity_plans_family_idx
  on private.trading_research_multiplicity_plans(
    family_id, created_at, plan_id
  );

create index trading_research_multiplicity_plans_method_idx
  on private.trading_research_multiplicity_plans(
    method, created_at, plan_id
  );

create index trading_research_multiplicity_plans_agent_run_idx
  on private.trading_research_multiplicity_plans(source_agent_run_id)
  where source_agent_run_id is not null;

alter table private.trading_research_multiplicity_plans
  enable row level security;

revoke all on private.trading_research_multiplicity_plans
  from anon, authenticated;

create trigger trading_research_multiplicity_plans_append_only
before update or delete on private.trading_research_multiplicity_plans
for each row
execute function private.rhen_research_append_only_guard();

create view private.rhen_research_multiplicity_state_v1
with (security_invoker = true)
as
select
  'math001-multiplicity-v1'::text as multiplicity_version,
  count(*) as plan_count,
  count(*) filter (where method <> 'none') as corrected_family_count,
  count(*) filter (where input_type = 'P_VALUE') as p_value_plan_count,
  count(*) filter (where input_type = 'E_VALUE') as e_value_plan_count,
  count(*) filter (where error_metric = 'FWER') as fwer_plan_count,
  count(*) filter (where error_metric = 'FDR') as fdr_plan_count,
  max(created_at) as latest_plan_at,
  bool_or(production_authority) as any_production_authority,
  bool_or(protected_stage_authority) as any_protected_stage_authority
from private.trading_research_multiplicity_plans;

revoke all on private.rhen_research_multiplicity_state_v1
  from anon, authenticated;

create function private.rhen_research_record_multiplicity_plan(
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

  v_plan := p_ledger->'multiplicity_plan';
  v_family := p_ledger->'family';

  if jsonb_typeof(v_plan) <> 'object'
     or v_plan->>'plan_version' <> 'math001-multiplicity-plan-v1'
     or coalesce(v_plan->>'plan_id','') = ''
     or coalesce(v_plan->>'plan_hash','') !~ '^[0-9a-f]{64}$'
     or coalesce(v_plan->>'proposal_hash','') !~ '^[0-9a-f]{64}$'
     or coalesce(v_plan->>'method','') = ''
     or coalesce(v_plan->>'family_scope','') = ''
     or coalesce(v_plan->>'input_type','') = ''
     or coalesce(v_plan->>'error_metric','') = ''
     or coalesce(v_plan->>'dependence_scope','') = '' then
    raise exception 'invalid multiplicity plan';
  end if;

  if v_plan->>'proposal_id' is distinct from p_ledger->>'proposal_id'
     or (v_plan->>'proposal_revision')::integer is distinct from
        (p_ledger->>'proposal_revision')::integer
     or v_plan->>'proposal_hash' is distinct from p_ledger->>'proposal_hash' then
    raise exception 'multiplicity plan is not bound to the search ledger proposal';
  end if;

  if coalesce((v_plan->>'production_authority')::boolean, true)
     or coalesce((v_plan->>'protected_stage_authority')::boolean, true) then
    raise exception 'multiplicity plan cannot carry production or stage authority';
  end if;

  select min((item->>'created_at')::timestamptz)
    into v_created_at
  from jsonb_array_elements(p_ledger->'hypotheses') item;

  insert into private.trading_research_multiplicity_plans (
    plan_id,
    plan_hash,
    plan_version,
    proposal_id,
    proposal_revision,
    proposal_hash,
    family_id,
    source_agent_run_id,
    source_commit,
    method,
    alpha,
    family_scope,
    family_size,
    input_type,
    error_metric,
    dependence_scope,
    method_configuration,
    search_generation,
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
    v_plan->>'method',
    nullif(v_plan->>'alpha','')::numeric,
    v_plan->>'family_scope',
    (v_plan->>'family_size')::integer,
    v_plan->>'input_type',
    v_plan->>'error_metric',
    v_plan->>'dependence_scope',
    coalesce(v_plan->'method_configuration', '{}'::jsonb),
    coalesce((v_plan->>'search_generation')::integer, 0),
    v_plan->>'policy_status',
    (v_plan->>'production_authority')::boolean,
    (v_plan->>'protected_stage_authority')::boolean,
    v_created_at
  )
  on conflict (plan_id) do nothing;

  select plan_hash, proposal_hash
    into v_existing_hash, v_existing_proposal_hash
  from private.trading_research_multiplicity_plans
  where plan_id = (v_plan->>'plan_id')::uuid;

  if v_existing_hash is distinct from v_plan->>'plan_hash'
     or v_existing_proposal_hash is distinct from v_plan->>'proposal_hash' then
    raise exception 'multiplicity plan identity collision';
  end if;

  return jsonb_build_object(
    'multiplicity_version', 'math001-multiplicity-v1',
    'plan_id', v_plan->>'plan_id',
    'plan_hash', v_plan->>'plan_hash',
    'method', v_plan->>'method',
    'family_size', (v_plan->>'family_size')::integer,
    'policy_status', v_plan->>'policy_status',
    'production_authority', false,
    'protected_stage_authority', false
  );
end;
$function$;

revoke execute on function
  private.rhen_research_record_multiplicity_plan(jsonb)
  from public, anon, authenticated;
