-- Finalize MATH-001-C durable theory state and normalize empty multiplicity summaries.
-- Canonical registry hash: f1bdf30a487366a176a69a4db171e51ad361d29e76a325cd93ec3fdd0003d4f3
-- Source commit: a10cfc13570267f141acad8676cacf775bcb9654

create or replace view private.rhen_research_multiplicity_state_v1
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
  coalesce(bool_or(production_authority), false) as any_production_authority,
  coalesce(bool_or(protected_stage_authority), false)
    as any_protected_stage_authority
from private.trading_research_multiplicity_plans;

revoke all on private.rhen_research_multiplicity_state_v1
  from anon, authenticated;

update private.anevum_theory_artifacts
set status = 'ARCHIVED'
where artifact_key = 'MATH-001:foundation:v2'
  and status = 'ACTIVE';

insert into private.anevum_theory_artifacts (
  artifact_key,
  artifact_family_key,
  artifact_version,
  problem_id,
  artifact_type,
  novelty_state,
  status,
  visibility,
  title,
  summary,
  payload,
  registry_hash,
  source_commit,
  supersedes_artifact_id
) values (
  'MATH-001:foundation:v3',
  'MATH-001:foundation',
  3,
  'MATH-001',
  'PROGRAM_NOTE',
  'UNASSESSED',
  'ACTIVE',
  'PUBLIC',
  'MATH-001 — Discovery Reliability',
  'Canonical MATH-001 foundation after sequential evidence, immutable search accounting, and multiplicity-control implementation.',
  jsonb_build_object(
    'implemented_results', jsonb_build_array(
      'MATH-001-P1',
      'MATH-001-P2',
      'MATH-001-B1',
      'MATH-001-A1',
      'MATH-001-C-M1'
    ),
    'search_ledger_version', 'math001-search-ledger-v1',
    'multiplicity_version', 'math001-multiplicity-v1',
    'multiplicity_plan_version', 'math001-multiplicity-plan-v1',
    'final_multiplicity_policy_frozen', false,
    'final_alpha_or_fdr_target_frozen', false,
    'theory_can_change_live_trading', false,
    'theory_can_open_protected_research_stages', false,
    'novelty_claimed', false
  ),
  'f1bdf30a487366a176a69a4db171e51ad361d29e76a325cd93ec3fdd0003d4f3',
  'a10cfc13570267f141acad8676cacf775bcb9654',
  (
    select artifact_id
    from private.anevum_theory_artifacts
    where artifact_key = 'MATH-001:foundation:v2'
  )
)
on conflict (artifact_key) do nothing;

insert into private.anevum_theory_artifacts (
  artifact_key,
  artifact_family_key,
  artifact_version,
  problem_id,
  artifact_type,
  novelty_state,
  status,
  visibility,
  title,
  summary,
  payload,
  registry_hash,
  source_commit
) values (
  'MATH-001:C:v1',
  'MATH-001:C',
  1,
  'MATH-001',
  'RESULT',
  'APPLICATION',
  'ACTIVE',
  'PUBLIC',
  'Multiplicity-control study engine',
  'Deterministic research implementations of Bonferroni, Holm, BH, BY, base e-BH, e-LOND, and async-e-LOND with proposal-bound freeze-time plans and durable append-only persistence.',
  jsonb_build_object(
    'note_path', 'research/notes/MATH-001-C-multiplicity.md',
    'model_path', 'app/research_agent/multiplicity.py',
    'runner_path', 'scripts/math001_c_multiplicity.py',
    'migration_path', 'database/20260928121527_math001_c_multiplicity.sql',
    'fixed_family_methods', jsonb_build_array(
      'bonferroni','holm','bh','by','e_bh'
    ),
    'online_study_methods', jsonb_build_array(
      'e_lond','async_e_lond'
    ),
    'bh_dependence_requirement', 'INDEPENDENCE_OR_PRDS',
    'e_bh_dependence_scope', 'ARBITRARY',
    'online_dependence_scope', 'ARBITRARY',
    'final_policy_frozen', false,
    'production_authority', false,
    'protected_stage_authority', false,
    'novelty_claimed', false
  ),
  'f1bdf30a487366a176a69a4db171e51ad361d29e76a325cd93ec3fdd0003d4f3',
  'a10cfc13570267f141acad8676cacf775bcb9654'
)
on conflict (artifact_key) do nothing;

insert into private.anevum_theory_artifacts (
  artifact_key,
  artifact_family_key,
  artifact_version,
  problem_id,
  artifact_type,
  novelty_state,
  status,
  visibility,
  title,
  summary,
  payload,
  registry_hash,
  source_commit
) values (
  'MATH-001:C-BENCHMARK:v1',
  'MATH-001:C-BENCHMARK',
  1,
  'MATH-001',
  'SIMULATION',
  'APPLICATION',
  'ACTIVE',
  'PUBLIC',
  'Multiplicity benchmark v1',
  'A deterministic 2,000-replicate known-truth benchmark comparing fixed-family and online multiplicity procedures under independent and signed common-factor scenarios.',
  jsonb_build_object(
    'artifact_path', 'research/results/MATH-001-C-benchmark-v1.json',
    'runner_path', 'scripts/math001_c_multiplicity.py',
    'replicates', 2000,
    'hypothesis_count', 40,
    'nonnull_count', 5,
    'effect', 2.5,
    'rho', 0.65,
    'alpha', 0.05,
    'seed', 1002,
    'diagnostic_conclusion',
      'Current one-shot e-value calibration is too conservative at this benchmark scale; do not freeze e-BH/e-LOND as final RHEN policy from this result.',
    'negative_result_preserved', true,
    'final_policy_frozen', false,
    'production_authority', false,
    'protected_stage_authority', false
  ),
  'f1bdf30a487366a176a69a4db171e51ad361d29e76a325cd93ec3fdd0003d4f3',
  'a10cfc13570267f141acad8676cacf775bcb9654'
)
on conflict (artifact_key) do nothing;
