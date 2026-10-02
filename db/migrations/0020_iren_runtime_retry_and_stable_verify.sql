-- ANEVUM Foundation v2
-- Migration 0020: queue the retry-capable runtime verifier and a fresh stable-build gate.

begin;

insert into iren.jobs (
    job_id,job_key,owner_system,workflow,status,max_attempts,input,error,
    objective_key,title,instructions,job_type,priority,protected_action,
    requires_human,requested_by,requested_via,metadata
)
select
    gen_random_uuid(),
    'iren-control:runtime-evidence-verify:v5',
    'IREN',
    'CONTROL_RUNTIME_EVIDENCE_VERIFY',
    'QUEUED',
    100,
    '{"instructions":"Retry canonical runtime inventory verification until current topology is complete."}'::jsonb,
    '{}'::jsonb,
    'iren.runtime-evidence.v1',
    'Verify converged IREN runtime deployment evidence',
    'Verify current canonical topology. Requeue on transient incomplete inventory; complete only when deployment inventory is complete.',
    'CONTROL_RUNTIME_EVIDENCE_VERIFY',
    160,
    false,
    false,
    'IREN',
    'system',
    '{"source":"runtime-evidence-retry-v1","paid_model_call":false,"protected_action":false}'::jsonb
where exists (
    select 1 from iren.objectives
    where objective_key='iren.runtime-evidence.v1' and status <> 'COMPLETE'
)
and not exists (
    select 1 from iren.jobs
    where objective_key='iren.runtime-evidence.v1'
      and job_type='CONTROL_RUNTIME_EVIDENCE_VERIFY'
      and status in ('QUEUED','RUNNING')
)
on conflict (job_key) do nothing;

insert into iren.job_events(job_id,event_type,event)
select job_id,'CREATED',
       '{"source":"runtime-evidence-retry-v1","job_type":"CONTROL_RUNTIME_EVIDENCE_VERIFY"}'::jsonb
from iren.jobs j
where j.job_key='iren-control:runtime-evidence-verify:v5'
  and not exists (
      select 1 from iren.job_events e
      where e.job_id=j.job_id and e.event_type='CREATED'
  );

insert into iren.jobs (
    job_id,job_key,owner_system,workflow,status,max_attempts,input,error,
    objective_key,title,instructions,job_type,priority,protected_action,
    requires_human,requested_by,requested_via,metadata
)
select
    gen_random_uuid(),
    'iren-human:stable-build-verify:v2:2026-10-02',
    'IREN',
    'CONTROL_STABLE_BUILD_VERIFY',
    'QUEUED',
    1,
    '{"instructions":"Verify the evidence-gated IREN stable build against current canonical state."}'::jsonb,
    '{}'::jsonb,
    'iren.stable-build.2026-10-05',
    'Verify current IREN stable build',
    'Run the deterministic stable-build verifier against current canonical state. Do not perform protected actions.',
    'CONTROL_STABLE_BUILD_VERIFY',
    120,
    false,
    false,
    'owner',
    'human',
    '{"source":"owner-continuation-2026-10-02","autopilot_bypass":false,"protected_action":false}'::jsonb
where exists (
    select 1 from iren.objectives
    where objective_key='iren.stable-build.2026-10-05' and status <> 'COMPLETE'
)
and not exists (
    select 1 from iren.jobs
    where objective_key='iren.stable-build.2026-10-05'
      and status in ('QUEUED','RUNNING','WAITING','BLOCKED','NEEDS_APPROVAL')
)
on conflict (job_key) do nothing;

insert into iren.job_events(job_id,event_type,event)
select job_id,'CREATED',
       '{"source":"owner-continuation-2026-10-02","job_type":"CONTROL_STABLE_BUILD_VERIFY"}'::jsonb
from iren.jobs j
where j.job_key='iren-human:stable-build-verify:v2:2026-10-02'
  and not exists (
      select 1 from iren.job_events e
      where e.job_id=j.job_id and e.event_type='CREATED'
  );

commit;
