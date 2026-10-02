-- ANEVUM Foundation v2
-- Migration 0011: make the Monday stable-build objective evidence-gated.

begin;

update iren.objectives
set status='ACTIVE',
    success_criteria = '{
      "continuous_planner": true,
      "bounded_autopilot": true,
      "durable_state": true,
      "command_visibility": true,
      "protected_actions_fail_closed": true,
      "control_state_healthy": true,
      "deterministic_executor_complete": true,
      "verifier_complete": true
    }'::jsonb,
    metadata = metadata || '{
      "classification":"ACTIVE",
      "job_type":"CONTROL_STABLE_BUILD_VERIFY",
      "auto_activate":true,
      "target_date":"2026-10-05"
    }'::jsonb,
    updated_at=now()
where objective_key='iren.stable-build.2026-10-05'
  and status <> 'COMPLETE';

commit;
