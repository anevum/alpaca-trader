-- Advance durable theory artifacts to canonical registry v2.
-- Registry hash: 8e7ac99956e628cb47d88caef626dfec560aedc9dfe9aac380f5ecc0cf5b0088

update private.anevum_theory_artifacts
set status = 'ARCHIVED'
where artifact_key = 'ATP-001:foundation:v1'
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
  'ATP-001:foundation:v2',
  'ATP-001:foundation',
  2,
  'ATP-001',
  'PROGRAM_NOTE',
  'UNASSESSED',
  'ACTIVE',
  'PUBLIC',
  'ATP-001 — Adaptive Inference Under Nonstationarity',
  'Canonical founding problem plus the first restricted analytical baseline, ATP-001-P1.',
  jsonb_build_object(
    'note_path', 'research/notes/ATP-001-foundation.md',
    'registered_result', 'ATP-001-P1',
    'theory_can_change_live_trading', false,
    'theory_can_open_protected_research_stages', false,
    'novelty_claimed', false
  ),
  '8e7ac99956e628cb47d88caef626dfec560aedc9dfe9aac380f5ecc0cf5b0088',
  'bff371eee263273041adcded8a25b94e44e496c0',
  (select artifact_id from private.anevum_theory_artifacts where artifact_key = 'ATP-001:foundation:v1')
)
on conflict (artifact_key) do nothing;

insert into private.anevum_theory_artifacts (
  artifact_key,
  artifact_family_key,
  artifact_version,
  problem_id,
  conjecture_id,
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
  'ATP-001:P1:v1',
  'ATP-001:P1',
  1,
  'ATP-001',
  'ATP-001-C1',
  'DERIVATION',
  'APPLICATION',
  'ACTIVE',
  'PUBLIC',
  'Exact myopic switching thresholds in the symmetric two-state model',
  'In the restricted one-period symmetric binary-state model, positive switching cost creates exact upper and lower belief thresholds and a hysteresis interval of width kappa divided by the correct-versus-wrong reward spread.',
  jsonb_build_object(
    'proof_path', 'research/notes/ATP-001-P1-myopic-switching.md',
    'model_path', 'app/research_agent/atp001.py',
    'tests_path', 'tests/test_atp001.py',
    'general_conjecture_proved', false,
    'novelty_claimed', false
  ),
  '8e7ac99956e628cb47d88caef626dfec560aedc9dfe9aac380f5ecc0cf5b0088',
  'bff371eee263273041adcded8a25b94e44e496c0'
)
on conflict (artifact_key) do nothing;
