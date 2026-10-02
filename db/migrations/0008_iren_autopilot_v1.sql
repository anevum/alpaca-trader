-- ANEVUM Foundation v2
-- Migration 0008: enable bounded IREN Autopilot v1 and seed the stable-build objective graph.

begin;

update iren.settings
set autopilot_enabled = true,
    autopilot_max_jobs_per_day = 3,
    updated_by = 'foundation-migration-0008',
    updated_at = now()
where singleton;

insert into iren.objectives (
    objective_key,
    parent_key,
    title,
    description,
    status,
    owner_system,
    priority,
    dependencies,
    success_criteria,
    protected_action,
    metadata
)
values
(
    'iren.stable-build.2026-10-05',
    null,
    'IREN stable build by 2026-10-05',
    'Reach a stable IREN operating baseline: continuous planning, bounded autopilot, durable objective/job state, deterministic execution, verification, Command visibility, and protected-action escalation.',
    'ACTIVE',
    'IREN',
    120,
    '[]'::jsonb,
    '{"continuous_planner":true,"bounded_autopilot":true,"durable_state":true,"command_visibility":true,"protected_actions_fail_closed":true}'::jsonb,
    false,
    '{"classification":"ACTIVE","target_date":"2026-10-05","source":"iren-control-2026-10-02","job_type":"CONTROL_RECONCILE"}'::jsonb
),
(
    'iren.deterministic-executors.v1',
    'iren.stable-build.2026-10-05',
    'Expand deterministic IREN executors',
    'Add safe native execution capabilities for known GitHub, Railway, Foundation, GRAEN, RHEN, scheduler, health, and verification operations without model spending.',
    'FUTURE',
    'IREN',
    110,
    '["iren.stable-build.2026-10-05"]'::jsonb,
    '{"safe_executor_registry":true,"protected_actions_gated":true}'::jsonb,
    false,
    '{"classification":"FUTURE","source":"iren-control-2026-10-02"}'::jsonb
),
(
    'iren.verifier.v1',
    'iren.stable-build.2026-10-05',
    'Build independent IREN verifier',
    'Verify objective success criteria from canonical telemetry and durable evidence before objectives are completed or promoted.',
    'FUTURE',
    'IREN',
    105,
    '["iren.stable-build.2026-10-05"]'::jsonb,
    '{"evidence_based_completion":true,"fail_closed":true}'::jsonb,
    false,
    '{"classification":"FUTURE","source":"iren-control-2026-10-02"}'::jsonb
),
(
    'iren.model-worker.v1',
    'iren.stable-build.2026-10-05',
    'Add bounded model-backed software worker',
    'Add the optional model-backed implementation executor for novel software work with hard budget, iteration, merge, deploy, and protected-action gates.',
    'FUTURE',
    'IREN',
    90,
    '["iren.deterministic-executors.v1","iren.verifier.v1"]'::jsonb,
    '{"budget_cap":true,"iteration_cap":true,"verification_required":true}'::jsonb,
    true,
    '{"classification":"FUTURE","source":"iren-control-2026-10-02","requires_explicit_authorization":true}'::jsonb
)
on conflict (objective_key) do update
set title = excluded.title,
    description = excluded.description,
    owner_system = excluded.owner_system,
    priority = excluded.priority,
    dependencies = excluded.dependencies,
    success_criteria = excluded.success_criteria,
    protected_action = excluded.protected_action,
    metadata = excluded.metadata,
    updated_at = now();

commit;
