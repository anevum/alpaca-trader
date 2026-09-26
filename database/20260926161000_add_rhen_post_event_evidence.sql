-- RHEN post-event evidence: descriptive forward outcomes and deterministic replay evidence.
-- Analytics only. No live qualification, execution, sizing, risk, or promotion path reads these objects.

create or replace function private.project_candidate_forward_outcome()
returns trigger
language plpgsql
set search_path = private, pg_temp
as $$
declare
  v_candidate_id bigint;
  v_methodology text;
  v_total integer;
  v_complete integer;
  v_payload jsonb;
begin
  if new.event_type <> 'candidate_forward_outcome' then
    return new;
  end if;

  v_candidate_id := nullif(new.payload->>'candidate_id','')::bigint;
  v_methodology := nullif(new.payload->>'methodology_version','');
  if v_candidate_id is null or v_methodology is null then
    raise exception 'candidate_forward_outcome requires candidate_id and methodology_version';
  end if;

  insert into private.trading_candidate_forward_outcomes (
    candidate_id,
    horizon_minutes,
    observation_end_at,
    reference_price,
    forward_price,
    forward_return,
    max_favorable_return,
    max_adverse_return,
    provider,
    bar_interval,
    methodology_version,
    status,
    computed_at,
    details
  ) values (
    v_candidate_id,
    nullif(new.payload->>'horizon_minutes','')::integer,
    nullif(new.payload->>'observation_end_at','')::timestamptz,
    nullif(new.payload->>'reference_price','')::numeric,
    nullif(new.payload->>'forward_price','')::numeric,
    nullif(new.payload->>'forward_return','')::numeric,
    nullif(new.payload->>'max_favorable_return','')::numeric,
    nullif(new.payload->>'max_adverse_return','')::numeric,
    nullif(new.payload->>'provider',''),
    nullif(new.payload->>'bar_interval',''),
    v_methodology,
    new.payload->>'status',
    coalesce(nullif(new.payload->>'computed_at','')::timestamptz,new.occurred_at),
    coalesce(new.payload->'details','{}'::jsonb)
      || jsonb_build_object('source_event_id',new.event_id)
  )
  on conflict (candidate_id,horizon_minutes,methodology_version) do update
  set observation_end_at=excluded.observation_end_at,
      reference_price=coalesce(excluded.reference_price,private.trading_candidate_forward_outcomes.reference_price),
      forward_price=excluded.forward_price,
      forward_return=excluded.forward_return,
      max_favorable_return=excluded.max_favorable_return,
      max_adverse_return=excluded.max_adverse_return,
      provider=coalesce(excluded.provider,private.trading_candidate_forward_outcomes.provider),
      bar_interval=coalesce(excluded.bar_interval,private.trading_candidate_forward_outcomes.bar_interval),
      status=excluded.status,
      computed_at=excluded.computed_at,
      details=private.trading_candidate_forward_outcomes.details || excluded.details;

  select count(*),count(*) filter(where status='complete')
  into v_total,v_complete
  from private.trading_candidate_forward_outcomes
  where candidate_id=v_candidate_id and methodology_version=v_methodology;

  select coalesce(
    jsonb_object_agg(
      horizon_minutes::text,
      jsonb_build_object(
        'status',status,
        'observation_end_at',observation_end_at,
        'forward_return',forward_return,
        'max_favorable_return',max_favorable_return,
        'max_adverse_return',max_adverse_return,
        'methodology_version',methodology_version
      )
      order by horizon_minutes
    ),
    '{}'::jsonb
  )
  into v_payload
  from private.trading_candidate_forward_outcomes
  where candidate_id=v_candidate_id and methodology_version=v_methodology;

  update private.trading_candidate_evaluations
  set forward_outcomes=v_payload,
      forward_outcomes_status=case
        when v_total < 4 then 'pending'
        when v_complete = 4 then 'complete'
        else 'incomplete'
      end,
      forward_enriched_at=now()
  where candidate_id=v_candidate_id;

  return new;
end;
$$;

drop trigger if exists trg_project_candidate_forward_outcome on private.trading_events;
create trigger trg_project_candidate_forward_outcome
after insert on private.trading_events
for each row
when (new.event_type='candidate_forward_outcome')
execute function private.project_candidate_forward_outcome();

create or replace function private.link_candidate_signal_from_cycle()
returns trigger
language plpgsql
set search_path = private, pg_temp
as $$
declare
  v_cycle_key text;
  v_signal_id uuid;
begin
  select cycle_key into v_cycle_key
  from private.trading_scan_cycles
  where scan_cycle_id=new.scan_cycle_id;

  if coalesce(v_cycle_key,'')='' then
    return new;
  end if;

  select s.signal_id into v_signal_id
  from private.trading_signals s
  where upper(s.symbol)=upper(new.symbol)
    and s.payload->>'cycle_key'=v_cycle_key
  order by s.signal_at desc
  limit 1;

  if v_signal_id is null then
    return new;
  end if;

  update private.trading_candidate_evaluations
  set signal_id=coalesce(signal_id,v_signal_id)
  where candidate_id=new.candidate_id;

  update private.trading_signals
  set candidate_id=coalesce(candidate_id,new.candidate_id)
  where signal_id=v_signal_id;

  return new;
end;
$$;

drop trigger if exists trg_link_candidate_signal_from_cycle on private.trading_candidate_evaluations;
create trigger trg_link_candidate_signal_from_cycle
after insert on private.trading_candidate_evaluations
for each row
execute function private.link_candidate_signal_from_cycle();

create or replace function private.rhen_post_event_evidence_inputs(p_start date,p_end date)
returns jsonb
language sql
stable
set search_path = private, pg_temp
as $$
with bounds as (
  select
    (p_start::timestamp at time zone 'America/New_York') as starts_at,
    ((p_end+1)::timestamp at time zone 'America/New_York') as ends_at
),
candidates as (
  select c.*
  from private.trading_candidate_evaluations c,bounds b
  where c.observed_at>=b.starts_at and c.observed_at<b.ends_at
),
forward_rows as (
  select
    (c.observed_at at time zone 'America/New_York')::date as session,
    c.qualified,
    o.*
  from private.trading_candidate_forward_outcomes o
  join candidates c using(candidate_id)
),
comparison_events as (
  select distinct on (coalesce(e.payload->>'comparison_key',e.event_key))
    e.event_id,e.event_key,e.occurred_at,e.received_at,e.payload
  from private.trading_events e
  where e.event_type in ('live_offline_comparison','live_vs_offline_comparison')
    and nullif(e.payload->>'session','')::date between p_start and p_end
  order by coalesce(e.payload->>'comparison_key',e.event_key),e.occurred_at desc,e.received_at desc
),
comparison_categories as (
  select
    nullif(payload->>'session','')::date as session,
    coalesce(payload->>'mismatch_category','UNKNOWN') as category,
    count(*) as count
  from comparison_events
  group by 1,2
),
comparison_category_json as (
  select session,jsonb_object_agg(category,count order by category) as categories
  from comparison_categories
  group by session
),
comparison_summary as (
  select
    nullif(e.payload->>'session','')::date as session,
    count(*) as total,
    count(*) filter(where e.payload->>'match_state'='MATCH') as matches,
    count(*) filter(where e.payload->>'match_state'='MISMATCH') as mismatches,
    count(*) filter(where e.payload->>'match_state'='UNRECONSTRUCTABLE') as unreconstructable
  from comparison_events e
  group by 1
)
select jsonb_build_object(
  'forward_outcomes_by_horizon',coalesce((
    select jsonb_agg(to_jsonb(x) order by session,horizon_minutes)
    from (
      select
        session,
        horizon_minutes,
        count(*) as observations,
        count(*) filter(where status='complete') as complete,
        count(*) filter(where status='insufficient_future_data') as incomplete,
        count(*) filter(where status='error') as errors,
        count(*) filter(where not qualified and status='complete' and forward_return>0) as rejected_favorable,
        count(*) filter(where not qualified and status='complete' and forward_return<=0) as rejected_unfavorable,
        count(*) filter(where qualified and status='complete' and forward_return>0) as qualified_favorable,
        count(*) filter(where qualified and status='complete' and forward_return<=0) as qualified_unfavorable,
        avg(max_favorable_return) filter(where not qualified and status='complete') as rejected_avg_mfe,
        avg(max_adverse_return) filter(where not qualified and status='complete') as rejected_avg_mae,
        avg(max_favorable_return) filter(where qualified and status='complete') as qualified_avg_mfe,
        avg(max_adverse_return) filter(where qualified and status='complete') as qualified_avg_mae
      from forward_rows
      group by session,horizon_minutes
    ) x
  ),'[]'::jsonb),
  'forward_outcome_status',jsonb_build_object(
    'candidate_count',(select count(*) from candidates),
    'eligible_reference_price_count',(select count(*) from candidates where decision_reference_price is not null and decision_reference_price>0),
    'complete_rows',(select count(*) from forward_rows where status='complete'),
    'incomplete_rows',(select count(*) from forward_rows where status='insufficient_future_data'),
    'error_rows',(select count(*) from forward_rows where status='error'),
    'methodologies',coalesce((select jsonb_agg(distinct methodology_version) from forward_rows),'[]'::jsonb)
  ),
  'live_offline_summary',coalesce((
    select jsonb_agg(
      jsonb_build_object(
        'session',s.session,
        'total',s.total,
        'matches',s.matches,
        'mismatches',s.mismatches,
        'unreconstructable',s.unreconstructable,
        'match_rate',case when (s.total-s.unreconstructable)>0 then s.matches::numeric/(s.total-s.unreconstructable) else null end,
        'mismatch_categories',coalesce(c.categories,'{}'::jsonb)
      )
      order by s.session
    )
    from comparison_summary s
    left join comparison_category_json c using(session)
  ),'[]'::jsonb),
  'live_offline_details',coalesce((
    select jsonb_agg(jsonb_build_object(
      'event_id',event_id,
      'event_key',event_key,
      'occurred_at',occurred_at,
      'payload',payload
    ) order by payload->>'session',payload->>'symbol')
    from comparison_events
  ),'[]'::jsonb)
);
$$;

revoke all on function private.rhen_post_event_evidence_inputs(date,date)
from public,anon,authenticated;

comment on function private.rhen_post_event_evidence_inputs(date,date)
is 'Private post-event analytics inputs. Forward outcomes and replay comparisons are descriptive only and never feed live trading.';

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
     or coalesce(new.payload->>'report_version','') not in ('rhen-weekly-v1','rhen-weekly-v1.1','rhen-weekly-v1.2') then
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
  where period_start=(new.payload->>'period_start')::date
    and period_end=(new.payload->>'period_end')::date
    and report_key<>new.payload->>'report_key'
  order by generated_at desc,created_at desc
  limit 1;

  insert into private.trading_weekly_reports (
    report_key,report_version,source_fingerprint,period_start,period_end,
    completeness_state,expected_sessions,included_sessions,missing_sessions,
    shortened_sessions,included_daily_report_ids,strategy_versions,run_ids,
    runtime_provenance,generation_provenance,metrics,findings,
    research_questions,decisions,warnings,report_payload,data_cutoff,
    generated_at,source_event_id,supersedes_report_id
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
    where report_key=new.payload->>'report_key';
  end if;

  if v_report_id is null then
    raise exception 'canonical weekly report projection failed';
  end if;

  if jsonb_typeof(new.payload->'research_questions')='array' then
    for v_question in select value from jsonb_array_elements(new.payload->'research_questions')
    loop
      v_linked_experiment_id:=null;
      if coalesce(v_question->>'linked_experiment','')<>'' then
        select experiment_id into v_linked_experiment_id
        from private.trading_experiments
        where experiment_key=v_question->>'linked_experiment'
        limit 1;
      end if;

      insert into private.trading_research_questions (
        research_question_id,created_on,source_weekly_report_id,
        evidence_summary,sample_size,question,why_it_matters,
        required_data,status,linked_experiment_id
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
      on conflict (source_weekly_report_id,research_question_id) do nothing;
    end loop;
  end if;

  if jsonb_typeof(new.payload->'weekly_decisions')='array' then
    for v_decision in select value from jsonb_array_elements(new.payload->'weekly_decisions')
    loop
      insert into private.trading_weekly_decisions (
        source_weekly_report_id,decision_key,evidence,interpretation,
        decision,scope,production_behavior_changed,decided_at
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
      on conflict (source_weekly_report_id,decision_key) do nothing;
    end loop;
  end if;

  return new;
end;
$$;
