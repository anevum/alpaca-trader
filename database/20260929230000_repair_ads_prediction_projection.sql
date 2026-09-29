-- Analytics-only projection repair for RHEN ADS prediction evidence.
-- Projects ADS v1/v2 state carried by order_intent candidate snapshots and
-- versioned candidate_prediction_backfill events into canonical research tables.
-- No execution, broker, sizing, risk, strategy, or promotion state is touched.

create or replace function private.project_ads002_entry_prediction_scores()
returns trigger
language plpgsql
set search_path to 'private', 'pg_temp'
as $function$
declare
  v_candidate jsonb;
  v_ads jsonb;
  v_v2 jsonb;
  v_challenger record;
  v_candidate_id bigint;
  v_candidate_key text;
  v_run_id uuid;
  v_strategy_version_id text;
  v_observed_at timestamptz;
  v_symbol text;
  v_signal_id uuid;
begin
  if new.event_type = 'order_intent' then
    v_candidate := coalesce(
      new.payload#>'{intent,payload,candidate_snapshot}',
      '{}'::jsonb
    );
  elsif new.event_type = 'candidate_prediction_backfill' then
    v_candidate := coalesce(new.payload, '{}'::jsonb);
  else
    return new;
  end if;

  if jsonb_typeof(v_candidate) <> 'object' then
    return new;
  end if;

  v_candidate_key := nullif(v_candidate->>'candidate_key', '');

  if nullif(v_candidate->>'candidate_id', '') is not null then
    select
      candidate_id,
      candidate_key,
      run_id,
      strategy_version_id,
      observed_at,
      symbol,
      signal_id
    into
      v_candidate_id,
      v_candidate_key,
      v_run_id,
      v_strategy_version_id,
      v_observed_at,
      v_symbol,
      v_signal_id
    from private.trading_candidate_evaluations
    where candidate_id = (v_candidate->>'candidate_id')::bigint
    limit 1;
  elsif v_candidate_key is not null then
    select
      candidate_id,
      candidate_key,
      run_id,
      strategy_version_id,
      observed_at,
      symbol,
      signal_id
    into
      v_candidate_id,
      v_candidate_key,
      v_run_id,
      v_strategy_version_id,
      v_observed_at,
      v_symbol,
      v_signal_id
    from private.trading_candidate_evaluations
    where candidate_key = v_candidate_key
    limit 1;
  end if;

  if v_candidate_id is null then
    return new;
  end if;

  v_ads := coalesce(v_candidate->'ads002', '{}'::jsonb);
  if jsonb_typeof(v_ads) = 'object'
     and nullif(v_ads->>'methodology_version', '') is not null then
    insert into private.trading_ads_shadow_scores (
      candidate_id,
      candidate_key,
      run_id,
      strategy_version_id,
      session,
      symbol,
      observed_at,
      attention_score,
      qualification_score,
      timing_score,
      exit_health_score,
      confidence_score,
      pretrade_composite,
      legacy_quality_score,
      feature_vector,
      score_components,
      source_completeness,
      attribution_state,
      methodology_version,
      forward_methodology_version,
      computed_at
    ) values (
      v_candidate_id,
      v_candidate_key,
      v_run_id,
      v_strategy_version_id,
      (v_observed_at at time zone 'America/New_York')::date,
      v_symbol,
      v_observed_at,
      nullif(v_ads#>>'{attention,score}', '')::numeric,
      nullif(v_ads#>>'{qualification,score}', '')::numeric,
      nullif(v_ads#>>'{timing,score}', '')::numeric,
      null,
      null,
      nullif(v_ads->>'pretrade_composite', '')::numeric,
      nullif(v_ads->>'legacy_quality_score', '')::numeric,
      coalesce(v_ads->'feature_vector', '{}'::jsonb),
      jsonb_build_object(
        'attention', v_ads->'attention',
        'qualification', v_ads->'qualification',
        'timing', v_ads->'timing'
      ),
      coalesce(v_ads->'source_completeness', '{}'::jsonb),
      case when v_signal_id is null then 'CANDIDATE_ONLY' else 'DIRECT_PARTIAL' end,
      v_ads->>'methodology_version',
      'candidate-forward-v2',
      now()
    )
    on conflict (candidate_id, methodology_version) do update
    set
      candidate_key = excluded.candidate_key,
      attention_score = excluded.attention_score,
      qualification_score = excluded.qualification_score,
      timing_score = excluded.timing_score,
      pretrade_composite = excluded.pretrade_composite,
      legacy_quality_score = excluded.legacy_quality_score,
      feature_vector = excluded.feature_vector,
      score_components = excluded.score_components,
      source_completeness = excluded.source_completeness,
      attribution_state = case
        when private.trading_ads_shadow_scores.attribution_state = 'DIRECT_COMPLETE'
          then private.trading_ads_shadow_scores.attribution_state
        else excluded.attribution_state
      end,
      forward_methodology_version = excluded.forward_methodology_version,
      computed_at = excluded.computed_at;
  end if;

  v_v2 := coalesce(v_candidate->'ads002_v2', '{}'::jsonb);
  if jsonb_typeof(v_v2) = 'object'
     and v_v2->>'methodology_version' = 'ads-shadow-v2'
     and jsonb_typeof(v_v2->'challengers') = 'object' then
    for v_challenger in
      select key as model_key, value as payload
      from jsonb_each(v_v2->'challengers')
    loop
      if not exists (
        select 1
        from private.trading_ads_model_registry
        where model_key = v_challenger.model_key
          and status = 'ACTIVE_SHADOW'
      ) then
        continue;
      end if;

      insert into private.trading_ads_challenger_scores (
        candidate_id,
        candidate_key,
        run_id,
        strategy_version_id,
        session,
        symbol,
        observed_at,
        model_key,
        methodology_version,
        feature_schema_version,
        normalization_version,
        attention_score,
        qualification_score,
        timing_score,
        raw_score,
        source_completeness,
        feature_vector,
        component_payload,
        forward_methodology_version,
        research_only,
        execution_authority,
        computed_at
      ) values (
        v_candidate_id,
        v_candidate_key,
        v_run_id,
        v_strategy_version_id,
        (v_observed_at at time zone 'America/New_York')::date,
        v_symbol,
        v_observed_at,
        v_challenger.model_key,
        'ads-shadow-v2',
        coalesce(v_v2->>'feature_schema_version', 'ads-features-v2'),
        coalesce(v_v2->>'normalization_version', 'hybrid-cross-sectional-v1'),
        nullif(v_v2#>>'{attention,score}', '')::numeric,
        nullif(v_v2#>>'{qualification,score}', '')::numeric,
        nullif(v_v2#>>'{timing,score}', '')::numeric,
        nullif(v_challenger.payload->>'s_raw', '')::numeric,
        coalesce(v_v2->'source_completeness', '{}'::jsonb),
        coalesce(v_v2->'raw_features', '{}'::jsonb),
        jsonb_build_object(
          'attention', v_v2->'attention',
          'qualification', v_v2->'qualification',
          'timing', v_v2->'timing',
          'component_ranks', v_v2->'component_ranks',
          'confidence', v_v2->'confidence'
        ),
        'candidate-forward-v2',
        true,
        false,
        now()
      )
      on conflict (candidate_id, model_key, methodology_version) do update
      set
        candidate_key = excluded.candidate_key,
        attention_score = excluded.attention_score,
        qualification_score = excluded.qualification_score,
        timing_score = excluded.timing_score,
        raw_score = excluded.raw_score,
        source_completeness = excluded.source_completeness,
        feature_vector = excluded.feature_vector,
        component_payload = excluded.component_payload,
        computed_at = excluded.computed_at;
    end loop;
  end if;

  return new;
end;
$function$;

drop trigger if exists trg_zzzzzz_ads002_entry_prediction_scores
on private.trading_events;

create trigger trg_zzzzzz_ads002_entry_prediction_scores
after insert on private.trading_events
for each row
execute function private.project_ads002_entry_prediction_scores();
