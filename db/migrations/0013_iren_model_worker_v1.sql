-- ANEVUM Foundation v2
-- Migration 0013: owner-authorized bounded IREN model worker v1 build state.

begin;

update iren.objectives
set status='ACTIVE',
    protected_action=false,
    success_criteria='{
      "bounded_worker_built": true,
      "draft_pr_only": true,
      "auto_merge_disabled": true,
      "daily_job_cap": 1,
      "budget_required": true,
      "protected_actions_fail_closed": true
    }'::jsonb,
    metadata = metadata || '{
      "classification":"ACTIVE",
      "job_type":"CONTROL_MODEL_WORKER_VERIFY",
      "auto_activate":false,
      "authorization_granted":true,
      "authorization_granted_at":"2026-10-02",
      "runtime_activation_requires_credentials_and_budget":true,
      "model_backend":"openai_responses_api",
      "default_model":"gpt-6-astra",
      "default_reasoning_effort":"high"
    }'::jsonb,
    updated_at=now()
where objective_key='iren.model-worker.v1'
  and status <> 'COMPLETE';

commit;
