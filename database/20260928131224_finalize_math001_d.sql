-- Finalize MATH-001-D durable theory state.
-- Canonical registry hash: 147e56ca529aa316a4d04cb609caea536289e698a76a655f823f61f0cb0c889c
-- Source commit: ee36546f72b61c3a15c55e309a8485f3e46ad955

update private.anevum_theory_artifacts
set status = 'ARCHIVED'
where artifact_key = 'MATH-001:foundation:v3'
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
  'MATH-001:foundation:v4',
  'MATH-001:foundation',
  4,
  'MATH-001',
  'PROGRAM_NOTE',
  'UNASSESSED',
  'ACTIVE',
  'PUBLIC',
  'MATH-001 — Discovery Reliability',
  'Canonical MATH-001 foundation after sequential evidence, immutable search accounting, multiplicity control, and conditional-dependence validation.',
  jsonb_build_object(
    'implemented_results', jsonb_build_array(
      'MATH-001-P1',
      'MATH-001-P2',
      'MATH-001-B1',
      'MATH-001-A1',
      'MATH-001-C-M1',
      'MATH-001-D1'
    ),
    'search_ledger_version', 'math001-search-ledger-v1',
    'multiplicity_version', 'math001-multiplicity-v1',
    'dependence_version', 'math001-dependence-v1',
    'dependence_plan_version', 'math001-dependence-plan-v1',
    'final_multiplicity_policy_frozen', false,
    'final_dependence_policy_frozen', false,
    'final_alpha_or_fdr_target_frozen', false,
    'theory_can_change_live_trading', false,
    'theory_can_open_protected_research_stages', false,
    'novelty_claimed', false
  ),
  '147e56ca529aa316a4d04cb609caea536289e698a76a655f823f61f0cb0c889c',
  'ee36546f72b61c3a15c55e309a8485f3e46ad955',
  (
    select artifact_id
    from private.anevum_theory_artifacts
    where artifact_key = 'MATH-001:foundation:v3'
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
  'MATH-001:D:v1',
  'MATH-001:D',
  1,
  'MATH-001',
  'RESULT',
  'APPLICATION',
  'ACTIVE',
  'PUBLIC',
  'Conditional-dependence validity framework',
  'RHEN research now distinguishes dependence compatible with the B1 conditional-mean null from unconditional-zero processes that violate it, freezes dependence safeguards before methodology eligibility, and records immutable dependence plans.',
  jsonb_build_object(
    'note_path', 'research/notes/MATH-001-D-dependence.md',
    'model_path', 'app/research_agent/dependence.py',
    'runner_path', 'scripts/math001_d_dependence.py',
    'migration_path', 'database/20260928125644_math001_d_dependence.sql',
    'dependence_version', 'math001-dependence-v1',
    'dependence_plan_version', 'math001-dependence-plan-v1',
    'iid_required', false,
    'conditional_null_required', true,
    'raw_unbounded_b1_input_allowed', false,
    'required_base_checks', jsonb_build_array(
      'conditional_mean_residual_check',
      'serial_autocorrelation_diagnostic',
      'volatility_clustering_stress',
      'rare_extreme_stress',
      'overlap_double_counting_check'
    ),
    'cross_candidate_check_when_multiple_configurations',
      'cross_candidate_dependence_stress',
    'final_policy_frozen', false,
    'production_authority', false,
    'protected_stage_authority', false,
    'novelty_claimed', false
  ),
  '147e56ca529aa316a4d04cb609caea536289e698a76a655f823f61f0cb0c889c',
  'ee36546f72b61c3a15c55e309a8485f3e46ad955'
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
  'MATH-001:D-BENCHMARK:v1',
  'MATH-001:D-BENCHMARK',
  1,
  'MATH-001',
  'SIMULATION',
  'APPLICATION',
  'ACTIVE',
  'PUBLIC',
  'Dependence benchmark v1',
  'A deterministic 500-replicate known-truth benchmark separating five valid dependent conditional-null worlds from two unconditional-zero conditional-null violations.',
  jsonb_build_object(
    'artifact_path', 'research/results/MATH-001-D-benchmark-v1.json',
    'runner_path', 'scripts/math001_d_dependence.py',
    'replicates', 500,
    'candidates_per_replicate', 20,
    'observations_per_candidate', 120,
    'alpha', 0.05,
    'common_factor_weight', 0.8,
    'ar_phi', 0.75,
    'seed', 1003,
    'valid_null_candidate_e_crossing_max', 0.0149,
    'valid_null_familywise_e_crossing_max', 0.008,
    'valid_null_terminal_e_bh_rejection_max', 0.002,
    'common_factor_mean_abs_cross_candidate_correlation', 0.941573327047043,
    'overlap_ma1_candidate_e_crossing_rate', 0.0829,
    'overlap_ma1_familywise_e_crossing_rate', 0.252,
    'ar1_candidate_e_crossing_rate', 0.2707,
    'ar1_familywise_e_crossing_rate', 0.902,
    'ar1_terminal_e_bh_any_rejection_rate', 0.702,
    'diagnostic_conclusion',
      'Dependence alone did not break B1 calibration in the valid conditional-null worlds; unconditional-zero processes that violated the conditional null materially inflated evidence.',
    'final_policy_frozen', false,
    'production_authority', false,
    'protected_stage_authority', false
  ),
  '147e56ca529aa316a4d04cb609caea536289e698a76a655f823f61f0cb0c889c',
  'ee36546f72b61c3a15c55e309a8485f3e46ad955'
)
on conflict (artifact_key) do nothing;
