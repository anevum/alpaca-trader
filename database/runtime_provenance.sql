-- RHEN runtime/deployment provenance projection.
--
-- Strategy provenance remains immutable in private.trading_strategy_versions.
-- The logical run remains stable across process restarts and redeployments.
-- Each runtime_start event is append-only; this trigger projects the newest
-- runtime identity onto the existing trading_runs row without creating a run.

create or replace function private.project_runtime_provenance()
returns trigger
language plpgsql
set search_path = private, pg_temp
as $$
declare
  v_runtime jsonb;
  v_origin jsonb;
  v_current_snapshot jsonb;
  v_git_commit text;
  v_deployment_id text;
begin
  if new.event_type <> 'runtime_start' or new.run_id is null then
    return new;
  end if;

  -- Backward compatibility: historical runtime_start events predate provenance.
  if jsonb_typeof(new.payload->'runtime') is distinct from 'object' then
    return new;
  end if;

  v_runtime := new.payload->'runtime';
  v_git_commit := nullif(v_runtime->>'git_commit', '');
  v_deployment_id := nullif(v_runtime->>'deployment_id', '');

  select
    config_snapshot,
    coalesce(
      config_snapshot->'provenance'->'run_origin',
      jsonb_strip_nulls(
        jsonb_build_object(
          'git_commit', git_commit,
          'deployment_id', deployment_id,
          'started_at', started_at
        )
      )
    )
  into v_current_snapshot, v_origin
  from private.trading_runs
  where run_id = new.run_id
    and strategy_version_id is not distinct from new.strategy_version_id
  for update;

  if not found then
    raise exception
      'runtime_start references unknown run or mismatched strategy: run_id=%, strategy_version_id=%',
      new.run_id, new.strategy_version_id;
  end if;

  update private.trading_runs
  set
    git_commit = coalesce(v_git_commit, 'UNKNOWN'),
    deployment_id = v_deployment_id,
    config_snapshot = jsonb_set(
      coalesce(v_current_snapshot, '{}'::jsonb),
      '{provenance}',
      coalesce(v_current_snapshot->'provenance', '{}'::jsonb)
        || jsonb_build_object(
          'run_origin', v_origin,
          'current_runtime', v_runtime,
          'current_configuration', coalesce(new.payload->'configuration', '{}'::jsonb),
          'last_runtime_event_at', new.occurred_at
        ),
      true
    )
  where run_id = new.run_id;

  return new;
end;
$$;

drop trigger if exists trading_runtime_provenance_projection
on private.trading_events;

create trigger trading_runtime_provenance_projection
after insert on private.trading_events
for each row
when (new.event_type = 'runtime_start')
execute function private.project_runtime_provenance();
