-- Foundation v2: durable CODEX_HANDOFF jobs reuse existing commands/jobs/events.
begin;
create unique index if not exists iren_one_open_codex_handoff
    on iren.jobs(objective_key) where job_type='CODEX_HANDOFF' and status='WAITING';
create index if not exists iren_codex_handoff_history
    on iren.jobs(objective_key,created_at desc) where job_type='CODEX_HANDOFF';

insert into iren.objectives(objective_key,title,description,status,owner_system,priority,
    dependencies,success_criteria,protected_action,metadata)
values(
    'iren.runtime-evidence.v1',
    'Complete IREN deployment evidence for isolated software verification',
    'Extend IREN read-only runtime observation to cover the current Railway service inventory with deployment and revision provenance. Existing topology covers only part of the live inventory and some legacy health responses omit provenance. Make missing or stale identity explicitly block Codex verification, expose the evidence in Command, and demonstrate that only expected services changed. Reuse the existing observation loop and Foundation state; do not add a second planner, provider write authority, secrets, or trading changes.',
    'READY','IREN',115,
    '["iren.verifier.v1","iren.model-worker.v1"]'::jsonb,
    '{"complete_deployment_inventory":true}'::jsonb,false,
    '{"job_type":"SOFTWARE_BUILD","source":"codex-handoff-audit-2026-10-02",
      "codex_scope":{"allowed_paths":["app/iren/","foundation/iren_","tests/test_iren_","docs/iren/"],
      "expected_services":["IREN","IREN_EXECUTOR","FOUNDATION"],
      "verification_checks":{"complete_deployment_inventory":{"source":"IREN","path":["runtime_evidence","complete_deployment_inventory"]}}}}'::jsonb
) on conflict(objective_key) do nothing;

-- Explicit owner request to create one real handoff after rollout; not a model job.
insert into iren.commands(command_id,requested_by,target_system,command,arguments,status,
    command_text,source,context,created_at,updated_at)
values('c0de0001-2026-4002-8000-000000000015','owner','IREN','prepare for Codex','{}','QUEUED',
    'prepare for Codex','command','{"source":"owner-request-codex-handoff-v1"}',now(),now())
on conflict(command_id) do nothing;
commit;
