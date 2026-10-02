-- ANEVUM Foundation v2
-- Migration 0012: explicit owner-requested stable-build verification.
-- This does not alter Autopilot limits; it queues one non-protected deterministic verifier job.

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
    'iren-human:stable-build-verify:2026-10-02',
    'IREN',
    'CONTROL_STABLE_BUILD_VERIFY',
    'QUEUED',
    1,
    '{"instructions":"Verify the evidence-gated IREN stable-build objective against current canonical state."}'::jsonb,
    '{}'::jsonb,
    'iren.stable-build.2026-10-05',
    'Verify IREN stable build',
    'Run the deterministic evidence-gated stable-build verifier against current canonical state. Do not perform protected actions.',
    'CONTROL_STABLE_BUILD_VERIFY',
    120,
    false,
    false,
    'owner',
    'human',
    '{"source":"explicit-owner-continuation","autopilot_bypass":false,"protected_action":false}'::jsonb
where exists (
    select 1
    from iren.objectives
    where objective_key='iren.stable-build.2026-10-05'
      and status <> 'COMPLETE'
)
and not exists (
    select 1
    from iren.jobs
    where objective_key='iren.stable-build.2026-10-05'
      and status in ('QUEUED','RUNNING','WAITING','BLOCKED','NEEDS_APPROVAL')
)
on conflict (job_key) do nothing;

insert into iren.job_events (job_id, event_type, event)
select
    job_id,
    'CREATED',
    '{"source":"explicit-owner-continuation","job_type":"CONTROL_STABLE_BUILD_VERIFY"}'::jsonb
from iren.jobs j
where j.job_key='iren-human:stable-build-verify:2026-10-02'
  and not exists (
      select 1
      from iren.job_events e
      where e.job_id=j.job_id
        and e.event_type='CREATED'
  );

commit;
