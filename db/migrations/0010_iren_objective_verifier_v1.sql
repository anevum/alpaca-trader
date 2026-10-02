-- ANEVUM Foundation v2
-- Migration 0010: activate objective verification and safe typed executors.

begin;

update iren.settings
set autopilot_max_jobs_per_day = 6,
    updated_by = 'foundation-migration-0010',
    updated_at = now()
where singleton;

update iren.objectives
set status='READY',
    dependencies='[]'::jsonb,
    metadata = metadata || '{"classification":"ACTIVE","job_type":"CONTROL_CAPABILITIES","auto_activate":true}'::jsonb,
    updated_at=now()
where objective_key='iren.deterministic-executors.v1'
  and status <> 'COMPLETE';

update iren.objectives
set status='FUTURE',
    dependencies='["iren.deterministic-executors.v1"]'::jsonb,
    metadata = metadata || '{"classification":"FUTURE","job_type":"CONTROL_VERIFIER_SELFTEST","auto_activate":true}'::jsonb,
    updated_at=now()
where objective_key='iren.verifier.v1'
  and status <> 'COMPLETE';

update iren.objectives
set status='FUTURE',
    dependencies='["iren.deterministic-executors.v1","iren.verifier.v1"]'::jsonb,
    protected_action=true,
    metadata = metadata || '{"classification":"FUTURE","job_type":"SOFTWARE_BUILD","auto_activate":false,"requires_explicit_authorization":true}'::jsonb,
    updated_at=now()
where objective_key='iren.model-worker.v1'
  and status <> 'COMPLETE';

commit;
