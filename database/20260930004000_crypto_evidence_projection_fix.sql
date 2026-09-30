-- Production verification fixes for RHEN's continuous crypto lane.
-- Raw trading_events remain immutable; this migration only corrects derived attribution
-- projections and crypto forward-evidence completion semantics.

create or replace function private.project_crypto_cycle_attribution()
returns trigger
language plpgsql
set search_path = private, pg_temp
as $$
declare
  v_cycle_id bigint;
  v_candidate jsonb;
  v_lane text;
  v_cycle_strategy_version text;
begin
  if new.event_type <> 'decision_cycle' then return new; end if;
  v_lane := coalesce(nullif(new.payload->>'market_lane',''), 'us_equity');

  select scan_cycle_id into v_cycle_id
  from private.trading_scan_cycles
  where cycle_key = new.payload->>'cycle_key';
  if v_cycle_id is null then return new; end if;

  if jsonb_typeof(new.payload->'candidates')='array' then
    select nullif(value->>'strategy_version_id','')
    into v_cycle_strategy_version
    from jsonb_array_elements(new.payload->'candidates')
    where nullif(value->>'strategy_version_id','') is not null
    limit 1;
  end if;

  update private.trading_scan_cycles
  set market_lane = v_lane,
      strategy_version_id = case
        when v_lane='crypto' then coalesce(v_cycle_strategy_version, strategy_version_id)
        else strategy_version_id
      end,
      model_version = nullif(new.payload->>'model_version',''),
      calibration_version = nullif(new.payload->>'calibration_version',''),
      regime_version = nullif(new.payload->>'regime_version',''),
      execution_adapter_version = nullif(new.payload->>'execution_adapter_version','')
  where scan_cycle_id=v_cycle_id;

  if jsonb_typeof(new.payload->'candidates')='array' then
    for v_candidate in select value from jsonb_array_elements(new.payload->'candidates') loop
      update private.trading_candidate_evaluations
      set strategy_version_id = coalesce(
            nullif(v_candidate->>'strategy_version_id',''),
            strategy_version_id
          ),
          market_lane = coalesce(nullif(v_candidate->>'market_lane',''), v_lane),
          model_version = nullif(v_candidate->>'model_version',''),
          calibration_version = nullif(v_candidate->>'calibration_version',''),
          regime_version = nullif(v_candidate->>'regime_version',''),
          execution_adapter_version = nullif(v_candidate->>'execution_adapter_version',''),
          raw_features = coalesce(
            v_candidate->'raw_features',
            v_candidate#>'{features,raw_features}',
            v_candidate#>'{features,feature_state,raw}',
            '{}'::jsonb
          ),
          normalized_features = coalesce(
            v_candidate->'normalized_features',
            v_candidate#>'{features,normalized_features}',
            v_candidate#>'{features,feature_state,normalized}',
            '{}'::jsonb
          ),
          ads_prediction = coalesce(
            v_candidate->'ads_crypto',
            v_candidate#>'{features,ads_crypto}',
            '{}'::jsonb
          ),
          available_depth = nullif(coalesce(
            v_candidate#>>'{features,feature_state,raw,available_depth}',
            v_candidate#>>'{features,market_quality,available_depth}'
          ),'')::numeric,
          trade_volume = nullif(coalesce(
            v_candidate#>>'{features,feature_state,raw,trade_volume}',
            v_candidate#>>'{features,market_quality,trade_volume}'
          ),'')::numeric,
          trade_count = nullif(coalesce(
            v_candidate#>>'{features,feature_state,raw,trade_count}',
            v_candidate#>>'{features,market_quality,trade_count}'
          ),'')::bigint,
          volatility_state = jsonb_build_object(
            'realized_volatility',
              v_candidate#>'{features,feature_state,raw,realized_volatility}',
            'volatility_normalized_momentum',
              v_candidate#>'{features,feature_state,raw,volatility_normalized_momentum}'
          ),
          time_state = coalesce(
            v_candidate#>'{features,time_state}',
            v_candidate#>'{features,feature_state,time_state}',
            '{}'::jsonb
          )
      where scan_cycle_id=v_cycle_id
        and symbol=upper(v_candidate->>'symbol');
    end loop;
  end if;

  update private.trading_signals s
  set strategy_version_id=c.strategy_version_id,
      market_lane=c.market_lane,
      model_version=c.model_version,
      calibration_version=c.calibration_version,
      regime_version=c.regime_version,
      execution_adapter_version=c.execution_adapter_version
  from private.trading_candidate_evaluations c
  where c.scan_cycle_id=v_cycle_id
    and c.market_lane='crypto'
    and s.candidate_id=c.candidate_id;

  return new;
end;
$$;

create or replace function private.populate_signal_market_attribution()
returns trigger
language plpgsql
set search_path = private, pg_temp
as $$
declare
  v_candidate private.trading_candidate_evaluations%rowtype;
begin
  if new.candidate_id is not null then
    select * into v_candidate
    from private.trading_candidate_evaluations
    where candidate_id=new.candidate_id;
    if found then
      if v_candidate.market_lane='crypto' then
        new.strategy_version_id := coalesce(
          v_candidate.strategy_version_id,
          new.strategy_version_id
        );
      end if;
      new.market_lane := coalesce(new.market_lane, v_candidate.market_lane);
      new.model_version := coalesce(new.model_version, v_candidate.model_version);
      new.calibration_version := coalesce(new.calibration_version, v_candidate.calibration_version);
      new.regime_version := coalesce(new.regime_version, v_candidate.regime_version);
      new.execution_adapter_version := coalesce(new.execution_adapter_version, v_candidate.execution_adapter_version);
    end if;
  end if;
  new.market_lane := coalesce(
    new.market_lane,
    new.payload#>>'{metadata,market_lane}',
    case when new.strategy_version_id like 'CRYPTO-%' then 'crypto' else 'us_equity' end
  );
  new.model_version := coalesce(new.model_version, new.payload#>>'{metadata,model_version}');
  new.calibration_version := coalesce(new.calibration_version, new.payload#>>'{metadata,calibration_version}');
  new.regime_version := coalesce(new.regime_version, new.payload#>>'{metadata,regime_version}');
  new.execution_adapter_version := coalesce(new.execution_adapter_version, new.payload#>>'{metadata,execution_adapter_version}');
  return new;
end;
$$;

create or replace function private.populate_intent_market_attribution()
returns trigger
language plpgsql
set search_path = private, pg_temp
as $$
declare
  v_signal private.trading_signals%rowtype;
begin
  if new.signal_id is not null then
    select * into v_signal
    from private.trading_signals
    where signal_id=new.signal_id;
    if found then
      if v_signal.market_lane='crypto' then
        new.strategy_version_id := coalesce(v_signal.strategy_version_id, new.strategy_version_id);
      end if;
      new.market_lane := coalesce(new.market_lane, v_signal.market_lane);
      new.model_version := coalesce(new.model_version, v_signal.model_version);
      new.calibration_version := coalesce(new.calibration_version, v_signal.calibration_version);
      new.regime_version := coalesce(new.regime_version, v_signal.regime_version);
      new.execution_adapter_version := coalesce(new.execution_adapter_version, v_signal.execution_adapter_version);
    end if;
  end if;
  new.market_lane := coalesce(
    new.market_lane,
    case when new.strategy_version_id like 'CRYPTO-%' then 'crypto' else 'us_equity' end
  );
  return new;
end;
$$;

create or replace function private.populate_order_market_attribution()
returns trigger
language plpgsql
set search_path = private, pg_temp
as $$
declare
  v_intent private.trading_order_intents%rowtype;
begin
  if new.order_intent_id is not null then
    select * into v_intent
    from private.trading_order_intents
    where intent_id=new.order_intent_id;
    if found then
      if v_intent.market_lane='crypto' then
        new.strategy_version_id := coalesce(v_intent.strategy_version_id, new.strategy_version_id);
      end if;
      new.market_lane := coalesce(new.market_lane, v_intent.market_lane);
      new.model_version := coalesce(new.model_version, v_intent.model_version);
      new.calibration_version := coalesce(new.calibration_version, v_intent.calibration_version);
      new.regime_version := coalesce(new.regime_version, v_intent.regime_version);
      new.execution_adapter_version := coalesce(new.execution_adapter_version, v_intent.execution_adapter_version);
    end if;
  end if;
  new.market_lane := coalesce(
    new.market_lane,
    case
      when new.strategy_version_id like 'CRYPTO-%'
        or new.client_order_id like 'anevum-crypto-%'
      then 'crypto'
      else 'us_equity'
    end
  );
  return new;
end;
$$;

create or replace function private.project_crypto_forward_status_event()
returns trigger
language plpgsql
set search_path = private, pg_temp
as $$
declare
  v_candidate_id bigint;
  v_total integer;
  v_complete integer;
begin
  if new.event_type <> 'candidate_forward_outcome'
     or new.payload->>'methodology_version' <> 'candidate-forward-crypto-v1'
  then
    return new;
  end if;

  v_candidate_id := nullif(new.payload->>'candidate_id','')::bigint;
  if v_candidate_id is null then return new; end if;

  select
    count(*),
    count(*) filter (where status='complete')
  into v_total, v_complete
  from private.trading_candidate_forward_outcomes
  where candidate_id=v_candidate_id
    and methodology_version='candidate-forward-crypto-v1';

  update private.trading_candidate_evaluations
  set forward_outcomes_status = case
        when v_total < 7 then 'pending'
        when v_complete = 7 then 'complete'
        else 'incomplete'
      end,
      forward_enriched_at=now()
  where candidate_id=v_candidate_id;

  return new;
end;
$$;

drop trigger if exists trg_zzzz_project_crypto_forward_status on private.trading_events;
create trigger trg_zzzz_project_crypto_forward_status
after insert on private.trading_events
for each row
when (new.event_type='candidate_forward_outcome')
execute function private.project_crypto_forward_status_event();

-- Correct derived projections from immutable raw decision-cycle events.
update private.trading_candidate_evaluations
set strategy_version_id = coalesce(
      nullif(features->>'strategy_version_id',''),
      nullif(research_attribution->>'live_strategy_version',''),
      strategy_version_id
    )
where market_lane='crypto';

update private.trading_scan_cycles sc
set strategy_version_id=src.strategy_version_id
from lateral (
  select c.strategy_version_id
  from private.trading_candidate_evaluations c
  where c.scan_cycle_id=sc.scan_cycle_id
    and c.market_lane='crypto'
    and c.strategy_version_id is not null
  limit 1
) src
where sc.market_lane='crypto'
  and src.strategy_version_id is not null;

update private.trading_signals s
set strategy_version_id=c.strategy_version_id,
    market_lane=c.market_lane,
    model_version=c.model_version,
    calibration_version=c.calibration_version,
    regime_version=c.regime_version,
    execution_adapter_version=c.execution_adapter_version
from private.trading_candidate_evaluations c
where s.candidate_id=c.candidate_id
  and c.market_lane='crypto';

update private.trading_order_intents i
set strategy_version_id=s.strategy_version_id,
    market_lane=s.market_lane,
    model_version=s.model_version,
    calibration_version=s.calibration_version,
    regime_version=s.regime_version,
    execution_adapter_version=s.execution_adapter_version
from private.trading_signals s
where i.signal_id=s.signal_id
  and s.market_lane='crypto';

update private.trading_orders o
set strategy_version_id=i.strategy_version_id,
    market_lane=i.market_lane,
    model_version=i.model_version,
    calibration_version=i.calibration_version,
    regime_version=i.regime_version,
    execution_adapter_version=i.execution_adapter_version
from private.trading_order_intents i
where o.order_intent_id=i.intent_id
  and i.market_lane='crypto';

update private.trading_candidate_forward_outcomes o
set strategy_family=c.strategy_family,
    strategy_version_id=c.strategy_version_id,
    market_lane=c.market_lane,
    model_version=c.model_version,
    calibration_version=c.calibration_version,
    regime_version=c.regime_version,
    execution_adapter_version=c.execution_adapter_version
from private.trading_candidate_evaluations c
where o.candidate_id=c.candidate_id
  and c.market_lane='crypto';
