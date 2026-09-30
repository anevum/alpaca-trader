insert into private.iren_objectives (
  objective_key,parent_key,title,description,status,owner_system,priority,
  dependencies,success_criteria,protected_action,metadata
) values (
  'IREN-EXECUTION-ROUTER','ANEVUM-AUTONOMY',
  'Deploy IREN execution router',
  'Create an independently deployable execution boundary that accepts durable IREN jobs, enforces protected-action and spending gates, routes supported work to execution backends, and reports results through authenticated callbacks.',
  'READY','IREN',99,
  '["IREN-OBJECTIVE-JOB-ENGINE"]'::jsonb,
  '{"independent_service":true,"authenticated_routing":true,"protected_action_gate":true,"callback_path":true,"model_spending_default_off":true}'::jsonb,
  false,
  '{"job_type":"SOFTWARE_BUILD","canonical":true}'::jsonb
)
on conflict (objective_key) do update set
  title=excluded.title,
  description=excluded.description,
  owner_system=excluded.owner_system,
  priority=excluded.priority,
  dependencies=excluded.dependencies,
  success_criteria=excluded.success_criteria,
  metadata=private.iren_objectives.metadata || excluded.metadata,
  updated_at=now();

update private.iren_objectives
set dependencies='["IREN-OBJECTIVE-JOB-ENGINE","IREN-EXECUTION-ROUTER"]'::jsonb,
    updated_at=now()
where objective_key='GRAEN-INDEPENDENT-RUNTIME';
