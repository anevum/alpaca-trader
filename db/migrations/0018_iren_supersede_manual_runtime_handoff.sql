-- ANEVUM Foundation v2
-- Migration 0018: supersede the stale manual-Codex handoff after direct implementation,
-- preserve history, and queue deterministic runtime-evidence verification.

begin;

with superseded as (
    update iren.jobs
    set status='CANCELLED',
        output=coalesce(output,'{}'::jsonb) || jsonb_build_object(
            'handoff_status','SUPERSEDED',
            'superseded_reason','manual_implementation_while_work_credits_unavailable'
        ),
        updated_at=now(),
        completed_at=now()
    where objective_key='iren.runtime-evidence.v1'
      and job_type='CODEX_HANDOFF'
      and status='WAITING'
    returning job_id
)
insert into iren.job_events(job_id,event_type,event)
select job_id,'SUPERSEDED',
       '{"reason":"manual_implementation_while_work_credits_unavailable","source":"owner-directed-direct-build"}'::jsonb
from superseded;

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
    'iren-control:runtime-evidence-verify:v3',
    'IREN',
    'CONTROL_RUNTIME_EVIDENCE_VERIFY',
    'QUEUED',
    1,
    '{"instructions":"Verify converged deployment inventory after superseding the stale manual Codex handoff."}'::jsonb,
    '{}'::jsonb,
    'iren.runtime-evidence.v1',
    'Verify completed IREN runtime deployment evidence',
    'Verify canonical IREN topology reports complete deployment inventory. Complete the objective only from current evidence.',
    'CONTROL_RUNTIME_EVIDENCE_VERIFY',
    140,
    false,
    false,
    'owner',
    'system',
    '{"source":"manual-codex-supersession-2026-10-02","paid_model_call":false,"protected_action":false}'::jsonb
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
      and job_type <> 'CODEX_HANDOFF'
      and status in ('QUEUED','RUNNING','WAITING','BLOCKED','NEEDS_APPROVAL')
)
on conflict (job_key) do nothing;

insert into iren.job_events(job_id,event_type,event)
select job_id,'CREATED',
       '{"source":"manual-codex-supersession-2026-10-02","job_type":"CONTROL_RUNTIME_EVIDENCE_VERIFY"}'::jsonb
from iren.jobs j
where j.job_key='iren-control:runtime-evidence-verify:v3'
  and not exists (
      select 1
      from iren.job_events e
      where e.job_id=j.job_id and e.event_type='CREATED'
  );

commit;
