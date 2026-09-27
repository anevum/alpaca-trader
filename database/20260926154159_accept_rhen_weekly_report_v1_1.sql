CREATE OR REPLACE FUNCTION private.project_canonical_weekly_report()
 RETURNS trigger
 LANGUAGE plpgsql
 SET search_path TO 'private', 'pg_temp'
AS $function$
declare
  v_report_id uuid;
  v_previous_report_id uuid;
  v_question jsonb;
  v_decision jsonb;
  v_linked_experiment_id uuid;
begin
  if new.event_type <> 'research_weekly_report'
     or coalesce(new.payload->>'report_version','') not in ('rhen-weekly-v1','rhen-weekly-v1.1') then
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
$function$
