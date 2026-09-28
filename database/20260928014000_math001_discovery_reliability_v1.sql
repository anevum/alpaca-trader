-- MATH-001 Discovery Reliability v1.
-- Research-only durable theory artifacts. No live strategy, broker, risk,
-- sizing, research-stage, holdout, quarantine, or promotion authority.
-- Canonical registry hash: ad6477ddec10f59710709d59ba776f439ffa0b8fc087f05c61458d7170d23998

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
  'MATH-001:foundation:v1',
  'MATH-001:foundation',
  1,
  'MATH-001',
  'PROGRAM_NOTE',
  'UNASSESSED',
  'ACTIVE',
  'PUBLIC',
  'MATH-001 — Discovery Reliability',
  'Founding research program for quantifying how much evidence is required before RHEN may treat an apparent edge as statistically credible under repeated search, adaptive inspection, dependence, selection, and nonstationarity.',
  jsonb_build_object(
    'note_path', 'research/notes/MATH-001-B1-evidence-process.md',
    'model_path', 'app/research_agent/math001.py',
    'simulation_path', 'scripts/math001_g1.py',
    'theory_can_change_live_trading', false,
    'theory_can_open_protected_research_stages', false,
    'final_evidence_policy_frozen', false,
    'novelty_claimed', false
  ),
  'ad6477ddec10f59710709d59ba776f439ffa0b8fc087f05c61458d7170d23998',
  'b3cb8f62bd1d9b63bd5357ebaf8061ee4a112c45'
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
  'MATH-001:B1:v1',
  'MATH-001:B1',
  1,
  'MATH-001',
  'DERIVATION',
  'APPLICATION',
  'ACTIVE',
  'PUBLIC',
  'MATH-001-B1 — Canonical sequential evidence primitive',
  'Executable study-only implementation of the fixed-lambda and convex-mixture e-process construction, anytime evidence summary, conservative lower confidence sequence, and pure-null repeated-search simulation.',
  jsonb_build_object(
    'proof_path', 'research/notes/MATH-001-B1-evidence-process.md',
    'model_path', 'app/research_agent/math001.py',
    'tests_path', 'tests/test_math001.py',
    'simulation_path', 'scripts/math001_g1.py',
    'final_evidence_policy_frozen', false,
    'may_confirm_edge', false,
    'may_open_development', false,
    'may_open_validation', false,
    'may_open_holdout', false,
    'may_access_quarantine', false,
    'may_change_production', false,
    'novelty_claimed', false
  ),
  'ad6477ddec10f59710709d59ba776f439ffa0b8fc087f05c61458d7170d23998',
  'b3cb8f62bd1d9b63bd5357ebaf8061ee4a112c45'
)
on conflict (artifact_key) do nothing;
