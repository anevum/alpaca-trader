create table private.anevum_scheduler_runs (
  run_id uuid primary key default gen_random_uuid(),
  job_key text not null unique,
  workflow_id text not null,
  workflow_version text not null,
  scheduler_version text not null,
  scheduled_at timestamptz not null,
  started_at timestamptz not null default now(),
  completed_at timestamptz,
  status text not null default 'RUNNING',
  trigger_type text not null,
  attempt integer not null default 1,
  max_attempts integer not null default 1,
  worker_identity text,
  source_commit text,
  input_identity text,
  output_identity text,
  error_classification text,
  error_summary jsonb not null default '{}'::jsonb,
  retry_state text,
  catchup_state text,
  slack_notification_status text,
  details jsonb not null default '{}'::jsonb,
  lease_until timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  constraint anevum_scheduler_runs_status_check
    check (status in ('RUNNING','SUCCEEDED','FAILED','MISSED','SKIPPED','STALE')),
  constraint anevum_scheduler_runs_attempt_check
    check (attempt >= 1 and max_attempts >= 1 and attempt <= max_attempts),
  constraint anevum_scheduler_runs_error_summary_check
    check (jsonb_typeof(error_summary) = 'object'),
  constraint anevum_scheduler_runs_details_check
    check (jsonb_typeof(details) = 'object')
);

create index anevum_scheduler_runs_workflow_scheduled_idx
  on private.anevum_scheduler_runs(workflow_id, scheduled_at desc);

create index anevum_scheduler_runs_status_scheduled_idx
  on private.anevum_scheduler_runs(status, scheduled_at desc);

create index anevum_scheduler_runs_lease_idx
  on private.anevum_scheduler_runs(lease_until)
  where status = 'RUNNING';

alter table private.anevum_scheduler_runs enable row level security;
revoke all on private.anevum_scheduler_runs from anon, authenticated;

create or replace function private.anevum_scheduler_claim(p_job jsonb)
returns jsonb
language plpgsql
security definer
set search_path = private, pg_temp
as $function$
declare
  v_row private.anevum_scheduler_runs%rowtype;
  v_job_key text := nullif(btrim(p_job->>'job_key'),'');
  v_workflow_id text := nullif(btrim(p_job->>'workflow_id'),'');
  v_workflow_version text := nullif(btrim(p_job->>'workflow_version'),'');
  v_scheduler_version text := nullif(btrim(p_job->>'scheduler_version'),'');
  v_scheduled_at timestamptz;
  v_trigger_type text := nullif(btrim(p_job->>'trigger_type'),'');
  v_max_attempts integer := greatest(1, least(10, coalesce(nullif(p_job->>'max_attempts','')::integer,1)));
  v_lease_seconds integer := greatest(60, least(3600, coalesce(nullif(p_job->>'lease_seconds','')::integer,900)));
  v_allow_retry boolean := coalesce((p_job->>'allow_retry')::boolean,false);
begin
  if v_job_key is null or v_workflow_id is null or v_workflow_version is null
     or v_scheduler_version is null or v_trigger_type is null then
    raise exception 'invalid_scheduler_claim';
  end if;
  v_scheduled_at := (p_job->>'scheduled_at')::timestamptz;

  insert into private.anevum_scheduler_runs (
    job_key, workflow_id, workflow_version, scheduler_version,
    scheduled_at, started_at, status, trigger_type, attempt, max_attempts,
    worker_identity, source_commit, input_identity, retry_state,
    catchup_state, lease_until, details
  ) values (
    v_job_key, v_workflow_id, v_workflow_version, v_scheduler_version,
    v_scheduled_at, now(), 'RUNNING', v_trigger_type, 1, v_max_attempts,
    nullif(p_job->>'worker_identity',''),
    nullif(p_job->>'source_commit',''),
    nullif(p_job->>'input_identity',''),
    'initial',
    nullif(p_job->>'catchup_state',''),
    now() + make_interval(secs => v_lease_seconds),
    coalesce(p_job->'details','{}'::jsonb)
  )
  on conflict (job_key) do nothing
  returning * into v_row;

  if found then
    return jsonb_build_object(
      'claimed',true,'duplicate',false,'run_id',v_row.run_id,
      'status',v_row.status,'attempt',v_row.attempt,'lease_until',v_row.lease_until
    );
  end if;

  select * into v_row
  from private.anevum_scheduler_runs
  where job_key = v_job_key
  for update;

  if v_row.status = 'RUNNING'
     and v_row.lease_until < now()
     and v_row.attempt < greatest(v_row.max_attempts,v_max_attempts) then
    update private.anevum_scheduler_runs
      set attempt = attempt + 1,
          max_attempts = greatest(max_attempts,v_max_attempts),
          started_at = now(),
          completed_at = null,
          lease_until = now() + make_interval(secs => v_lease_seconds),
          worker_identity = nullif(p_job->>'worker_identity',''),
          source_commit = nullif(p_job->>'source_commit',''),
          input_identity = nullif(p_job->>'input_identity',''),
          retry_state = 'lease_recovery',
          catchup_state = coalesce(nullif(p_job->>'catchup_state',''),catchup_state),
          updated_at = now()
    where job_key = v_job_key
    returning * into v_row;
    return jsonb_build_object(
      'claimed',true,'duplicate',false,'recovered',true,'run_id',v_row.run_id,
      'status',v_row.status,'attempt',v_row.attempt,'lease_until',v_row.lease_until
    );
  end if;

  if v_allow_retry
     and v_row.status = 'FAILED'
     and v_row.error_classification in (
       'transient_infrastructure',
       'dependency_unavailable',
       'evidence_unavailable'
     )
     and v_row.attempt < greatest(v_row.max_attempts,v_max_attempts) then
    update private.anevum_scheduler_runs
      set status = 'RUNNING',
          attempt = attempt + 1,
          max_attempts = greatest(max_attempts,v_max_attempts),
          started_at = now(),
          completed_at = null,
          lease_until = now() + make_interval(secs => v_lease_seconds),
          worker_identity = nullif(p_job->>'worker_identity',''),
          source_commit = nullif(p_job->>'source_commit',''),
          input_identity = nullif(p_job->>'input_identity',''),
          retry_state = 'retry',
          catchup_state = coalesce(nullif(p_job->>'catchup_state',''),catchup_state),
          updated_at = now()
    where job_key = v_job_key
    returning * into v_row;
    return jsonb_build_object(
      'claimed',true,'duplicate',false,'retry',true,'run_id',v_row.run_id,
      'status',v_row.status,'attempt',v_row.attempt,'lease_until',v_row.lease_until
    );
  end if;

  return jsonb_build_object(
    'claimed',false,
    'duplicate',v_row.status in ('SUCCEEDED','MISSED','SKIPPED','STALE'),
    'busy',v_row.status = 'RUNNING',
    'exhausted',v_row.status = 'FAILED' and v_row.attempt >= v_row.max_attempts,
    'run_id',v_row.run_id,'status',v_row.status,'attempt',v_row.attempt,
    'max_attempts',v_row.max_attempts,'lease_until',v_row.lease_until
  );
end;
$function$;

create or replace function private.anevum_scheduler_complete(p_result jsonb)
returns jsonb
language plpgsql
security definer
set search_path = private, pg_temp
as $function$
declare
  v_row private.anevum_scheduler_runs%rowtype;
  v_job_key text := nullif(btrim(p_result->>'job_key'),'');
  v_status text := nullif(btrim(p_result->>'status'),'');
begin
  if v_job_key is null or v_status not in ('SUCCEEDED','FAILED','MISSED','SKIPPED','STALE') then
    raise exception 'invalid_scheduler_completion';
  end if;

  update private.anevum_scheduler_runs
    set status = v_status,
        completed_at = now(),
        lease_until = null,
        output_identity = nullif(p_result->>'output_identity',''),
        error_classification = nullif(p_result->>'error_classification',''),
        error_summary = coalesce(p_result->'error_summary','{}'::jsonb),
        retry_state = nullif(p_result->>'retry_state',''),
        catchup_state = coalesce(nullif(p_result->>'catchup_state',''),catchup_state),
        slack_notification_status = nullif(p_result->>'slack_notification_status',''),
        details = details || coalesce(p_result->'details','{}'::jsonb),
        updated_at = now()
  where job_key = v_job_key
    and status in ('RUNNING','FAILED')
  returning * into v_row;

  if found then
    return jsonb_build_object(
      'updated',true,'idempotent',false,'run_id',v_row.run_id,
      'status',v_row.status,'attempt',v_row.attempt
    );
  end if;

  select * into v_row
  from private.anevum_scheduler_runs
  where job_key = v_job_key;

  if found and v_row.status = v_status then
    return jsonb_build_object('updated',false,'idempotent',true,'status',v_row.status);
  end if;
  raise exception 'scheduler_job_not_completable';
end;
$function$;

revoke execute on function private.anevum_scheduler_claim(jsonb) from public, anon, authenticated;
revoke execute on function private.anevum_scheduler_complete(jsonb) from public, anon, authenticated;

comment on table private.anevum_scheduler_runs is
  'Canonical ANEVUM recurring-work execution ledger. Idempotent job keys prevent duplicate scheduled work; rows carry no live trading authority.';
comment on function private.anevum_scheduler_claim(jsonb) is
  'Atomically claims or recovers a scheduler job lease without granting execution authority outside the orchestrator.';
comment on function private.anevum_scheduler_complete(jsonb) is
  'Completes a claimed scheduler job with durable terminal status and evidence metadata.';
