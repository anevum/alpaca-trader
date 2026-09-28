-- ANEVUM Mathematics & Theory v1.
-- Additive research-only storage. No execution, risk, sizing, strategy, broker,
-- research-stage, holdout, quarantine, or production-promotion authority.

create table private.anevum_theory_artifacts (
  artifact_id uuid primary key default gen_random_uuid(),
  artifact_key text not null unique,
  artifact_family_key text not null,
  artifact_version integer not null default 1,
  problem_id text not null,
  conjecture_id text,
  artifact_type text not null,
  novelty_state text not null default 'UNASSESSED',
  status text not null default 'DRAFT',
  visibility text not null default 'PRIVATE',
  title text not null,
  summary text not null,
  payload jsonb not null default '{}'::jsonb,
  content_hash text not null,
  registry_hash text not null,
  source_commit text,
  linked_experiment_id uuid references private.trading_experiments(experiment_id),
  source_agent_run_id uuid references private.trading_research_agent_runs(run_id),
  supersedes_artifact_id uuid references private.anevum_theory_artifacts(artifact_id),
  created_at timestamptz not null default now(),
  verified_at timestamptz,
  constraint anevum_theory_artifacts_version_check
    check (artifact_version >= 1),
  constraint anevum_theory_artifacts_type_check
    check (artifact_type in (
      'PROGRAM_NOTE','LITERATURE_REVIEW','DEFINITION','CONJECTURE','LEMMA',
      'THEOREM','PROOF_ATTEMPT','COUNTEREXAMPLE','DERIVATION','SIMULATION',
      'EXPERIMENT_LINK','RESULT','REJECTION'
    )),
  constraint anevum_theory_artifacts_novelty_check
    check (novelty_state in (
      'UNASSESSED','KNOWN','APPLICATION','POTENTIALLY_NOVEL',
      'ORIGINAL_VERIFIED','REJECTED'
    )),
  constraint anevum_theory_artifacts_status_check
    check (status in ('DRAFT','ACTIVE','VERIFIED','REJECTED','ARCHIVED')),
  constraint anevum_theory_artifacts_visibility_check
    check (visibility in ('PRIVATE','PUBLIC')),
  constraint anevum_theory_artifacts_payload_check
    check (jsonb_typeof(payload) = 'object'),
  constraint anevum_theory_artifacts_content_hash_check
    check (content_hash ~ '^[0-9a-f]{64}$'),
  constraint anevum_theory_artifacts_registry_hash_check
    check (registry_hash ~ '^[0-9a-f]{64}$'),
  constraint anevum_theory_artifacts_verified_original_check
    check (
      novelty_state <> 'ORIGINAL_VERIFIED'
      or (status = 'VERIFIED' and verified_at is not null)
    ),
  constraint anevum_theory_artifacts_supersession_check
    check (supersedes_artifact_id is null or supersedes_artifact_id <> artifact_id),
  unique (artifact_family_key, artifact_version)
);

create index anevum_theory_artifacts_problem_created_idx
  on private.anevum_theory_artifacts(problem_id, created_at desc);
create index anevum_theory_artifacts_conjecture_created_idx
  on private.anevum_theory_artifacts(conjecture_id, created_at desc)
  where conjecture_id is not null;
create index anevum_theory_artifacts_experiment_idx
  on private.anevum_theory_artifacts(linked_experiment_id)
  where linked_experiment_id is not null;
create index anevum_theory_artifacts_agent_run_idx
  on private.anevum_theory_artifacts(source_agent_run_id)
  where source_agent_run_id is not null;

alter table private.anevum_theory_artifacts enable row level security;
revoke all on private.anevum_theory_artifacts from anon, authenticated;

create table private.anevum_theory_experiment_links (
  link_id uuid primary key default gen_random_uuid(),
  problem_id text not null,
  conjecture_id text,
  experiment_id uuid not null references private.trading_experiments(experiment_id),
  relationship text not null,
  rationale text not null,
  evidence_reference jsonb not null default '{}'::jsonb,
  registry_hash text not null,
  created_at timestamptz not null default now(),
  constraint anevum_theory_experiment_links_relationship_check
    check (relationship in ('TESTS','INFORMS','SUPPORTS','CONTRADICTS','FALSIFIES')),
  constraint anevum_theory_experiment_links_evidence_check
    check (jsonb_typeof(evidence_reference) = 'object'),
  constraint anevum_theory_experiment_links_registry_hash_check
    check (registry_hash ~ '^[0-9a-f]{64}$'),
  unique (problem_id, conjecture_id, experiment_id, relationship)
);

create index anevum_theory_experiment_links_experiment_idx
  on private.anevum_theory_experiment_links(experiment_id, created_at desc);

alter table private.anevum_theory_experiment_links enable row level security;
revoke all on private.anevum_theory_experiment_links from anon, authenticated;

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
  content_hash,
  registry_hash
) values (
  'ATP-001:foundation:v1',
  'ATP-001:foundation',
  1,
  'ATP-001',
  'PROGRAM_NOTE',
  'UNASSESSED',
  'ACTIVE',
  'PUBLIC',
  'ATP-001 — Adaptive Inference Under Nonstationarity',
  'Founding problem statement for ANEVUM Mathematics & Theory. It formalizes adaptive decision-making under partial observation, nonstationarity, switching cost, market friction, and finite capital without claiming a proven market edge or mathematical novelty.',
  jsonb_build_object(
    'note_path', 'research/notes/ATP-001-foundation.md',
    'theory_can_change_live_trading', false,
    'theory_can_open_protected_research_stages', false,
    'novelty_claimed', false
  ),
  '6a0a3b987a2bfd8b9229fba909e99c45809ddea60486462c94829e0481bc85ed',
  '6a0a3b987a2bfd8b9229fba909e99c45809ddea60486462c94829e0481bc85ed'
)
on conflict (artifact_key) do nothing;
