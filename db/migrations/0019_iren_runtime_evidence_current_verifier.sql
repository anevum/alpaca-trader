-- ANEVUM Foundation v2
-- Migration 0019: retire stale pre-handler runtime-evidence verifier jobs and ensure
-- one current deterministic verifier is queued against converged topology evidence.

begin;

with retired as (
    update iren.jobs
    set status='CANCELLED',
        error=coalesce(error,'{}'::jsonb) || '{"reason":"stale_pre_convergence_runtime_verifier"}'::jsonb,
        updated_at=now(),
        completed_at=now(),
        lease_owner=null,
        lease_until=null
    where objective_key='iren.runtime-evidence.v1'
      and job_type='CONTROL_RUNTIME_EVIDENCE_VERIFY'
      and status in ('WAITING','BLOCKED','NEEDS_APPROVAL')
    returning job_id
)
insert into iren.job_events(job_id,event_type,event)
select job_id,'CANCELLED',
       '{"reason":"stale_pre_convergence_runtime_verifier","source":"runtime-evidence-lifecycle-repair"}'::jsonb
from retired;

insert into iren.jobs (
    job_id,job_key,owner_system,workflow,status,max_attempts,input,error,
    objective_key,title,instructions,job_type,priority,protected_action,
    requires_human,requested_by,requested_via,metadata
)
select
    gen_random_uuid(),
    'iren-control:runtime-evidence-verify:v4',
    'IREN',
    'CONTROL_RUNTIME_EVIDENCE_VERIFY',
    'QUEUED',
    1,
    '{"instructions":"Verify current converged runtime inventory using the deployed runtime-evidence verifier."}'::jsonb,
    '{}'::jsonb,
    'iren.runtime-evidence.v1',
    'Verify current IREN runtime deployment evidence',
    'Verify canonical topology reports complete deployment inventory now. Complete only from current evidence.',
    'CONTROL_RUNTIME_EVIDENCE_VERIFY',
    150,
    false,
    false,
    'IREN',
    'system',
    '{"source":"runtime-evidence-lifecycle-repair-2026-10-02","paid_model_call":false,"protected_action":false}'::jsonb
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
       '{"source":"runtime-evidence-lifecycle-repair-2026-10-02","job_type":"CONTROL_RUNTIME_EVIDENCE_VERIFY"}'::jsonb
from iren.jobs j
where j.job_key='iren-control:runtime-evidence-verify:v4'
  and not exists (
      select 1 from iren.job_events e
      where e.job_id=j.job_id and e.event_type='CREATED'
  );

commit;
