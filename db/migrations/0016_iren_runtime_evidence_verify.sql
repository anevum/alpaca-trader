-- ANEVUM Foundation v2
-- Migration 0016: queue deterministic verification for runtime evidence objective.

begin;

update iren.objectives
set metadata = metadata || '{"job_type":"CONTROL_RUNTIME_EVIDENCE_VERIFY","classification":"ACTIVE"}'::jsonb,
    updated_at = now()
where objective_key='iren.runtime-evidence.v1'
  and status <> 'COMPLETE';

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
    'iren-control:runtime-evidence-verify:v1',
    'IREN',
    'CONTROL_RUNTIME_EVIDENCE_VERIFY',
    'QUEUED',
    1,
    '{"instructions":"Verify complete deployment inventory from canonical IREN topology evidence."}'::jsonb,
    '{}'::jsonb,
    'iren.runtime-evidence.v1',
    'Verify IREN runtime deployment evidence',
    'Verify complete deployment inventory from canonical IREN topology. Fail closed on any missing deployment, revision, or readiness evidence.',
    'CONTROL_RUNTIME_EVIDENCE_VERIFY',
    116,
    false,
    false,
    'IREN',
    'system',
    '{"source":"runtime-evidence-v1","paid_model_call":false,"protected_action":false}'::jsonb
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
select job_id,'CREATED','{"source":"runtime-evidence-v1","job_type":"CONTROL_RUNTIME_EVIDENCE_VERIFY"}'::jsonb
from iren.jobs j
where j.job_key='iren-control:runtime-evidence-verify:v1'
  and not exists (
      select 1 from iren.job_events e
      where e.job_id=j.job_id and e.event_type='CREATED'
  );

commit;
