-- ANEVUM Foundation v2
-- Migration 0017: requeue runtime-evidence verification after live inventory convergence.

begin;

insert into iren.jobs (
    job_id,
    job_key,
    owner_system,
    workflow,
    status,
    max_attempts,
    input,
    error,
    objective_key,
    title,
    instructions,
    job_type,
    priority,
    protected_action,
    requires_human,
    requested_by,
    requested_via,
    metadata
)
select
    gen_random_uuid(),
    'iren-control:runtime-evidence-verify:v2',
    'IREN',
    'CONTROL_RUNTIME_EVIDENCE_VERIFY',
    'QUEUED',
    1,
    '{"instructions":"Re-verify complete deployment inventory after production provenance convergence."}'::jsonb,
    '{}'::jsonb,
    'iren.runtime-evidence.v1',
    'Re-verify IREN runtime deployment evidence',
    'Verify current canonical topology reports complete deployment inventory. Do not complete on stale or missing evidence.',
    'CONTROL_RUNTIME_EVIDENCE_VERIFY',
    130,
    false,
    false,
    'IREN',
    'system',
    '{"source":"runtime-evidence-convergence-2026-10-02","paid_model_call":false,"protected_action":false}'::jsonb
where exists (
    select 1
    from iren.objectives
    where objective_key='iren.runtime-evidence.v1'
      and status <> 'COMPLETE'
)
and not exists (
    select 1
    from iren.jobs
    where objective_key='iren.runtime-evidence.v1'
      and status in ('QUEUED','RUNNING','WAITING','BLOCKED','NEEDS_APPROVAL')
)
on conflict (job_key) do nothing;

insert into iren.job_events (job_id,event_type,event)
select job_id,'CREATED','{"source":"runtime-evidence-convergence-2026-10-02","job_type":"CONTROL_RUNTIME_EVIDENCE_VERIFY"}'::jsonb
from iren.jobs j
where j.job_key='iren-control:runtime-evidence-verify:v2'
  and not exists (
      select 1 from iren.job_events e
      where e.job_id=j.job_id and e.event_type='CREATED'
  );

commit;
