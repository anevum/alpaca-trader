
alter table private.trading_scan_cycles
  add column if not exists cycle_key text,
  add column if not exists strategy_version_id text references private.trading_strategy_versions(version_id),
  add column if not exists cycle_started_at timestamptz,
  add column if not exists cycle_ended_at timestamptz,
  add column if not exists runtime_instance_id text,
  add column if not exists deployment_id text,
  add column if not exists git_commit text,
  add column if not exists candidate_count integer,
  add column if not exists qualified_count integer,
  add column if not exists rejected_count integer,
  add column if not exists cycle_outcome text,
  add column if not exists data_status text,
  add column if not exists degraded boolean not null default false,
  add column if not exists error_text text;

create unique index if not exists trading_scan_cycles_cycle_key_key
  on private.trading_scan_cycles(cycle_key)
  where cycle_key is not null;

alter table private.trading_candidate_evaluations
  add column if not exists candidate_state text,
  add column if not exists rejection_reasons jsonb not null default '[]'::jsonb,
  add column if not exists data_quality_state text,
  add column if not exists decision_reference_price numeric,
  add column if not exists decision_bid numeric,
  add column if not exists decision_ask numeric,
  add column if not exists decision_midpoint numeric,
  add column if not exists observed_spread numeric,
  add column if not exists quote_observed_at timestamptz,
  add column if not exists forward_outcomes jsonb not null default '{}'::jsonb,
  add column if not exists forward_outcomes_status text not null default 'pending',
  add column if not exists forward_enriched_at timestamptz,
  add column if not exists research_attribution jsonb not null default '{}'::jsonb;

create index if not exists trading_candidates_forward_status_idx
  on private.trading_candidate_evaluations(forward_outcomes_status, observed_at);

alter table private.trading_orders
  add column if not exists decision_at timestamptz,
  add column if not exists decision_reference_price numeric,
  add column if not exists decision_bid numeric,
  add column if not exists decision_ask numeric,
  add column if not exists decision_midpoint numeric,
  add column if not exists observed_spread numeric,
  add column if not exists order_intent_at timestamptz,
  add column if not exists broker_acknowledged_at timestamptz,
  add column if not exists decision_to_submit_ms bigint,
  add column if not exists submit_to_ack_ms bigint,
  add column if not exists ack_to_fill_ms bigint,
  add column if not exists decision_midpoint_to_fill_bps numeric,
  add column if not exists reference_price_to_fill_bps numeric;

alter table private.trading_positions
  add column if not exists entry_reference_price numeric,
  add column if not exists entry_fill_price numeric,
  add column if not exists peak_favorable_price numeric,
  add column if not exists peak_favorable_at timestamptz,
  add column if not exists peak_adverse_price numeric,
  add column if not exists peak_adverse_at timestamptz,
  add column if not exists time_to_mfe_ms bigint,
  add column if not exists time_to_mae_ms bigint,
  add column if not exists holding_duration_ms bigint,
  add column if not exists excursion_source text,
  add column if not exists excursion_updated_at timestamptz;

alter table private.trading_experiments
  add column if not exists research_family text,
  add column if not exists manifest_version text,
  add column if not exists manifest_hash text,
  add column if not exists manifest_hash_method text,
  add column if not exists code_commit text,
  add column if not exists data_source text,
  add column if not exists data_feed text,
  add column if not exists timeframe text,
  add column if not exists rejection_criteria jsonb not null default '{}'::jsonb,
  add column if not exists stage_reached text,
  add column if not exists survivor_state text,
  add column if not exists report_reference text,
  add column if not exists completed_at timestamptz,
  add column if not exists capital_scaling_authorized boolean not null default false;

create or replace function private.project_decision_cycle_event()
returns trigger
language plpgsql
set search_path to 'private', 'pg_temp'
as $$
declare
  v_cycle_key text;
  v_scan_cycle_id bigint;
  v_candidate jsonb;
  v_candidate_id bigint;
  v_signal_id uuid;
  v_qualified boolean;
begin
  if new.event_type <> 'decision_cycle' then
    return new;
  end if;

  v_cycle_key := nullif(new.payload->>'cycle_key', '');
  if v_cycle_key is null then
    raise exception 'decision_cycle event requires cycle_key';
  end if;

  insert into private.trading_scan_cycles (
    cycle_key,
    run_id,
    strategy_version_id,
    observed_at,
    cycle_started_at,
    cycle_ended_at,
    runtime_instance_id,
    deployment_id,
    git_commit,
    market_is_open,
    symbol_count,
    candidate_count,
    qualified_count,
    rejected_count,
    cycle_outcome,
    data_status,
    degraded,
    error_text,
    cycle_duration_ms,
    metadata
  ) values (
    v_cycle_key,
    new.run_id,
    new.strategy_version_id,
    coalesce(nullif(new.payload->>'cycle_ended_at','')::timestamptz, new.occurred_at),
    nullif(new.payload->>'cycle_started_at','')::timestamptz,
    nullif(new.payload->>'cycle_ended_at','')::timestamptz,
    nullif(new.payload#>>'{runtime,runtime_instance_id}',''),
    nullif(new.payload#>>'{runtime,deployment_id}',''),
    nullif(new.payload#>>'{runtime,git_commit}',''),
    case
      when new.payload ? 'market_is_open' and new.payload->'market_is_open' <> 'null'::jsonb
      then (new.payload->>'market_is_open')::boolean
      else null
    end,
    coalesce((new.payload->>'active_universe_size')::integer, 0),
    coalesce((new.payload->>'candidate_count')::integer, 0),
    coalesce((new.payload->>'qualified_count')::integer, 0),
    coalesce((new.payload->>'rejected_count')::integer, 0),
    nullif(new.payload->>'cycle_outcome',''),
    nullif(new.payload->>'data_status',''),
    coalesce((new.payload->>'degraded')::boolean, false),
    nullif(new.payload->>'error',''),
    nullif(new.payload->>'cycle_duration_ms','')::integer,
    new.payload - 'candidates'
  )
  on conflict (cycle_key) where cycle_key is not null do update
  set
    observed_at = excluded.observed_at,
    cycle_started_at = coalesce(private.trading_scan_cycles.cycle_started_at, excluded.cycle_started_at),
    cycle_ended_at = coalesce(excluded.cycle_ended_at, private.trading_scan_cycles.cycle_ended_at),
    runtime_instance_id = coalesce(excluded.runtime_instance_id, private.trading_scan_cycles.runtime_instance_id),
    deployment_id = coalesce(excluded.deployment_id, private.trading_scan_cycles.deployment_id),
    git_commit = coalesce(excluded.git_commit, private.trading_scan_cycles.git_commit),
    market_is_open = coalesce(excluded.market_is_open, private.trading_scan_cycles.market_is_open),
    symbol_count = excluded.symbol_count,
    candidate_count = excluded.candidate_count,
    qualified_count = excluded.qualified_count,
    rejected_count = excluded.rejected_count,
    cycle_outcome = coalesce(excluded.cycle_outcome, private.trading_scan_cycles.cycle_outcome),
    data_status = coalesce(excluded.data_status, private.trading_scan_cycles.data_status),
    degraded = excluded.degraded,
    error_text = coalesce(excluded.error_text, private.trading_scan_cycles.error_text),
    cycle_duration_ms = coalesce(excluded.cycle_duration_ms, private.trading_scan_cycles.cycle_duration_ms),
    metadata = private.trading_scan_cycles.metadata || excluded.metadata
  returning scan_cycle_id into v_scan_cycle_id;

  if jsonb_typeof(new.payload->'candidates') = 'array' then
    for v_candidate in
      select value from jsonb_array_elements(new.payload->'candidates')
    loop
      v_qualified := coalesce((v_candidate->>'qualified')::boolean, false);

      insert into private.trading_candidate_evaluations (
        scan_cycle_id,
        run_id,
        strategy_version_id,
        symbol,
        observed_at,
        action,
        qualified,
        reason,
        candidate_rank,
        candidate_state,
        rejection_reasons,
        data_quality_state,
        decision_reference_price,
        decision_bid,
        decision_ask,
        decision_midpoint,
        observed_spread,
        quote_observed_at,
        features,
        checks,
        forward_outcomes,
        forward_outcomes_status,
        research_attribution
      ) values (
        v_scan_cycle_id,
        new.run_id,
        new.strategy_version_id,
        upper(v_candidate->>'symbol'),
        coalesce(nullif(v_candidate->>'observed_at','')::timestamptz, new.occurred_at),
        coalesce(nullif(v_candidate->>'action',''), 'hold'),
        v_qualified,
        nullif(v_candidate->>'reason',''),
        nullif(v_candidate->>'candidate_rank','')::integer,
        coalesce(nullif(v_candidate->>'candidate_state',''), case when v_qualified then 'qualified' else 'rejected' end),
        coalesce(v_candidate->'rejection_reasons', '[]'::jsonb),
        nullif(v_candidate->>'data_quality_state',''),
        nullif(v_candidate->>'decision_reference_price','')::numeric,
        nullif(v_candidate#>>'{quote,bid}','')::numeric,
        nullif(v_candidate#>>'{quote,ask}','')::numeric,
        nullif(v_candidate#>>'{quote,midpoint}','')::numeric,
        nullif(v_candidate#>>'{quote,spread_pct}','')::numeric,
        nullif(v_candidate#>>'{quote,observed_at}','')::timestamptz,
        coalesce(v_candidate->'features', '{}'::jsonb),
        coalesce(v_candidate->'checks', '{}'::jsonb),
        coalesce(v_candidate->'forward_outcomes', '{}'::jsonb),
        coalesce(nullif(v_candidate->>'forward_outcomes_status',''), 'pending'),
        coalesce(v_candidate->'research_attribution', '{}'::jsonb)
      )
      on conflict (scan_cycle_id, symbol) do update
      set
        observed_at = excluded.observed_at,
        action = excluded.action,
        qualified = excluded.qualified,
        reason = excluded.reason,
        candidate_rank = coalesce(excluded.candidate_rank, private.trading_candidate_evaluations.candidate_rank),
        candidate_state = excluded.candidate_state,
        rejection_reasons = excluded.rejection_reasons,
        data_quality_state = excluded.data_quality_state,
        decision_reference_price = excluded.decision_reference_price,
        decision_bid = excluded.decision_bid,
        decision_ask = excluded.decision_ask,
        decision_midpoint = excluded.decision_midpoint,
        observed_spread = excluded.observed_spread,
        quote_observed_at = excluded.quote_observed_at,
        features = private.trading_candidate_evaluations.features || excluded.features,
        checks = private.trading_candidate_evaluations.checks || excluded.checks,
        research_attribution = private.trading_candidate_evaluations.research_attribution || excluded.research_attribution
      returning candidate_id into v_candidate_id;

      if v_qualified and coalesce(v_candidate->>'signal_id','') <> '' then
        v_signal_id := (v_candidate->>'signal_id')::uuid;
        insert into private.trading_signals (
          signal_id,
          candidate_id,
          run_id,
          strategy_version_id,
          symbol,
          side,
          signal_at,
          reference_price,
          stop_price,
          target_price,
          payload
        ) values (
          v_signal_id,
          v_candidate_id,
          new.run_id,
          new.strategy_version_id,
          upper(v_candidate->>'symbol'),
          'buy',
          coalesce(nullif(v_candidate->>'observed_at','')::timestamptz, new.occurred_at),
          nullif(v_candidate->>'decision_reference_price','')::numeric,
          nullif(v_candidate->>'stop_price','')::numeric,
          nullif(v_candidate->>'target_price','')::numeric,
          jsonb_build_object(
            'cycle_key', v_cycle_key,
            'candidate_state', coalesce(v_candidate->>'candidate_state','qualified'),
            'reason', v_candidate->>'reason',
            'telemetry_source', 'decision_cycle'
          )
        )
        on conflict (signal_id) do update
        set
          candidate_id = coalesce(private.trading_signals.candidate_id, excluded.candidate_id),
          payload = private.trading_signals.payload || excluded.payload;
      end if;
    end loop;
  end if;

  return new;
end;
$$;

drop trigger if exists trg_project_decision_cycle on private.trading_events;
create trigger trg_project_decision_cycle
after insert on private.trading_events
for each row
when (new.event_type = 'decision_cycle')
execute function private.project_decision_cycle_event();

create or replace function private.derive_execution_telemetry()
returns trigger
language plpgsql
set search_path to 'private', 'pg_temp'
as $$
declare
  v_intent private.trading_order_intents%rowtype;
  v_signal private.trading_signals%rowtype;
  v_side_mult numeric := 1;
begin
  if tg_op = 'UPDATE' then
    new.decision_at := coalesce(new.decision_at, old.decision_at);
    new.decision_reference_price := coalesce(new.decision_reference_price, old.decision_reference_price);
    new.decision_bid := coalesce(new.decision_bid, old.decision_bid);
    new.decision_ask := coalesce(new.decision_ask, old.decision_ask);
    new.decision_midpoint := coalesce(new.decision_midpoint, old.decision_midpoint);
    new.observed_spread := coalesce(new.observed_spread, old.observed_spread);
    new.order_intent_at := coalesce(new.order_intent_at, old.order_intent_at);
    new.broker_acknowledged_at := coalesce(new.broker_acknowledged_at, old.broker_acknowledged_at);
  end if;

  if new.order_intent_id is not null then
    select * into v_intent
    from private.trading_order_intents
    where intent_id = new.order_intent_id;

    if found then
      new.order_intent_at := coalesce(new.order_intent_at, v_intent.intended_at);
      new.decision_at := coalesce(
        new.decision_at,
        nullif(v_intent.payload->>'decision_at','')::timestamptz
      );
      new.decision_reference_price := coalesce(
        new.decision_reference_price,
        nullif(v_intent.payload->>'decision_reference_price','')::numeric
      );
      new.decision_bid := coalesce(
        new.decision_bid,
        nullif(v_intent.payload#>>'{decision_quote,bid}','')::numeric
      );
      new.decision_ask := coalesce(
        new.decision_ask,
        nullif(v_intent.payload#>>'{decision_quote,ask}','')::numeric
      );
      new.decision_midpoint := coalesce(
        new.decision_midpoint,
        nullif(v_intent.payload#>>'{decision_quote,midpoint}','')::numeric
      );
      new.observed_spread := coalesce(
        new.observed_spread,
        nullif(v_intent.payload#>>'{decision_quote,spread_pct}','')::numeric
      );

      if v_intent.signal_id is not null then
        select * into v_signal
        from private.trading_signals
        where signal_id = v_intent.signal_id;
        if found then
          new.decision_at := coalesce(new.decision_at, v_signal.signal_at);
          new.decision_reference_price := coalesce(new.decision_reference_price, v_signal.reference_price);
        end if;
      end if;
    end if;
  end if;

  new.broker_acknowledged_at := coalesce(
    new.broker_acknowledged_at,
    nullif(new.payload->>'acknowledged_at','')::timestamptz
  );

  if new.decision_at is not null and new.submitted_at is not null then
    new.decision_to_submit_ms := greatest(
      round(extract(epoch from (new.submitted_at - new.decision_at)) * 1000)::bigint,
      0
    );
  end if;

  if new.submitted_at is not null and new.broker_acknowledged_at is not null then
    new.submit_to_ack_ms := greatest(
      round(extract(epoch from (new.broker_acknowledged_at - new.submitted_at)) * 1000)::bigint,
      0
    );
  end if;

  if new.broker_acknowledged_at is not null and new.filled_at is not null then
    new.ack_to_fill_ms := greatest(
      round(extract(epoch from (new.filled_at - new.broker_acknowledged_at)) * 1000)::bigint,
      0
    );
  end if;

  if lower(coalesce(new.side,'')) = 'sell' then
    v_side_mult := -1;
  end if;

  if new.filled_avg_price is not null and new.decision_midpoint is not null and new.decision_midpoint > 0 then
    new.decision_midpoint_to_fill_bps :=
      v_side_mult * ((new.filled_avg_price - new.decision_midpoint) / new.decision_midpoint) * 10000;
  end if;

  if new.filled_avg_price is not null and new.decision_reference_price is not null and new.decision_reference_price > 0 then
    new.reference_price_to_fill_bps :=
      v_side_mult * ((new.filled_avg_price - new.decision_reference_price) / new.decision_reference_price) * 10000;
  end if;

  return new;
end;
$$;

drop trigger if exists trg_derive_execution_telemetry on private.trading_orders;
create trigger trg_derive_execution_telemetry
before insert or update on private.trading_orders
for each row execute function private.derive_execution_telemetry();

create or replace function private.derive_position_telemetry()
returns trigger
language plpgsql
set search_path to 'private', 'pg_temp'
as $$
begin
  new.entry_fill_price := coalesce(new.entry_fill_price, new.avg_entry_price);
  if new.opened_at is not null and new.closed_at is not null then
    new.holding_duration_ms := greatest(
      round(extract(epoch from (new.closed_at - new.opened_at)) * 1000)::bigint,
      0
    );
  end if;
  return new;
end;
$$;

drop trigger if exists trg_derive_position_telemetry on private.trading_positions;
create trigger trg_derive_position_telemetry
before insert or update on private.trading_positions
for each row execute function private.derive_position_telemetry();

create or replace function private.project_position_metrics_event()
returns trigger
language plpgsql
set search_path to 'private', 'pg_temp'
as $$
declare
  v_position_id uuid;
  v_mfe numeric;
  v_mae numeric;
  v_peak_price numeric;
  v_trough_price numeric;
  v_peak_at timestamptz;
  v_trough_at timestamptz;
  v_time_to_mfe bigint;
  v_time_to_mae bigint;
  v_entry_reference numeric;
begin
  if new.event_type <> 'position_metrics' or new.symbol is null then
    return new;
  end if;

  v_mfe := nullif(new.payload->>'max_favorable_excursion', '')::numeric;
  v_mae := nullif(new.payload->>'max_adverse_excursion', '')::numeric;
  v_peak_price := nullif(new.payload->>'peak_favorable_price', '')::numeric;
  v_trough_price := nullif(new.payload->>'peak_adverse_price', '')::numeric;
  v_peak_at := nullif(new.payload->>'peak_favorable_at', '')::timestamptz;
  v_trough_at := nullif(new.payload->>'peak_adverse_at', '')::timestamptz;
  v_time_to_mfe := nullif(new.payload->>'time_to_mfe_ms', '')::bigint;
  v_time_to_mae := nullif(new.payload->>'time_to_mae_ms', '')::bigint;
  v_entry_reference := nullif(new.payload->>'entry_reference_price', '')::numeric;

  select p.position_id
    into v_position_id
  from private.trading_positions p
  where p.symbol = upper(new.symbol)
    and (new.run_id is null or p.run_id = new.run_id)
    and p.opened_at <= new.occurred_at
    and (p.closed_at is null or p.closed_at >= new.occurred_at)
  order by p.opened_at desc
  limit 1;

  if v_position_id is not null then
    update private.trading_positions p
    set
      entry_reference_price = coalesce(
        p.entry_reference_price,
        v_entry_reference,
        (
          select s.reference_price
          from private.trading_orders o
          left join private.trading_order_intents i on i.intent_id = o.order_intent_id
          left join private.trading_signals s on s.signal_id = i.signal_id
          where o.broker_order_id = p.payload->>'entry_order_id'
          limit 1
        )
      ),
      entry_fill_price = coalesce(p.entry_fill_price, p.avg_entry_price),
      peak_favorable_price = case
        when v_mfe is null then p.peak_favorable_price
        when p.max_favorable_excursion is null or v_mfe >= p.max_favorable_excursion
          then coalesce(v_peak_price, p.peak_favorable_price)
        else p.peak_favorable_price
      end,
      peak_favorable_at = case
        when v_mfe is null then p.peak_favorable_at
        when p.max_favorable_excursion is null or v_mfe >= p.max_favorable_excursion
          then coalesce(v_peak_at, p.peak_favorable_at)
        else p.peak_favorable_at
      end,
      time_to_mfe_ms = case
        when v_mfe is null then p.time_to_mfe_ms
        when p.max_favorable_excursion is null or v_mfe >= p.max_favorable_excursion
          then coalesce(v_time_to_mfe, p.time_to_mfe_ms)
        else p.time_to_mfe_ms
      end,
      peak_adverse_price = case
        when v_mae is null then p.peak_adverse_price
        when p.max_adverse_excursion is null or v_mae <= p.max_adverse_excursion
          then coalesce(v_trough_price, p.peak_adverse_price)
        else p.peak_adverse_price
      end,
      peak_adverse_at = case
        when v_mae is null then p.peak_adverse_at
        when p.max_adverse_excursion is null or v_mae <= p.max_adverse_excursion
          then coalesce(v_trough_at, p.peak_adverse_at)
        else p.peak_adverse_at
      end,
      time_to_mae_ms = case
        when v_mae is null then p.time_to_mae_ms
        when p.max_adverse_excursion is null or v_mae <= p.max_adverse_excursion
          then coalesce(v_time_to_mae, p.time_to_mae_ms)
        else p.time_to_mae_ms
      end,
      max_favorable_excursion = case
        when v_mfe is null then p.max_favorable_excursion
        when p.max_favorable_excursion is null then v_mfe
        else greatest(p.max_favorable_excursion, v_mfe)
      end,
      max_adverse_excursion = case
        when v_mae is null then p.max_adverse_excursion
        when p.max_adverse_excursion is null then v_mae
        else least(p.max_adverse_excursion, v_mae)
      end,
      excursion_source = coalesce(nullif(new.payload->>'source',''), p.excursion_source),
      excursion_updated_at = new.occurred_at,
      payload = coalesce(p.payload, '{}'::jsonb)
        || jsonb_build_object('excursion_metrics', new.payload)
    where p.position_id = v_position_id;
  end if;

  return new;
end;
$$;

insert into private.trading_experiments (
  experiment_key,
  name,
  research_family,
  hypothesis,
  environment,
  status,
  dataset_window,
  methodology,
  metrics,
  decision,
  conclusion,
  manifest_version,
  manifest_hash,
  manifest_hash_method,
  code_commit,
  data_source,
  data_feed,
  timeframe,
  rejection_criteria,
  stage_reached,
  survivor_state,
  report_reference,
  completed_at,
  capital_scaling_authorized
) values (
  'edge-corpus-v1',
  'Edge Corpus v1',
  'Edge Discovery v1 directional continuation',
  'At least one frozen one-minute long directional-continuation family may retain robust positive expectancy after frozen costs and period-level elimination gates.',
  'simulation',
  'rejected',
  jsonb_build_object(
    'development', jsonb_build_array(
      jsonb_build_object('id','dev-01','start','2026-01-05','end','2026-01-23'),
      jsonb_build_object('id','dev-02','start','2026-01-26','end','2026-02-13'),
      jsonb_build_object('id','dev-03','start','2026-02-17','end','2026-03-06'),
      jsonb_build_object('id','dev-04','start','2026-03-09','end','2026-03-27'),
      jsonb_build_object('id','dev-05','start','2026-03-30','end','2026-04-17'),
      jsonb_build_object('id','dev-06','start','2026-04-20','end','2026-05-08')
    ),
    'validation', jsonb_build_array(
      jsonb_build_object('id','val-01','start','2026-05-18','end','2026-06-05','opened',false),
      jsonb_build_object('id','val-02','start','2026-06-08','end','2026-06-26','opened',false)
    ),
    'holdout', jsonb_build_array(
      jsonb_build_object('id','holdout-01','start','2026-07-13','end','2026-07-31','opened',false)
    ),
    'quarantine', jsonb_build_array(
      jsonb_build_object('start','2026-08-03','end','2026-09-25','opened',false)
    )
  ),
  jsonb_build_object(
    'families', jsonb_build_array(
      'controlled_continuation',
      'pullback_reclaim',
      'compression_breakout',
      'relative_strength_impulse',
      'opening_breakout_retest'
    ),
    'forward_horizon_minutes', 15,
    'stop_pct', 0.0035,
    'target_pct', 0.005,
    'same_family_symbol_cooldown_minutes', 15,
    'cost_scenarios', jsonb_build_array(
      jsonb_build_object('name','base','spread_bps',5,'slippage_bps_per_side',2),
      jsonb_build_object('name','moderate','spread_bps',8,'slippage_bps_per_side',3),
      jsonb_build_object('name','stress','spread_bps',12,'slippage_bps_per_side',5,'round_trip_bps',22)
    ),
    'corpus_integrity_definition', 'complete expected regular-session representation plus completed pagination',
    'iex_bar_density_role', 'diagnostic_only',
    'artifact_status', 'historical report exists; canonical row reflects the completed corrected runner evidence'
  ),
  jsonb_build_object(
    'development_symbols_complete_expected_sessions', 36,
    'development_symbol_count', 36,
    'shared_panel_ratio', 1.0,
    'validation_opened', false,
    'holdout_opened', false,
    'survivors', jsonb_build_array(),
    'rejected_families', jsonb_build_array(
      'controlled_continuation',
      'pullback_reclaim',
      'compression_breakout',
      'relative_strength_impulse',
      'opening_breakout_retest'
    )
  ),
  'reject',
  'All five frozen families were irreversibly rejected in development. Validation and holdout remained unopened. Capital scaling was not authorized.',
  'edge-corpus-v1',
  '62b965acbae2692381a39661492d7c9c03508ca3',
  'git_blob_sha1',
  'cd98ea04f48047219ff9bd94c462488aa077b9c6',
  'Alpaca historical bars',
  'iex',
  '1Min',
  jsonb_build_object(
    'development_min_positive_periods', 4,
    'development_min_worst_period_expectancy_pct', -0.0015,
    'must_survive_every_cost_scenario', true,
    'stress_gate', 'worst-period expectancy floor enforced under frozen stress costs'
  ),
  'development',
  'all_rejected',
  'research/edge-corpus-v1-result-2026-09-25.md; research/corpus-integrity-diagnosis-2026-09-26.md',
  now(),
  false
)
on conflict (experiment_key) do update
set
  name = excluded.name,
  research_family = excluded.research_family,
  hypothesis = excluded.hypothesis,
  environment = excluded.environment,
  status = excluded.status,
  dataset_window = excluded.dataset_window,
  methodology = excluded.methodology,
  metrics = excluded.metrics,
  decision = excluded.decision,
  conclusion = excluded.conclusion,
  manifest_version = excluded.manifest_version,
  manifest_hash = excluded.manifest_hash,
  manifest_hash_method = excluded.manifest_hash_method,
  code_commit = excluded.code_commit,
  data_source = excluded.data_source,
  data_feed = excluded.data_feed,
  timeframe = excluded.timeframe,
  rejection_criteria = excluded.rejection_criteria,
  stage_reached = excluded.stage_reached,
  survivor_state = excluded.survivor_state,
  report_reference = excluded.report_reference,
  completed_at = coalesce(private.trading_experiments.completed_at, excluded.completed_at),
  capital_scaling_authorized = false;

insert into private.trading_experiments (
  experiment_key,
  name,
  research_family,
  hypothesis,
  environment,
  status,
  dataset_window,
  methodology,
  metrics,
  decision,
  conclusion,
  manifest_version,
  manifest_hash,
  manifest_hash_method,
  code_commit,
  data_source,
  data_feed,
  timeframe,
  rejection_criteria,
  stage_reached,
  survivor_state,
  report_reference,
  completed_at,
  capital_scaling_authorized
) values (
  'edge-discovery-v2-residual-downshock-rebound-v2.1',
  'Residual Downshock Rebound v2.1',
  'Edge Discovery v2 residual downshock reversion',
  'A sufficiently large negative idiosyncratic five-minute residual shock relative to synchronized market and sector observations may exhibit a measurable 30-minute rebound.',
  'simulation',
  'planned',
  jsonb_build_object(
    'development', jsonb_build_object('start','2026-01-05','end','2026-05-08','windows',6,'opened',false),
    'validation', jsonb_build_object('start','2026-05-18','end','2026-06-26','opened',false),
    'holdout', jsonb_build_object('start','2026-07-13','end','2026-07-31','opened',false),
    'quarantine', jsonb_build_object('start','2026-08-03','end','2026-09-25','opened',false)
  ),
  jsonb_build_object(
    'predecessor_lane', 'edge-corpus-v1',
    'predecessor_lane_closed', true,
    'rejected_predecessor_families', jsonb_build_array(
      'controlled_continuation',
      'pullback_reclaim',
      'compression_breakout',
      'relative_strength_impulse',
      'opening_breakout_retest'
    ),
    'observation_interval', '5Min synchronized',
    'primary_forward_horizon_minutes', 30,
    'primary_target', 'market-and-sector residual forward return',
    'experiment_run_authorized', false,
    'cost_assumptions', jsonb_build_object(
      'base_cost_evidence_required', true,
      'stress_cost_evidence_required', true,
      'exact_cost_schedule', 'not re-derived in this telemetry task'
    )
  ),
  jsonb_build_object(
    'run_started', false,
    'development_opened', false,
    'validation_opened', false,
    'holdout_opened', false
  ),
  'DESIGN/FROZEN',
  'Research direction frozen only. No experiment has been implemented or run. Capital scaling remains unauthorized.',
  'residual-downshock-rebound-v2.1-design',
  null,
  null,
  null,
  'Alpaca historical bars',
  'iex',
  '5Min synchronized observations',
  jsonb_build_object(
    'complete_pagination_and_session_representation_required', true,
    'minimum_synchronized_coverage_ratio', 0.90,
    'minimum_events', 240,
    'minimum_sessions', 50,
    'minimum_events_per_development_window', 20,
    'minimum_development_windows_meeting_event_floor', 4,
    'matched_control_uplift_positive', true,
    'stress_expectancy_positive', true,
    'stress_profit_factor_min', 1.15,
    'minimum_positive_development_windows', 4,
    'worst_development_window_floor', -0.0010,
    'base_cost_bootstrap_95pct_lower_bound_positive', true,
    'max_symbol_concentration', 0.20,
    'max_sector_concentration', 0.35,
    'must_survive_remove_best_symbol', true,
    'must_survive_remove_best_sector', true
  ),
  'design_frozen',
  'not_run',
  null,
  null,
  false
)
on conflict (experiment_key) do update
set
  name = excluded.name,
  research_family = excluded.research_family,
  hypothesis = excluded.hypothesis,
  environment = excluded.environment,
  status = excluded.status,
  dataset_window = excluded.dataset_window,
  methodology = excluded.methodology,
  metrics = excluded.metrics,
  decision = excluded.decision,
  conclusion = excluded.conclusion,
  manifest_version = excluded.manifest_version,
  manifest_hash = excluded.manifest_hash,
  manifest_hash_method = excluded.manifest_hash_method,
  code_commit = excluded.code_commit,
  data_source = excluded.data_source,
  data_feed = excluded.data_feed,
  timeframe = excluded.timeframe,
  rejection_criteria = excluded.rejection_criteria,
  stage_reached = excluded.stage_reached,
  survivor_state = excluded.survivor_state,
  report_reference = excluded.report_reference,
  completed_at = excluded.completed_at,
  capital_scaling_authorized = false;
