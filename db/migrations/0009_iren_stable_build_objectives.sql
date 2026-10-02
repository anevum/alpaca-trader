-- ANEVUM Foundation v2
-- Migration 0009: normalize stable-build objective graph for executable child work.

begin;

update iren.objectives
set status='COMPLETE',
    completed_at=coalesce(completed_at, now()),
    updated_at=now(),
    metadata = metadata || '{"verification":"merged_deployed_runtime_verified"}'::jsonb
where objective_key='iren.stable-build.2026-10-05'
  and false;

update iren.objectives
set status='READY',
    dependencies='[]'::jsonb,
    protected_action=false,
    metadata = metadata || '{"classification":"ACTIVE","job_type":"CONTROL_VERIFY"}'::jsonb,
    updated_at=now()
where objective_key='iren.deterministic-executors.v1';

update iren.objectives
set status='FUTURE',
    dependencies='["iren.deterministic-executors.v1"]'::jsonb,
    metadata = metadata || '{"classification":"FUTURE","job_type":"CONTROL_VERIFY"}'::jsonb,
    updated_at=now()
where objective_key='iren.verifier.v1';

update iren.objectives
set status='FUTURE',
    dependencies='["iren.deterministic-executors.v1","iren.verifier.v1"]'::jsonb,
    protected_action=true,
    metadata = metadata || '{"classification":"FUTURE","job_type":"SOFTWARE_BUILD","requires_explicit_authorization":true}'::jsonb,
    updated_at=now()
where objective_key='iren.model-worker.v1';

commit;
