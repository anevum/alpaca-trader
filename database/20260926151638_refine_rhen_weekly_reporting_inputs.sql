CREATE OR REPLACE FUNCTION private.rhen_weekly_report_inputs(p_start date, p_end date)
 RETURNS jsonb
 LANGUAGE sql
 STABLE
 SET search_path TO 'private', 'pg_temp'
AS $function$
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
    coalesce(max(nullif(last_equity,0)), max(equity) filter(where first_rn=1)) as starting_equity,
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
        count(*) filter(where lower(side)='buy') as entry_fills,
        count(distinct broker_order_id) filter(where lower(side)='buy') as filled_opportunities
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
        count(*) filter(where event_type='reconciliation' and coalesce(payload->>'action','') in ('error','blocked')) as reconciliation_failure_events,
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
$function$
