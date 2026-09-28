-- Sync durable MATH-001 theory artifacts after A1 search-accounting implementation.
-- Canonical registry hash: 8f1c322d2a98dd1932cf2901e1074784d18b4a2128303c7ac383a2019588b776
-- Source commit: a176a213ca025de7dc331b2f6ba0152b13e05e40

update private.anevum_theory_artifacts
set status = 'ARCHIVED'
where artifact_key = 'MATH-001:foundation:v1'
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
  'MATH-001:foundation:v2',
  'MATH-001:foundation',
  2,
  'MATH-001',
  'PROGRAM_NOTE',
  'UNASSESSED',
  'ACTIVE',
  'PUBLIC',
  'MATH-001 — Discovery Reliability',
  'Canonical MATH-001 foundation after B1 sequential evidence and A1 immutable search-accounting implementation.',
  jsonb_build_object(
    'current_problem', true,
    'implemented_results', jsonb_build_array(
      'MATH-001-P1',
      'MATH-001-P2',
      'MATH-001-B1',
      'MATH-001-A1'
    ),
    'search_ledger_version', 'math001-search-ledger-v1',
    'search_ledger_note_path', 'research/notes/MATH-001-A1-search-ledger.md',
    'evidence_note_path', 'research/notes/MATH-001-B1-evidence-process.md',
    'theory_can_change_live_trading', false,
    'theory_can_open_protected_research_stages', false,
    'final_multiplicity_policy_frozen', false,
    'novelty_claimed', false
  ),
  '8f1c322d2a98dd1932cf2901e1074784d18b4a2128303c7ac383a2019588b776',
  'a176a213ca025de7dc331b2f6ba0152b13e05e40',
  (
    select artifact_id
    from private.anevum_theory_artifacts
    where artifact_key = 'MATH-001:foundation:v1'
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
  'MATH-001:A1:v1',
  'MATH-001:A1',
  1,
  'MATH-001',
  'RESULT',
  'APPLICATION',
  'ACTIVE',
  'PUBLIC',
  'Immutable hypothesis and search-exposure ledger',
  'RHEN now assigns deterministic research identities and preserves proposal, data-exposure, result-inspection, rejection, freeze, and archival history in an append-only ledger, including historical backfill of prior edge research.',
  jsonb_build_object(
    'note_path', 'research/notes/MATH-001-A1-search-ledger.md',
    'model_path', 'app/research_agent/search_ledger.py',
    'migration_path', 'database/20260928021901_math001_a1_search_ledger.sql',
    'gateway_path', 'supabase/functions/research-agent-gateway/index.ts',
    'ledger_version', 'math001-search-ledger-v1',
    'historical_family_count_at_activation', 6,
    'historical_candidate_count_at_activation', 6,
    'historical_terminal_rejections_at_activation', 5,
    'rdr_v2_1_classification', 'corpus_quality_failure_not_performance_rejection',
    'append_only', true,
    'production_authority', false,
    'protected_stage_authority', false,
    'final_multiplicity_policy_frozen', false,
    'novelty_claimed', false
  ),
  '8f1c322d2a98dd1932cf2901e1074784d18b4a2128303c7ac383a2019588b776',
  'a176a213ca025de7dc331b2f6ba0152b13e05e40'
)
on conflict (artifact_key) do nothing;
