-- ANEVUM Foundation v2
-- Migration 0014: explicit owner-authorized verification of IREN model worker v1.
-- No paid model invocation is performed by this verification job.

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
    'iren-human:model-worker-verify:2026-10-02',
    'IREN',
    'CONTROL_MODEL_WORKER_VERIFY',
    'QUEUED',
    1,
    '{"instructions":"Verify the deployed bounded model worker contract without invoking a paid model."}'::jsonb,
    '{}'::jsonb,
    'iren.model-worker.v1',
    'Verify IREN model worker v1',
    'Verify the deployed bounded model worker contract. Do not invoke a paid model and do not perform protected actions.',
    'CONTROL_MODEL_WORKER_VERIFY',
    115,
    false,
    false,
    'owner',
    'human',
    '{"source":"explicit-owner-authorization","paid_model_call":false,"protected_action":false}'::jsonb
where exists (
    select 1
    from iren.objectives
    where objective_key='iren.model-worker.v1'
      and status <> 'COMPLETE'
)
and not exists (
    select 1
    from iren.jobs
    where objective_key='iren.model-worker.v1'
      and status in ('QUEUED','RUNNING','WAITING','BLOCKED','NEEDS_APPROVAL')
)
on conflict (job_key) do nothing;

insert into iren.job_events (job_id, event_type, event)
select
    job_id,
    'CREATED',
    '{"source":"explicit-owner-authorization","job_type":"CONTROL_MODEL_WORKER_VERIFY","paid_model_call":false}'::jsonb
from iren.jobs j
where j.job_key='iren-human:model-worker-verify:2026-10-02'
  and not exists (
      select 1
      from iren.job_events e
      where e.job_id=j.job_id
        and e.event_type='CREATED'
  );

commit;
