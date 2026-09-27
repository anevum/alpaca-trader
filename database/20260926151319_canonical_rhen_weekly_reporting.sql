
create table if not exists private.trading_weekly_reports (
  weekly_report_id uuid primary key default gen_random_uuid(),
  report_key text not null unique,
  report_version text not null,
  source_fingerprint text not null,
  period_start date not null,
  period_end date not null,
  completeness_state text not null check (completeness_state in ('COMPLETE','PARTIAL','INCOMPLETE')),
  expected_sessions date[] not null default '{}',
  included_sessions date[] not null default '{}',
  missing_sessions date[] not null default '{}',
  shortened_sessions jsonb not null default '[]'::jsonb,
  included_daily_report_ids uuid[] not null default '{}',
  strategy_versions text[] not null default '{}',
  run_ids uuid[] not null default '{}',
  runtime_provenance jsonb not null default '[]'::jsonb,
  generation_provenance jsonb not null default '{}'::jsonb,
  metrics jsonb not null default '{}'::jsonb,
  findings jsonb not null default '{}'::jsonb,
  research_questions jsonb not null default '[]'::jsonb,
  decisions jsonb not null default '[]'::jsonb,
  warnings jsonb not null default '[]'::jsonb,
  report_payload jsonb not null,
  data_cutoff timestamptz,
  generated_at timestamptz not null,
  source_event_id uuid unique references private.trading_events(event_id),
  supersedes_report_id uuid references private.trading_weekly_reports(weekly_report_id),
  created_at timestamptz not null default now(),
  check (period_end >= period_start)
);

create index if not exists trading_weekly_reports_period_idx
  on private.trading_weekly_reports(period_end desc, generated_at desc);
create index if not exists trading_weekly_reports_version_idx
  on private.trading_weekly_reports(report_version, period_end desc);

alter table private.trading_weekly_reports enable row level security;
revoke all on private.trading_weekly_reports from anon, authenticated;

create table if not exists private.trading_research_questions (
  question_record_id uuid primary key default gen_random_uuid(),
  research_question_id text not null,
  created_on date not null,
  source_weekly_report_id uuid not null references private.trading_weekly_reports(weekly_report_id),
  evidence_summary jsonb not null default '{}'::jsonb,
  sample_size integer not null default 0 check (sample_size >= 0),
  question text not null,
  why_it_matters text not null,
  required_data jsonb not null default '[]'::jsonb,
  status text not null check (status in ('OPEN','MONITOR','READY_FOR_RESEARCH','CLOSED','REJECTED')),
  linked_experiment_id uuid references private.trading_experiments(experiment_id),
  created_at timestamptz not null default now(),
  unique (source_weekly_report_id, research_question_id)
);

create index if not exists trading_research_questions_stable_id_idx
  on private.trading_research_questions(research_question_id, created_at desc);
create index if not exists trading_research_questions_status_idx
  on private.trading_research_questions(status, created_at desc);

alter table private.trading_research_questions enable row level security;
revoke all on private.trading_research_questions from anon, authenticated;

create table if not exists private.trading_weekly_decisions (
  weekly_decision_id uuid primary key default gen_random_uuid(),
  source_weekly_report_id uuid not null references private.trading_weekly_reports(weekly_report_id),
  decision_key text not null,
  evidence jsonb not null default '{}'::jsonb,
  interpretation text not null,
  decision text not null,
  scope text not null,
  production_behavior_changed boolean not null default false,
  decided_at timestamptz not null,
  created_at timestamptz not null default now(),
  unique (source_weekly_report_id, decision_key)
);

create index if not exists trading_weekly_decisions_report_idx
  on private.trading_weekly_decisions(source_weekly_report_id, decided_at);

alter table private.trading_weekly_decisions enable row level security;
revoke all on private.trading_weekly_decisions from anon, authenticated;

create or replace view private.rhen_latest_weekly_report
with (security_invoker = true)
as
select distinct on (period_start, period_end)
  weekly_report_id,
  report_key,
  report_version,
  period_start,
  period_end,
  completeness_state,
  generated_at,
  data_cutoff,
  report_payload
from private.trading_weekly_reports
order by period_start, period_end, generated_at desc, created_at desc;

revoke all on private.rhen_latest_weekly_report from anon, authenticated;

create or replace function private.project_canonical_weekly_report()
returns trigger
language plpgsql
set search_path = private, pg_temp
as $$
declare
  v_report_id uuid;
  v_previous_report_id uuid;
  v_question jsonb;
  v_decision jsonb;
  v_linked_experiment_id uuid;
begin
  if new.event_type <> 'research_weekly_report'
     or coalesce(new.payload->>'report_version','') <> 'rhen-weekly-v1' then
    return new;
  end if;

  if coalesce(new.payload->>'report_key','') = ''
     or coalesce(new.payload->>'source_fingerprint','') = ''
     or coalesce(new.payload->>'period_start','') = ''
     or coalesce(new.payload->>'period_end','') = '' then
    raise exception 'canonical weekly report payload is missing required identity fields';
  end if;

  select weekly_report_id into v_previous_report_id
  from private.trading_weekly_reports
  where period_start = (new.payload->>'period_start')::date
    and period_end = (new.payload->>'period_end')::date
    and report_key <> new.payload->>'report_key'
  order by generated_at desc, created_at desc
  limit 1;

  insert into private.trading_weekly_reports (
    report_key, report_version, source_fingerprint, period_start, period_end,
    completeness_state, expected_sessions, included_sessions, missing_sessions,
    shortened_sessions, included_daily_report_ids, strategy_versions, run_ids,
    runtime_provenance, generation_provenance, metrics, findings,
    research_questions, decisions, warnings, report_payload, data_cutoff,
    generated_at, source_event_id, supersedes_report_id
  ) values (
    new.payload->>'report_key',
    new.payload->>'report_version',
    new.payload->>'source_fingerprint',
    (new.payload->>'period_start')::date,
    (new.payload->>'period_end')::date,
    new.payload->>'completeness_state',
    array(select value::date from jsonb_array_elements_text(coalesce(new.payload->'expected_trading_sessions','[]'::jsonb))),
    array(select value::date from jsonb_array_elements_text(coalesce(new.payload->'included_trading_sessions','[]'::jsonb))),
    array(select value::date from jsonb_array_elements_text(coalesce(new.payload->'missing_trading_sessions','[]'::jsonb))),
    coalesce(new.payload->'shortened_sessions','[]'::jsonb),
    array(select value::uuid from jsonb_array_elements_text(coalesce(new.payload->'included_daily_report_ids','[]'::jsonb))),
    array(select value from jsonb_array_elements_text(coalesce(new.payload->'strategy_versions','[]'::jsonb))),
    array(select value::uuid from jsonb_array_elements_text(coalesce(new.payload->'trading_runs','[]'::jsonb))),
    coalesce(new.payload->'runtime_provenance','[]'::jsonb),
    coalesce(new.payload->'generation_provenance','{}'::jsonb),
    coalesce(new.payload->'metrics','{}'::jsonb),
    coalesce(new.payload->'evidence_stability','{}'::jsonb),
    coalesce(new.payload->'research_questions','[]'::jsonb),
    coalesce(new.payload->'weekly_decisions','[]'::jsonb),
    coalesce(new.payload->'warnings','[]'::jsonb),
    new.payload,
    nullif(new.payload->>'data_cutoff','')::timestamptz,
    coalesce(nullif(new.payload->>'generated_at','')::timestamptz,new.occurred_at),
    new.event_id,
    v_previous_report_id
  )
  on conflict (report_key) do nothing
  returning weekly_report_id into v_report_id;

  if v_report_id is null then
    select weekly_report_id into v_report_id
    from private.trading_weekly_reports
    where report_key = new.payload->>'report_key';
  end if;

  if v_report_id is null then
    raise exception 'canonical weekly report projection failed';
  end if;

  if jsonb_typeof(new.payload->'research_questions') = 'array' then
    for v_question in select value from jsonb_array_elements(new.payload->'research_questions')
    loop
      v_linked_experiment_id := null;
      if coalesce(v_question->>'linked_experiment','') <> '' then
        select experiment_id into v_linked_experiment_id
        from private.trading_experiments
        where experiment_key = v_question->>'linked_experiment'
        limit 1;
      end if;

      insert into private.trading_research_questions (
        research_question_id, created_on, source_weekly_report_id,
        evidence_summary, sample_size, question, why_it_matters,
        required_data, status, linked_experiment_id
      ) values (
        v_question->>'research_question_id',
        coalesce(nullif(new.payload->>'generated_at','')::timestamptz,new.occurred_at)::date,
        v_report_id,
        coalesce(v_question->'evidence_summary','{}'::jsonb),
        coalesce(nullif(v_question->>'sample_size','')::integer,0),
        v_question->>'question',
        v_question->>'why_it_matters',
        coalesce(v_question->'required_data','[]'::jsonb),
        v_question->>'status',
        v_linked_experiment_id
      )
      on conflict (source_weekly_report_id, research_question_id) do nothing;
    end loop;
  end if;

  if jsonb_typeof(new.payload->'weekly_decisions') = 'array' then
    for v_decision in select value from jsonb_array_elements(new.payload->'weekly_decisions')
    loop
      insert into private.trading_weekly_decisions (
        source_weekly_report_id, decision_key, evidence, interpretation,
        decision, scope, production_behavior_changed, decided_at
      ) values (
        v_report_id,
        v_decision->>'decision_key',
        coalesce(v_decision->'evidence','{}'::jsonb),
        v_decision->>'interpretation',
        v_decision->>'decision',
        v_decision->>'scope',
        coalesce((v_decision->>'production_behavior_changed')::boolean,false),
        coalesce(nullif(new.payload->>'generated_at','')::timestamptz,new.occurred_at)
      )
      on conflict (source_weekly_report_id, decision_key) do nothing;
    end loop;
  end if;

  return new;
end;
$$;

drop trigger if exists trg_project_canonical_weekly_report on private.trading_events;
create trigger trg_project_canonical_weekly_report
after insert on private.trading_events
for each row
when (new.event_type = 'research_weekly_report')
execute function private.project_canonical_weekly_report();

create or replace function private.rhen_weekly_report_inputs(p_start date, p_end date)
returns jsonb
language sql
stable
set search_path = private, pg_temp
as $$
with bounds as (
  select
    (p_start::timestamp at time zone 'America/New_York') as starts_at,
    ((p_end + 1)::timestamp at time zone 'America/New_York') as ends_at
),
daily_reports as (
  select e.event_id,e.occurred_at,e.received_at,e.strategy_version_id,e.payload
  from private.trading_events e
  where e.event_type='research_daily_report'
    and nullif(e.payload->>'session','')::date between p_start and p_end
),
candidate_rows as (
  select c.*,sc.data_status as cycle_data_status
  from private.trading_candidate_evaluations c
  left join private.trading_scan_cycles sc using(scan_cycle_id), bounds b
  where c.observed_at>=b.starts_at and c.observed_at<b.ends_at
),
rejection_reason_rows as (
  select
    (c.observed_at at time zone 'America/New_York')::date as session,
    rr.reason
  from candidate_rows c
  cross join lateral (
    select code::text as reason
    from unnest(coalesce(c.rejection_reason_codes,'{}'::text[])) code
    union all
    select value::text as reason
    from jsonb_array_elements_text(coalesce(c.rejection_reasons,'[]'::jsonb)) value
  ) rr
  where not c.qualified and coalesce(rr.reason,'')<>''
),
gate_rows as (
  select
    (c.observed_at at time zone 'America/New_York')::date as session,
    gate.key as gate,
    case when jsonb_typeof(gate.value)='boolean' then (gate.value::text)::boolean else null end as passed
  from candidate_rows c
  cross join lateral jsonb_each(coalesce(c.checks->'strategy','{}'::jsonb)) gate
  where jsonb_typeof(gate.value)='boolean'
),
order_rows as (
  select o.*,(coalesce(o.submitted_at,o.updated_at) at time zone 'America/New_York')::date as session
  from private.trading_orders o,bounds b
  where coalesce(o.submitted_at,o.updated_at)>=b.starts_at
    and coalesce(o.submitted_at,o.updated_at)<b.ends_at
),
intent_rows as (
  select i.*,(i.intended_at at time zone 'America/New_York')::date as session
  from private.trading_order_intents i,bounds b
  where i.intended_at>=b.starts_at and i.intended_at<b.ends_at
),
fill_rows as (
  select f.*,(f.filled_at at time zone 'America/New_York')::date as session
  from private.trading_fills f,bounds b
  where f.filled_at>=b.starts_at and f.filled_at<b.ends_at
),
position_rows as (
  select p.*
  from private.trading_positions p,bounds b
  where p.opened_at>=b.starts_at and p.opened_at<b.ends_at
),
snapshot_rows as (
  select s.*,(s.observed_at at time zone 'America/New_York')::date as session
  from private.trading_account_snapshots s,bounds b
  where s.observed_at>=b.starts_at and s.observed_at<b.ends_at and s.equity is not null
),
snapshot_ranked as (
  select s.*,
    row_number() over(partition by session order by observed_at) as first_rn,
    row_number() over(partition by session order by observed_at desc) as last_rn
  from snapshot_rows s
),
equity_by_session as (
  select
    session,
    max(coalesce(last_equity,equity)) filter(where first_rn=1) as starting_equity,
    max(equity) filter(where last_rn=1) as ending_equity
  from snapshot_ranked
  group by session
),
equity_points as (
  select observed_at,equity,
    max(equity) over(order by observed_at rows between unbounded preceding and current row) as peak_equity
  from snapshot_rows
),
incident_rows as (
  select i.*,(i.started_at at time zone 'America/New_York')::date as session
  from private.trading_incidents i,bounds b
  where i.started_at<b.ends_at
    and coalesce(i.ended_at,i.resolved_at,i.started_at)>=b.starts_at
),
period_events as (
  select e.*,(e.occurred_at at time zone 'America/New_York')::date as session
  from private.trading_events e,bounds b
  where e.occurred_at>=b.starts_at and e.occurred_at<b.ends_at
),
forward_rows as (
  select c.qualified,(c.observed_at at time zone 'America/New_York')::date as session,o.*
  from private.trading_candidate_forward_outcomes o
  join candidate_rows c using(candidate_id)
),
strategy_versions as (
  select distinct sv.*
  from private.trading_strategy_versions sv,bounds b
  where sv.activated_at<b.ends_at and coalesce(sv.retired_at,b.ends_at)>=b.starts_at
),
runs as (
  select distinct r.*
  from private.trading_runs r,bounds b
  where r.started_at<b.ends_at and coalesce(r.ended_at,b.ends_at)>=b.starts_at
),
runtime_instances as (
  select distinct ri.*
  from private.trading_runtime_instances ri,bounds b
  where ri.started_at<b.ends_at and coalesce(ri.stopped_at,b.ends_at)>=b.starts_at
)
select jsonb_build_object(
  'period',jsonb_build_object('start',p_start,'end',p_end),
  'daily_reports',coalesce((
    select jsonb_agg(jsonb_build_object(
      'event_id',event_id,
      'occurred_at',occurred_at,
      'strategy_version_id',strategy_version_id,
      'payload',payload
    ) order by payload->>'session')
    from daily_reports
  ),'[]'::jsonb),
  'earliest_daily_session',(
    select min(nullif(payload->>'session','')::date)
    from private.trading_events where event_type='research_daily_report'
  ),
  'strategy_versions',coalesce((
    select jsonb_agg(jsonb_build_object(
      'version_id',version_id,'strategy_name',strategy_name,'status',status,
      'environment',environment,'activated_at',activated_at,'retired_at',retired_at,
      'git_commit',git_commit,'deployment_id',deployment_id
    ) order by activated_at) from strategy_versions
  ),'[]'::jsonb),
  'runs',coalesce((
    select jsonb_agg(jsonb_build_object(
      'run_id',run_id,'run_key',run_key,'strategy_version_id',strategy_version_id,
      'environment',environment,'status',status,'started_at',started_at,'ended_at',ended_at,
      'git_commit',git_commit,'deployment_id',deployment_id
    ) order by started_at) from runs
  ),'[]'::jsonb),
  'runtime_instances',coalesce((
    select jsonb_agg(jsonb_build_object(
      'runtime_instance_id',runtime_instance_id,'run_id',run_id,
      'strategy_version_id',strategy_version_id,'deployment_id',deployment_id,
      'build_id',build_id,'git_commit',git_commit,'repository',repository,
      'branch',branch,'service_name',service_name,'environment_name',environment_name,
      'system_version',system_version,'started_at',started_at,'stopped_at',stopped_at
    ) order by started_at) from runtime_instances
  ),'[]'::jsonb),
  'account_equity_by_session',coalesce((
    select jsonb_agg(jsonb_build_object(
      'session',session,'starting_equity',starting_equity,'ending_equity',ending_equity
    ) order by session) from equity_by_session
  ),'[]'::jsonb),
  'account_weekly_drawdown',jsonb_build_object(
    'max_drawdown_pct',(
      select min(case when peak_equity>0 then (equity-peak_equity)/peak_equity else null end)
      from equity_points
    )
  ),
  'candidate_by_session',coalesce((
    select jsonb_agg(to_jsonb(x) order by session)
    from (
      select
        (observed_at at time zone 'America/New_York')::date as session,
        count(*) as evaluated,
        count(*) filter(where qualified) as qualified,
        count(*) filter(where not qualified) as rejected,
        count(*) filter(where signal_id is not null) as signals,
        count(*) filter(where cycle_data_status='partial_backfill') as partial_backfill
      from candidate_rows group by 1
    ) x
  ),'[]'::jsonb),
  'rejection_reasons',coalesce((
    select jsonb_agg(to_jsonb(x) order by session,reason)
    from (
      select session,reason,count(*) as count
      from rejection_reason_rows group by session,reason
    ) x
  ),'[]'::jsonb),
  'gate_rates',coalesce((
    select jsonb_agg(to_jsonb(x) order by session,gate)
    from (
      select session,gate,
        count(*) filter(where passed is not null) as total,
        count(*) filter(where passed) as passed,
        count(*) filter(where passed is false) as failed
      from gate_rows group by session,gate
    ) x
  ),'[]'::jsonb),
  'orders_by_session',coalesce((
    select jsonb_agg(to_jsonb(x) order by session)
    from (select session,count(*) as orders from order_rows group by session) x
  ),'[]'::jsonb),
  'order_intents_by_session',coalesce((
    select jsonb_agg(to_jsonb(x) order by session)
    from (
      select session,count(*) as order_intents,
        count(*) filter(where lower(side)='buy') as entry_intents
      from intent_rows group by session
    ) x
  ),'[]'::jsonb),
  'fills_by_session',coalesce((
    select jsonb_agg(to_jsonb(x) order by session)
    from (
      select session,count(*) as fills,
        count(*) filter(where lower(side)='buy') as entry_fills
      from fill_rows group by session
    ) x
  ),'[]'::jsonb),
  'positions',coalesce((
    select jsonb_agg(jsonb_build_object(
      'position_id',position_id,'run_id',run_id,'strategy_version_id',strategy_version_id,
      'symbol',symbol,'status',status,'opened_at',opened_at,'closed_at',closed_at,
      'realized_pnl',realized_pnl,'realized_return',realized_return,
      'max_favorable_excursion',max_favorable_excursion,
      'max_adverse_excursion',max_adverse_excursion,
      'holding_duration_ms',holding_duration_ms,'exit_reason',exit_reason,
      'target_touched',target_touched,'stop_touched',stop_touched,'entry_reason',entry_reason
    ) order by opened_at) from position_rows
  ),'[]'::jsonb),
  'incidents',coalesce((
    select jsonb_agg(jsonb_build_object(
      'incident_id',incident_id,'run_id',run_id,'session',session,'severity',severity,
      'incident_type',incident_type,'started_at',started_at,'ended_at',ended_at,
      'resolved_at',resolved_at,'message',message,'resolution',resolution,'details',details
    ) order by started_at) from incident_rows
  ),'[]'::jsonb),
  'operational_by_session',coalesce((
    select jsonb_agg(to_jsonb(x) order by session)
    from (
      select session,
        count(*) filter(where event_type='runtime_start') as runtime_starts,
        count(*) filter(where event_type='runtime_stop') as runtime_stops,
        count(*) filter(where event_type='runtime_error') as runtime_errors,
        count(*) filter(where event_type='research_reporting' and coalesce(payload->>'action','') in ('warning','error')) as report_failures,
        count(*) filter(where event_type='reconciliation' and coalesce(payload->>'action','') in ('error','blocked')) as reconciliation_failures,
        count(*) filter(where event_type='execution' and coalesce(payload->>'action','') in ('error','blocked','failed')) as execution_failures,
        count(*) filter(where event_type in ('scan','decision_cycle') and (
          lower(coalesce(payload->>'reason','')) like '%stale%'
          or lower(coalesce(payload->>'data_status',''))='degraded'
        )) as stale_market_data_events
      from period_events group by session
    ) x
  ),'[]'::jsonb),
  'forward_outcomes',coalesce((
    select jsonb_agg(to_jsonb(x) order by session)
    from (
      select session,
        count(*) filter(where status='complete') as complete,
        count(*) filter(where not qualified and status='complete' and forward_return>0) as rejected_favorable,
        count(*) filter(where not qualified and status='complete' and forward_return<=0) as rejected_failed,
        count(*) filter(where qualified and status='complete' and forward_return>0) as qualified_succeeded,
        count(*) filter(where qualified and status='complete' and forward_return<=0) as qualified_failed
      from forward_rows group by session
    ) x
  ),'[]'::jsonb),
  'live_offline',coalesce((
    select jsonb_agg(jsonb_build_object(
      'event_id',event_id,'session',session,'event_type',event_type,
      'occurred_at',occurred_at,'payload',payload
    ) order by occurred_at)
    from period_events
    where event_type in ('live_offline_comparison','live_vs_offline_comparison')
  ),'[]'::jsonb),
  'duplicate_checks',jsonb_build_object(
    'scan_cycle_key',(
      select count(*) from (
        select cycle_key from private.trading_scan_cycles
        where cycle_key is not null group by cycle_key having count(*)>1
      ) d
    ),
    'candidate_cycle_symbol',(
      select count(*) from (
        select scan_cycle_id,symbol from private.trading_candidate_evaluations
        group by scan_cycle_id,symbol having count(*)>1
      ) d
    ),
    'decision_event_key',(
      select count(*) from (
        select event_key from private.trading_events
        where event_type='decision_cycle' and event_key is not null
        group by event_key having count(*)>1
      ) d
    )
  ),
  'canonical_period_summary',private.rhen_reporting_inputs(p_start,p_end),
  'data_cutoff',greatest(
    (select max(received_at) from period_events),
    (select max(received_at) from daily_reports)
  ),
  'warnings','[]'::jsonb
);
$$;

comment on table private.trading_weekly_reports is
  'Immutable/versioned canonical RHEN weekly operating and research reports.';
comment on table private.trading_research_questions is
  'Durable post-event research-question queue. Rows never authorize live strategy changes.';
comment on table private.trading_weekly_decisions is
  'Durable weekly operating/research decisions. production_behavior_changed must be explicit.';
comment on function private.rhen_weekly_report_inputs(date,date) is
  'Read-only canonical inputs for weekly reports; daily reports are authoritative for daily aggregates.';
