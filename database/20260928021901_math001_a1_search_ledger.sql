
-- MATH-001-A1 // immutable hypothesis and search-exposure ledger.
-- Additive research accounting only. No strategy, risk, sizing, broker,
-- protected-stage, quarantine, methodology-freeze, or promotion authority.

create table private.trading_research_hypothesis_families (
  family_id uuid primary key,
  family_key text not null unique,
  family_hash text not null unique,
  normalized_name text not null unique,
  display_name text not null,
  first_seen_at timestamptz not null,
  created_at timestamptz not null default now(),
  constraint trading_research_hypothesis_families_hash_check
    check (family_hash ~ '^[0-9a-f]{64}$'),
  constraint trading_research_hypothesis_families_name_check
    check (btrim(normalized_name) <> '' and btrim(display_name) <> '')
);

create index trading_research_hypothesis_families_first_seen_idx
  on private.trading_research_hypothesis_families(first_seen_at, family_id);

alter table private.trading_research_hypothesis_families enable row level security;
revoke all on private.trading_research_hypothesis_families from anon, authenticated;

create table private.trading_research_hypotheses (
  hypothesis_id uuid primary key,
  hypothesis_key text not null unique,
  definition_hash text not null unique,
  family_id uuid not null
    references private.trading_research_hypothesis_families(family_id),
  parent_hypothesis_id uuid
    references private.trading_research_hypotheses(hypothesis_id),
  hypothesis_kind text not null,
  origin_kind text not null,
  search_generation integer not null default 0,
  research_question_id text,
  proposal_id text,
  proposal_revision integer,
  proposal_hash text,
  statement text not null,
  null_or_falsification_statement text not null,
  economic_mechanism text not null,
  primary_endpoint text not null,
  definition jsonb not null,
  source_agent_run_id uuid
    references private.trading_research_agent_runs(run_id),
  source_commit text,
  created_at timestamptz not null,
  constraint trading_research_hypotheses_hash_check
    check (definition_hash ~ '^[0-9a-f]{64}$'),
  constraint trading_research_hypotheses_proposal_hash_check
    check (proposal_hash is null or proposal_hash ~ '^[0-9a-f]{64}$'),
  constraint trading_research_hypotheses_kind_check
    check (hypothesis_kind in (
      'SCIENTIFIC_HYPOTHESIS',
      'CANDIDATE_VARIANT',
      'LEGACY_CANDIDATE'
    )),
  constraint trading_research_hypotheses_origin_check
    check (origin_kind in ('AGENT','MANUAL','BACKFILL','SYSTEM')),
  constraint trading_research_hypotheses_generation_check
    check (search_generation >= 0),
  constraint trading_research_hypotheses_revision_check
    check (proposal_revision is null or proposal_revision >= 1),
  constraint trading_research_hypotheses_definition_check
    check (jsonb_typeof(definition) = 'object'),
  constraint trading_research_hypotheses_parent_check
    check (parent_hypothesis_id is null or parent_hypothesis_id <> hypothesis_id)
);

create index trading_research_hypotheses_family_created_idx
  on private.trading_research_hypotheses(family_id, created_at, hypothesis_id);
create index trading_research_hypotheses_parent_idx
  on private.trading_research_hypotheses(parent_hypothesis_id)
  where parent_hypothesis_id is not null;
create index trading_research_hypotheses_proposal_idx
  on private.trading_research_hypotheses(proposal_id, proposal_revision)
  where proposal_id is not null;
create index trading_research_hypotheses_agent_run_idx
  on private.trading_research_hypotheses(source_agent_run_id)
  where source_agent_run_id is not null;

alter table private.trading_research_hypotheses enable row level security;
revoke all on private.trading_research_hypotheses from anon, authenticated;

create table private.trading_research_search_events (
  search_event_sequence bigint generated always as identity unique,
  search_event_id uuid primary key,
  event_key text not null unique,
  family_id uuid not null
    references private.trading_research_hypothesis_families(family_id),
  hypothesis_id uuid not null
    references private.trading_research_hypotheses(hypothesis_id),
  event_type text not null,
  research_stage text not null,
  source_kind text not null,
  event_at timestamptz not null,
  evidence_cutoff timestamptz,
  proposal_id text,
  proposal_revision integer,
  proposal_hash text,
  source_agent_run_id uuid
    references private.trading_research_agent_runs(run_id),
  experiment_id uuid
    references private.trading_experiments(experiment_id),
  corpus_id text,
  data_scope_hash text,
  global_filtration_id text,
  data_contaminating boolean not null default false,
  prior_hypotheses_examined integer not null default 0,
  payload jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  constraint trading_research_search_events_type_check
    check (event_type in (
      'PROPOSED',
      'SCREENED',
      'DATA_ACCESSED',
      'RESULT_INSPECTED',
      'REJECTED',
      'ABANDONED',
      'FROZEN',
      'DEVELOPMENT_OPENED',
      'DEVELOPMENT_COMPLETED',
      'VALIDATION_OPENED',
      'VALIDATION_COMPLETED',
      'HOLDOUT_OPENED',
      'HOLDOUT_COMPLETED',
      'CONFIRMED',
      'REPLICATED',
      'ARCHIVED'
    )),
  constraint trading_research_search_events_stage_check
    check (research_stage in ('NONE','PROPOSAL','DEVELOPMENT','VALIDATION','HOLDOUT')),
  constraint trading_research_search_events_source_check
    check (source_kind in ('AGENT','MANUAL','BACKFILL','SYSTEM')),
  constraint trading_research_search_events_revision_check
    check (proposal_revision is null or proposal_revision >= 1),
  constraint trading_research_search_events_proposal_hash_check
    check (proposal_hash is null or proposal_hash ~ '^[0-9a-f]{64}$'),
  constraint trading_research_search_events_scope_hash_check
    check (data_scope_hash is null or data_scope_hash ~ '^[0-9a-f]{64}$'),
  constraint trading_research_search_events_prior_count_check
    check (prior_hypotheses_examined >= 0),
  constraint trading_research_search_events_payload_check
    check (jsonb_typeof(payload) = 'object')
);

create index trading_research_search_events_hypothesis_idx
  on private.trading_research_search_events(hypothesis_id, event_at, search_event_sequence);
create index trading_research_search_events_family_idx
  on private.trading_research_search_events(family_id, event_at, search_event_sequence);
create index trading_research_search_events_type_idx
  on private.trading_research_search_events(event_type, event_at, search_event_sequence);
create index trading_research_search_events_experiment_idx
  on private.trading_research_search_events(experiment_id, event_at)
  where experiment_id is not null;
create index trading_research_search_events_agent_run_idx
  on private.trading_research_search_events(source_agent_run_id)
  where source_agent_run_id is not null;
create index trading_research_search_events_contaminating_idx
  on private.trading_research_search_events(event_at, hypothesis_id)
  where data_contaminating;

alter table private.trading_research_search_events enable row level security;
revoke all on private.trading_research_search_events from anon, authenticated;

with edge_families as (
  select
    regexp_replace(lower(replace(replace(r.family, '_', ' '), '-', ' ')), '\s+', ' ', 'g') as normalized_name,
    replace(r.family, '_', ' ') as display_name,
    min(e.created_at) as first_seen_at
  from private.trading_experiment_results r
  join private.trading_experiments e on e.experiment_id = r.experiment_id
  where e.experiment_key = 'edge-corpus-v1'
    and r.family <> 'corpus_integrity'
  group by r.family
),
rdr_family as (
  select
    regexp_replace(lower(replace(replace(e.research_family, '_', ' '), '-', ' ')), '\s+', ' ', 'g') as normalized_name,
    e.research_family as display_name,
    e.created_at as first_seen_at
  from private.trading_experiments e
  where e.experiment_key = 'edge-discovery-v2-residual-downshock-rebound-v2.1'
),
families as (
  select * from edge_families
  union all
  select * from rdr_family
),
identified as (
  select
    normalized_name,
    display_name,
    first_seen_at,
    encode(digest(normalized_name, 'sha256'), 'hex') as family_hash
  from families
)
insert into private.trading_research_hypothesis_families (
  family_id, family_key, family_hash, normalized_name, display_name, first_seen_at
)
select
  uuid_generate_v5('6f90b1f4-f34a-5d87-9dc3-7b9ca3e2fb3d'::uuid, family_hash),
  'rhen-family:' || family_hash,
  family_hash,
  normalized_name,
  display_name,
  first_seen_at
from identified
on conflict do nothing;

with edge_rows as (
  select distinct on (r.family)
    e.experiment_id,
    e.experiment_key,
    e.code_commit,
    e.created_at,
    regexp_replace(lower(replace(replace(r.family, '_', ' '), '-', ' ')), '\s+', ' ', 'g') as normalized_name,
    replace(r.family, '_', ' ') as display_name,
    r.result_key,
    r.terminal_reason_code
  from private.trading_experiment_results r
  join private.trading_experiments e on e.experiment_id = r.experiment_id
  where e.experiment_key = 'edge-corpus-v1'
    and r.family <> 'corpus_integrity'
  order by r.family, r.created_at, r.result_key
),
identified as (
  select
    *,
    encode(digest(normalized_name, 'sha256'), 'hex') as family_hash,
    encode(digest('legacy-edge-corpus-v1:' || normalized_name, 'sha256'), 'hex') as definition_hash
  from edge_rows
)
insert into private.trading_research_hypotheses (
  hypothesis_id, hypothesis_key, definition_hash, family_id,
  hypothesis_kind, origin_kind, search_generation, statement,
  null_or_falsification_statement, economic_mechanism, primary_endpoint,
  definition, source_commit, created_at
)
select
  uuid_generate_v5('ace4ce41-d4cc-56e0-9f8c-192028f6d41f'::uuid, definition_hash),
  'rhen-hypothesis:' || definition_hash,
  definition_hash,
  uuid_generate_v5('6f90b1f4-f34a-5d87-9dc3-7b9ca3e2fb3d'::uuid, family_hash),
  'LEGACY_CANDIDATE',
  'BACKFILL',
  0,
  'Historical Edge Discovery v1 candidate family: ' || display_name,
  'Reject the candidate if it violates the frozen development stress-cost gate.',
  'Historical directional edge candidate encoded by edge-corpus-v1.',
  'Frozen stress-cost expectancy and worst-period development gate.',
  jsonb_build_object(
    'definition_version', 'math001-legacy-candidate-v1',
    'source_experiment_id', experiment_id,
    'source_experiment_key', experiment_key,
    'source_result_key', result_key,
    'family', normalized_name,
    'terminal_reason_code', terminal_reason_code
  ),
  code_commit,
  created_at
from identified
on conflict do nothing;

with rdr as (
  select
    e.*,
    regexp_replace(lower(replace(replace(e.research_family, '_', ' '), '-', ' ')), '\s+', ' ', 'g') as normalized_name
  from private.trading_experiments e
  where e.experiment_key = 'edge-discovery-v2-residual-downshock-rebound-v2.1'
),
identified as (
  select
    *,
    encode(digest(normalized_name, 'sha256'), 'hex') as family_hash,
    encode(digest('legacy-rdr-v2.1:' || normalized_name, 'sha256'), 'hex') as definition_hash
  from rdr
)
insert into private.trading_research_hypotheses (
  hypothesis_id, hypothesis_key, definition_hash, family_id,
  hypothesis_kind, origin_kind, search_generation, statement,
  null_or_falsification_statement, economic_mechanism, primary_endpoint,
  definition, source_commit, created_at
)
select
  uuid_generate_v5('ace4ce41-d4cc-56e0-9f8c-192028f6d41f'::uuid, definition_hash),
  'rhen-hypothesis:' || definition_hash,
  definition_hash,
  uuid_generate_v5('6f90b1f4-f34a-5d87-9dc3-7b9ca3e2fb3d'::uuid, family_hash),
  'LEGACY_CANDIDATE',
  'BACKFILL',
  0,
  hypothesis,
  'The frozen v2.1 corpus and performance gates fail to establish the candidate edge.',
  'Residual-downshock mean reversion after a negative idiosyncratic shock.',
  'Frozen 30-minute rebound endpoint after residual-shock detection.',
  jsonb_build_object(
    'definition_version', 'math001-legacy-candidate-v1',
    'source_experiment_id', experiment_id,
    'source_experiment_key', experiment_key,
    'methodology_version', methodology_version,
    'manifest_hash', manifest_hash,
    'terminal_classification', 'corpus_quality_failure_not_performance_rejection'
  ),
  code_commit,
  created_at
from identified
on conflict do nothing;

with edge_hypotheses as (
  select
    h.hypothesis_id,
    h.family_id,
    f.normalized_name,
    e.experiment_id,
    e.created_at,
    row_number() over (order by f.normalized_name, h.hypothesis_id) - 1 as prior_count
  from private.trading_research_hypotheses h
  join private.trading_research_hypothesis_families f on f.family_id = h.family_id
  join private.trading_experiments e on e.experiment_key = 'edge-corpus-v1'
  where h.origin_kind = 'BACKFILL'
    and (h.definition->>'source_experiment_key') = 'edge-corpus-v1'
),
events as (
  select
    'legacy:edge-corpus-v1:' || normalized_name || ':PROPOSED' as event_key,
    family_id,
    hypothesis_id,
    'PROPOSED' as event_type,
    'PROPOSAL' as research_stage,
    created_at as event_at,
    prior_count::integer as prior_count,
    experiment_id
  from edge_hypotheses
)
insert into private.trading_research_search_events (
  search_event_id, event_key, family_id, hypothesis_id, event_type,
  research_stage, source_kind, event_at, experiment_id, corpus_id,
  data_contaminating, prior_hypotheses_examined, payload
)
select
  uuid_generate_v5('4a21b6b1-9ebf-5ce4-91ef-05577739a59e'::uuid, event_key),
  event_key, family_id, hypothesis_id, event_type, research_stage, 'BACKFILL',
  event_at, experiment_id, 'edge-corpus-v1', false, prior_count,
  jsonb_build_object(
    'ledger_version', 'math001-search-ledger-v1',
    'historical_backfill', true,
    'stage_opened', false
  )
from events
order by prior_count
on conflict do nothing;

with edge_results as (
  select distinct on (r.family)
    h.hypothesis_id,
    h.family_id,
    f.normalized_name,
    r.experiment_id,
    r.result_key,
    r.created_at,
    r.terminal_decision,
    r.terminal_reason_code,
    encode(digest('edge-corpus-v1:development', 'sha256'), 'hex') as data_scope_hash
  from private.trading_experiment_results r
  join private.trading_experiments e on e.experiment_id = r.experiment_id
  join private.trading_research_hypotheses h
    on (h.definition->>'source_experiment_key') = e.experiment_key
   and (h.definition->>'family') =
       regexp_replace(lower(replace(replace(r.family, '_', ' '), '-', ' ')), '\s+', ' ', 'g')
  join private.trading_research_hypothesis_families f on f.family_id = h.family_id
  where e.experiment_key = 'edge-corpus-v1'
    and r.family <> 'corpus_integrity'
  order by r.family, r.created_at, r.result_key
),
event_rows as (
  select
    'legacy:edge-corpus-v1:' || normalized_name || ':RESULT_INSPECTED' as event_key,
    family_id, hypothesis_id, experiment_id, created_at as event_at,
    'RESULT_INSPECTED' as event_type, true as data_contaminating,
    data_scope_hash,
    jsonb_build_object('result_key', result_key, 'historical_backfill', true) as payload
  from edge_results
  union all
  select
    'legacy:edge-corpus-v1:' || normalized_name || ':REJECTED',
    family_id, hypothesis_id, experiment_id, created_at,
    'REJECTED', false, data_scope_hash,
    jsonb_build_object(
      'result_key', result_key,
      'terminal_decision', terminal_decision,
      'terminal_reason_code', terminal_reason_code,
      'historical_backfill', true
    )
  from edge_results
)
insert into private.trading_research_search_events (
  search_event_id, event_key, family_id, hypothesis_id, event_type,
  research_stage, source_kind, event_at, experiment_id, corpus_id,
  data_scope_hash, global_filtration_id, data_contaminating,
  prior_hypotheses_examined, payload
)
select
  uuid_generate_v5('4a21b6b1-9ebf-5ce4-91ef-05577739a59e'::uuid, event_key),
  event_key, family_id, hypothesis_id, event_type, 'DEVELOPMENT', 'BACKFILL',
  event_at, experiment_id, 'edge-corpus-v1', data_scope_hash,
  'legacy:edge-corpus-v1:development', data_contaminating, 6, payload
from event_rows
order by event_at, event_type, event_key
on conflict do nothing;

with rdr as (
  select h.hypothesis_id, h.family_id, e.experiment_id, e.created_at
  from private.trading_research_hypotheses h
  join private.trading_experiments e
    on (h.definition->>'source_experiment_key') = e.experiment_key
  where (h.definition->>'source_experiment_key') =
    'edge-discovery-v2-residual-downshock-rebound-v2.1'
),
event_row as (
  select
    'legacy:rdr-v2.1:PROPOSED' as event_key,
    family_id, hypothesis_id, experiment_id, created_at as event_at
  from rdr
)
insert into private.trading_research_search_events (
  search_event_id, event_key, family_id, hypothesis_id, event_type,
  research_stage, source_kind, event_at, experiment_id, corpus_id,
  data_contaminating, prior_hypotheses_examined, payload
)
select
  uuid_generate_v5('4a21b6b1-9ebf-5ce4-91ef-05577739a59e'::uuid, event_key),
  event_key, family_id, hypothesis_id, 'PROPOSED', 'PROPOSAL', 'BACKFILL',
  event_at, experiment_id, 'edge-discovery-v2-residual-downshock-rebound-v2.1',
  false, 5,
  jsonb_build_object(
    'ledger_version', 'math001-search-ledger-v1',
    'historical_backfill', true,
    'stage_opened', false
  )
from event_row
on conflict do nothing;

with rdr as (
  select h.hypothesis_id, h.family_id, e.experiment_id, e.created_at
  from private.trading_research_hypotheses h
  join private.trading_experiments e
    on (h.definition->>'source_experiment_key') = e.experiment_key
  where (h.definition->>'source_experiment_key') =
    'edge-discovery-v2-residual-downshock-rebound-v2.1'
),
event_row as (
  select
    'legacy:rdr-v2.1:FROZEN' as event_key,
    family_id, hypothesis_id, experiment_id, created_at as event_at
  from rdr
)
insert into private.trading_research_search_events (
  search_event_id, event_key, family_id, hypothesis_id, event_type,
  research_stage, source_kind, event_at, experiment_id, corpus_id,
  data_contaminating, prior_hypotheses_examined, payload
)
select
  uuid_generate_v5('4a21b6b1-9ebf-5ce4-91ef-05577739a59e'::uuid, event_key),
  event_key, family_id, hypothesis_id, 'FROZEN', 'PROPOSAL', 'BACKFILL',
  event_at, experiment_id, 'edge-discovery-v2-residual-downshock-rebound-v2.1',
  false, 6,
  jsonb_build_object(
    'historical_backfill', true,
    'methodology_frozen', true,
    'stage_opened', false
  )
from event_row
on conflict do nothing;

with rdr_result as (
  select
    h.hypothesis_id,
    h.family_id,
    r.experiment_id,
    r.created_at,
    r.result_key,
    r.terminal_decision,
    r.terminal_reason_code,
    encode(
      digest('edge-discovery-v2-residual-downshock-rebound-v2.1:development', 'sha256'),
      'hex'
    ) as data_scope_hash
  from private.trading_experiment_results r
  join private.trading_experiments e on e.experiment_id = r.experiment_id
  join private.trading_research_hypotheses h
    on (h.definition->>'source_experiment_key') = e.experiment_key
  where e.experiment_key = 'edge-discovery-v2-residual-downshock-rebound-v2.1'
    and r.result_key = 'development:corpus-quality-gate'
),
event_rows as (
  select
    'legacy:rdr-v2.1:DEVELOPMENT_OPENED' as event_key,
    family_id, hypothesis_id, experiment_id, created_at as event_at,
    'DEVELOPMENT_OPENED' as event_type, false as data_contaminating,
    data_scope_hash,
    jsonb_build_object('historical_backfill', true, 'stage_opened', true) as payload
  from rdr_result
  union all
  select
    'legacy:rdr-v2.1:DATA_ACCESSED',
    family_id, hypothesis_id, experiment_id, created_at,
    'DATA_ACCESSED', true, data_scope_hash,
    jsonb_build_object(
      'historical_backfill', true,
      'result_key', result_key,
      'performance_evaluation_run', false
    )
  from rdr_result
  union all
  select
    'legacy:rdr-v2.1:DEVELOPMENT_COMPLETED',
    family_id, hypothesis_id, experiment_id, created_at,
    'DEVELOPMENT_COMPLETED', false, data_scope_hash,
    jsonb_build_object(
      'historical_backfill', true,
      'terminal_reason_code', terminal_reason_code,
      'performance_evaluation_run', false
    )
  from rdr_result
  union all
  select
    'legacy:rdr-v2.1:ARCHIVED',
    family_id, hypothesis_id, experiment_id, created_at,
    'ARCHIVED', false, data_scope_hash,
    jsonb_build_object(
      'historical_backfill', true,
      'terminal_decision', terminal_decision,
      'terminal_reason_code', terminal_reason_code,
      'classification', 'corpus_quality_failure_not_performance_rejection'
    )
  from rdr_result
)
insert into private.trading_research_search_events (
  search_event_id, event_key, family_id, hypothesis_id, event_type,
  research_stage, source_kind, event_at, experiment_id, corpus_id,
  data_scope_hash, global_filtration_id, data_contaminating,
  prior_hypotheses_examined, payload
)
select
  uuid_generate_v5('4a21b6b1-9ebf-5ce4-91ef-05577739a59e'::uuid, event_key),
  event_key, family_id, hypothesis_id, event_type, 'DEVELOPMENT', 'BACKFILL',
  event_at, experiment_id, 'edge-discovery-v2-residual-downshock-rebound-v2.1',
  data_scope_hash, 'legacy:rdr-v2.1:development', data_contaminating, 6, payload
from event_rows
order by
  case event_type
    when 'DEVELOPMENT_OPENED' then 1
    when 'DATA_ACCESSED' then 2
    when 'DEVELOPMENT_COMPLETED' then 3
    else 4
  end
on conflict do nothing;

create view private.rhen_research_search_exposure_v1
with (security_invoker = true)
as
select
  'math001-search-ledger-v1'::text as ledger_version,
  (select count(*) from private.trading_research_hypothesis_families) as family_count,
  (select count(*) from private.trading_research_hypotheses
    where hypothesis_kind = 'SCIENTIFIC_HYPOTHESIS') as scientific_hypothesis_count,
  (select count(*) from private.trading_research_hypotheses
    where hypothesis_kind in ('CANDIDATE_VARIANT','LEGACY_CANDIDATE')) as candidate_variant_count,
  (select count(*) from private.trading_research_hypotheses) as hypothesis_count,
  count(*) filter (where e.event_type = 'PROPOSED') as proposal_event_count,
  count(*) filter (where e.event_type = 'DATA_ACCESSED') as data_access_event_count,
  count(*) filter (where e.event_type = 'RESULT_INSPECTED') as result_inspection_event_count,
  count(distinct e.hypothesis_id) filter (where e.event_type = 'REJECTED')
    as rejected_hypothesis_count,
  count(distinct e.hypothesis_id) filter (where e.data_contaminating)
    as data_contaminated_hypothesis_count,
  count(*) filter (where e.data_contaminating) as contaminating_event_count,
  max(e.event_at) as latest_event_at
from private.trading_research_search_events e;

revoke all on private.rhen_research_search_exposure_v1 from anon, authenticated;

create view private.rhen_research_family_search_exposure_v1
with (security_invoker = true)
as
select
  f.family_id,
  f.family_key,
  f.normalized_name,
  f.display_name,
  f.first_seen_at,
  count(distinct h.hypothesis_id) as hypothesis_count,
  count(distinct h.hypothesis_id)
    filter (where h.hypothesis_kind in ('CANDIDATE_VARIANT','LEGACY_CANDIDATE'))
    as candidate_variant_count,
  count(e.search_event_id) as event_count,
  count(e.search_event_id) filter (where e.event_type = 'PROPOSED') as proposal_event_count,
  count(distinct e.hypothesis_id) filter (where e.event_type = 'REJECTED')
    as rejected_hypothesis_count,
  count(distinct e.hypothesis_id) filter (where e.data_contaminating)
    as data_contaminated_hypothesis_count,
  max(e.event_at) as latest_event_at
from private.trading_research_hypothesis_families f
left join private.trading_research_hypotheses h on h.family_id = f.family_id
left join private.trading_research_search_events e on e.hypothesis_id = h.hypothesis_id
group by f.family_id, f.family_key, f.normalized_name, f.display_name, f.first_seen_at;

revoke all on private.rhen_research_family_search_exposure_v1 from anon, authenticated;

create function private.rhen_research_record_search_ledger(p_ledger jsonb)
returns jsonb
language plpgsql
security invoker
set search_path = private, pg_temp
as $function$
declare
  v_family jsonb;
  v_hypothesis jsonb;
  v_event jsonb;
  v_first_seen timestamptz;
  v_existing_hash text;
  v_existing_family uuid;
  v_existing_event_key text;
  v_prior integer;
  v_hypotheses integer := 0;
  v_events integer := 0;
begin
  if jsonb_typeof(p_ledger) <> 'object'
     or p_ledger->>'ledger_version' <> 'math001-search-ledger-v1' then
    raise exception 'invalid MATH-001 search ledger payload';
  end if;

  v_family := p_ledger->'family';
  if jsonb_typeof(v_family) <> 'object'
     or coalesce(v_family->>'family_id','') = ''
     or coalesce(v_family->>'family_key','') = ''
     or coalesce(v_family->>'family_hash','') !~ '^[0-9a-f]{64}$'
     or coalesce(v_family->>'normalized_name','') = ''
     or coalesce(v_family->>'display_name','') = '' then
    raise exception 'invalid search ledger family';
  end if;

  if jsonb_typeof(p_ledger->'hypotheses') <> 'array'
     or jsonb_array_length(p_ledger->'hypotheses') = 0 then
    raise exception 'search ledger hypotheses are required';
  end if;
  if jsonb_typeof(p_ledger->'events') <> 'array'
     or jsonb_array_length(p_ledger->'events') = 0 then
    raise exception 'search ledger events are required';
  end if;

  select min((item->>'created_at')::timestamptz)
    into v_first_seen
  from jsonb_array_elements(p_ledger->'hypotheses') item;

  insert into private.trading_research_hypothesis_families (
    family_id, family_key, family_hash, normalized_name, display_name, first_seen_at
  ) values (
    (v_family->>'family_id')::uuid,
    v_family->>'family_key',
    v_family->>'family_hash',
    v_family->>'normalized_name',
    v_family->>'display_name',
    v_first_seen
  )
  on conflict (family_id) do nothing;

  select family_hash
    into v_existing_hash
  from private.trading_research_hypothesis_families
  where family_id = (v_family->>'family_id')::uuid;

  if v_existing_hash is distinct from v_family->>'family_hash' then
    raise exception 'family identity collision';
  end if;

  for v_hypothesis in
    select value from jsonb_array_elements(p_ledger->'hypotheses')
  loop
    if coalesce(v_hypothesis->>'definition_hash','') !~ '^[0-9a-f]{64}$'
       or jsonb_typeof(v_hypothesis->'definition') <> 'object'
       or (v_hypothesis->>'family_id')::uuid <> (v_family->>'family_id')::uuid then
      raise exception 'invalid search ledger hypothesis';
    end if;

    insert into private.trading_research_hypotheses (
      hypothesis_id, hypothesis_key, definition_hash, family_id,
      parent_hypothesis_id, hypothesis_kind, origin_kind, search_generation,
      research_question_id, proposal_id, proposal_revision, proposal_hash,
      statement, null_or_falsification_statement, economic_mechanism,
      primary_endpoint, definition, source_agent_run_id, source_commit, created_at
    ) values (
      (v_hypothesis->>'hypothesis_id')::uuid,
      v_hypothesis->>'hypothesis_key',
      v_hypothesis->>'definition_hash',
      (v_hypothesis->>'family_id')::uuid,
      nullif(v_hypothesis->>'parent_hypothesis_id','')::uuid,
      v_hypothesis->>'hypothesis_kind',
      v_hypothesis->>'origin_kind',
      coalesce((v_hypothesis->>'search_generation')::integer, 0),
      nullif(v_hypothesis->>'research_question_id',''),
      nullif(v_hypothesis->>'proposal_id',''),
      nullif(v_hypothesis->>'proposal_revision','')::integer,
      nullif(v_hypothesis->>'proposal_hash',''),
      v_hypothesis->>'statement',
      v_hypothesis->>'null_or_falsification_statement',
      v_hypothesis->>'economic_mechanism',
      v_hypothesis->>'primary_endpoint',
      v_hypothesis->'definition',
      nullif(v_hypothesis->>'source_agent_run_id','')::uuid,
      nullif(v_hypothesis->>'source_commit',''),
      (v_hypothesis->>'created_at')::timestamptz
    )
    on conflict (hypothesis_id) do nothing;

    select definition_hash, family_id
      into v_existing_hash, v_existing_family
    from private.trading_research_hypotheses
    where hypothesis_id = (v_hypothesis->>'hypothesis_id')::uuid;

    if v_existing_hash is distinct from v_hypothesis->>'definition_hash'
       or v_existing_family is distinct from (v_hypothesis->>'family_id')::uuid then
      raise exception 'hypothesis identity collision';
    end if;

    v_hypotheses := v_hypotheses + 1;
  end loop;

  for v_event in
    select value from jsonb_array_elements(p_ledger->'events')
  loop
    select count(distinct hypothesis_id)::integer
      into v_prior
    from private.trading_research_search_events
    where event_type = 'PROPOSED';

    insert into private.trading_research_search_events (
      search_event_id, event_key, family_id, hypothesis_id, event_type,
      research_stage, source_kind, event_at, evidence_cutoff, proposal_id,
      proposal_revision, proposal_hash, source_agent_run_id, experiment_id,
      corpus_id, data_scope_hash, global_filtration_id, data_contaminating,
      prior_hypotheses_examined, payload
    ) values (
      (v_event->>'search_event_id')::uuid,
      v_event->>'event_key',
      (v_event->>'family_id')::uuid,
      (v_event->>'hypothesis_id')::uuid,
      v_event->>'event_type',
      v_event->>'research_stage',
      v_event->>'source_kind',
      (v_event->>'event_at')::timestamptz,
      nullif(v_event->>'evidence_cutoff','')::timestamptz,
      nullif(v_event->>'proposal_id',''),
      nullif(v_event->>'proposal_revision','')::integer,
      nullif(v_event->>'proposal_hash',''),
      nullif(v_event->>'source_agent_run_id','')::uuid,
      nullif(v_event->>'experiment_id','')::uuid,
      nullif(v_event->>'corpus_id',''),
      nullif(v_event->>'data_scope_hash',''),
      nullif(v_event->>'global_filtration_id',''),
      coalesce((v_event->>'data_contaminating')::boolean, false),
      v_prior,
      coalesce(v_event->'payload', '{}'::jsonb)
    )
    on conflict (search_event_id) do nothing;

    select event_key
      into v_existing_event_key
    from private.trading_research_search_events
    where search_event_id = (v_event->>'search_event_id')::uuid;

    if v_existing_event_key is distinct from v_event->>'event_key' then
      raise exception 'search event identity collision';
    end if;

    v_events := v_events + 1;
  end loop;

  return jsonb_build_object(
    'ledger_version', 'math001-search-ledger-v1',
    'family_id', v_family->>'family_id',
    'hypotheses_processed', v_hypotheses,
    'events_processed', v_events,
    'production_authority', false,
    'protected_stage_authority', false
  );
end;
$function$;

revoke execute on function private.rhen_research_record_search_ledger(jsonb)
  from public, anon, authenticated;

create function private.rhen_research_append_only_guard()
returns trigger
language plpgsql
security invoker
set search_path = private, pg_temp
as $function$
begin
  raise exception '% is append-only; record a new research event instead', tg_table_name;
end;
$function$;

revoke execute on function private.rhen_research_append_only_guard()
  from public, anon, authenticated;

create trigger trading_research_hypothesis_families_append_only
before update or delete on private.trading_research_hypothesis_families
for each row execute function private.rhen_research_append_only_guard();

create trigger trading_research_hypotheses_append_only
before update or delete on private.trading_research_hypotheses
for each row execute function private.rhen_research_append_only_guard();

create trigger trading_research_search_events_append_only
before update or delete on private.trading_research_search_events
for each row execute function private.rhen_research_append_only_guard();
