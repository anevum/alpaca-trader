-- RHEN crypto market-lane attribution.
-- Additive/backward-compatible. Existing equity evidence and behavior remain unchanged.

alter table private.trading_scan_cycles
  add column if not exists market_lane text,
  add column if not exists model_version text,
  add column if not exists calibration_version text,
  add column if not exists regime_version text,
  add column if not exists execution_adapter_version text;

alter table private.trading_candidate_evaluations
  add column if not exists market_lane text,
  add column if not exists model_version text,
  add column if not exists calibration_version text,
  add column if not exists regime_version text,
  add column if not exists execution_adapter_version text,
  add column if not exists raw_features jsonb not null default '{}'::jsonb,
  add column if not exists normalized_features jsonb not null default '{}'::jsonb,
  add column if not exists ads_prediction jsonb not null default '{}'::jsonb,
  add column if not exists available_depth numeric,
  add column if not exists trade_volume numeric,
  add column if not exists trade_count bigint,
  add column if not exists volatility_state jsonb not null default '{}'::jsonb,
  add column if not exists time_state jsonb not null default '{}'::jsonb;

alter table private.trading_signals
  add column if not exists market_lane text,
  add column if not exists model_version text,
  add column if not exists calibration_version text,
  add column if not exists regime_version text,
  add column if not exists execution_adapter_version text;

alter table private.trading_order_intents
  add column if not exists market_lane text,
  add column if not exists model_version text,
  add column if not exists calibration_version text,
  add column if not exists regime_version text,
  add column if not exists execution_adapter_version text;

alter table private.trading_orders
  add column if not exists market_lane text,
  add column if not exists model_version text,
  add column if not exists calibration_version text,
  add column if not exists regime_version text,
  add column if not exists execution_adapter_version text;

alter table private.trading_candidate_forward_outcomes
  add column if not exists market_lane text,
  add column if not exists strategy_family text,
  add column if not exists strategy_version_id text,
  add column if not exists model_version text,
  add column if not exists calibration_version text,
  add column if not exists regime_version text,
  add column if not exists execution_adapter_version text;

create index if not exists trading_candidate_market_lane_observed_idx
  on private.trading_candidate_evaluations(market_lane, observed_at desc);
create index if not exists trading_forward_market_lane_horizon_idx
  on private.trading_candidate_forward_outcomes(market_lane, horizon_minutes, computed_at desc);
create index if not exists trading_orders_market_lane_updated_idx
  on private.trading_orders(market_lane, updated_at desc);

update private.trading_candidate_evaluations
set market_lane = case
      when coalesce(features->>'market_lane', features->>'market', research_attribution->>'market') = 'crypto'
        or strategy_version_id like 'CRYPTO-%' then 'crypto'
      else coalesce(market_lane, 'us_equity')
    end
where market_lane is null;

update private.trading_candidate_evaluations
set raw_features = case
      when market_lane='crypto' then coalesce(features->'raw_features', features#>'{feature_state,raw}', '{}'::jsonb)
      else raw_features
    end,
    normalized_features = case
      when market_lane='crypto' then coalesce(features->'normalized_features', features#>'{feature_state,normalized}', '{}'::jsonb)
      else normalized_features
    end,
    ads_prediction = case
      when market_lane='crypto' then coalesce(features->'ads_crypto', '{}'::jsonb)
      else ads_prediction
    end,
    model_version = coalesce(model_version, features->>'model_version'),
    calibration_version = coalesce(calibration_version, features->>'calibration_version'),
    regime_version = coalesce(regime_version, features->>'regime_version'),
    execution_adapter_version = coalesce(execution_adapter_version, features->>'execution_adapter_version'),
    available_depth = coalesce(available_depth, nullif(features#>>'{feature_state,raw,available_depth}','')::numeric),
    trade_volume = coalesce(trade_volume, nullif(features#>>'{feature_state,raw,trade_volume}','')::numeric),
    trade_count = coalesce(trade_count, nullif(features#>>'{feature_state,raw,trade_count}','')::bigint),
    volatility_state = case
      when market_lane='crypto' then jsonb_build_object(
        'realized_volatility', features#>'{feature_state,raw,realized_volatility}',
        'volatility_normalized_momentum', features#>'{feature_state,raw,volatility_normalized_momentum}'
      )
      else volatility_state
    end,
    time_state = case
      when market_lane='crypto' then coalesce(features->'time_state', features#>'{feature_state,time_state}', '{}'::jsonb)
      else time_state
    end
where market_lane='crypto';

create or replace function private.project_crypto_cycle_attribution()
returns trigger
language plpgsql
set search_path = private, pg_temp
as $$
declare
  v_cycle_id bigint;
  v_candidate jsonb;
  v_lane text;
begin
  if new.event_type <> 'decision_cycle' then return new; end if;
  v_lane := coalesce(nullif(new.payload->>'market_lane',''), 'us_equity');

  select scan_cycle_id into v_cycle_id
  from private.trading_scan_cycles
  where cycle_key = new.payload->>'cycle_key';
  if v_cycle_id is null then return new; end if;

  update private.trading_scan_cycles
  set market_lane = v_lane,
      model_version = nullif(new.payload->>'model_version',''),
      calibration_version = nullif(new.payload->>'calibration_version',''),
      regime_version = nullif(new.payload->>'regime_version',''),
      execution_adapter_version = nullif(new.payload->>'execution_adapter_version','')
  where scan_cycle_id=v_cycle_id;

  if jsonb_typeof(new.payload->'candidates')='array' then
    for v_candidate in select value from jsonb_array_elements(new.payload->'candidates') loop
      update private.trading_candidate_evaluations
      set market_lane = coalesce(nullif(v_candidate->>'market_lane',''), v_lane),
          model_version = nullif(v_candidate->>'model_version',''),
          calibration_version = nullif(v_candidate->>'calibration_version',''),
          regime_version = nullif(v_candidate->>'regime_version',''),
          execution_adapter_version = nullif(v_candidate->>'execution_adapter_version',''),
          raw_features = coalesce(v_candidate->'raw_features', v_candidate#>'{features,raw_features}', v_candidate#>'{features,feature_state,raw}', '{}'::jsonb),
          normalized_features = coalesce(v_candidate->'normalized_features', v_candidate#>'{features,normalized_features}', v_candidate#>'{features,feature_state,normalized}', '{}'::jsonb),
          ads_prediction = coalesce(v_candidate->'ads_crypto', v_candidate#>'{features,ads_crypto}', '{}'::jsonb),
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
            'realized_volatility', v_candidate#>'{features,feature_state,raw,realized_volatility}',
            'volatility_normalized_momentum', v_candidate#>'{features,feature_state,raw,volatility_normalized_momentum}'
          ),
          time_state = coalesce(v_candidate#>'{features,time_state}', v_candidate#>'{features,feature_state,time_state}', '{}'::jsonb)
      where scan_cycle_id=v_cycle_id
        and symbol=upper(v_candidate->>'symbol');
    end loop;
  end if;
  return new;
end;
$$;

drop trigger if exists trg_zzz_project_crypto_cycle_attribution on private.trading_events;
create trigger trg_zzz_project_crypto_cycle_attribution
after insert on private.trading_events
for each row when (new.event_type='decision_cycle')
execute function private.project_crypto_cycle_attribution();

create or replace function private.populate_signal_market_attribution()
returns trigger
language plpgsql
set search_path = private, pg_temp
as $$
declare
  v_candidate private.trading_candidate_evaluations%rowtype;
begin
  if new.candidate_id is not null then
    select * into v_candidate from private.trading_candidate_evaluations where candidate_id=new.candidate_id;
    if found then
      new.market_lane := coalesce(new.market_lane, v_candidate.market_lane);
      new.model_version := coalesce(new.model_version, v_candidate.model_version);
      new.calibration_version := coalesce(new.calibration_version, v_candidate.calibration_version);
      new.regime_version := coalesce(new.regime_version, v_candidate.regime_version);
      new.execution_adapter_version := coalesce(new.execution_adapter_version, v_candidate.execution_adapter_version);
    end if;
  end if;
  new.market_lane := coalesce(new.market_lane, new.payload#>>'{metadata,market_lane}',
    case when new.strategy_version_id like 'CRYPTO-%' then 'crypto' else 'us_equity' end);
  new.model_version := coalesce(new.model_version, new.payload#>>'{metadata,model_version}');
  new.calibration_version := coalesce(new.calibration_version, new.payload#>>'{metadata,calibration_version}');
  new.regime_version := coalesce(new.regime_version, new.payload#>>'{metadata,regime_version}');
  new.execution_adapter_version := coalesce(new.execution_adapter_version, new.payload#>>'{metadata,execution_adapter_version}');
  return new;
end;
$$;

drop trigger if exists trg_populate_signal_market_attribution on private.trading_signals;
create trigger trg_populate_signal_market_attribution
before insert or update on private.trading_signals
for each row execute function private.populate_signal_market_attribution();

create or replace function private.populate_intent_market_attribution()
returns trigger
language plpgsql
set search_path = private, pg_temp
as $$
declare
  v_signal private.trading_signals%rowtype;
begin
  if new.signal_id is not null then
    select * into v_signal from private.trading_signals where signal_id=new.signal_id;
    if found then
      new.market_lane := coalesce(new.market_lane, v_signal.market_lane);
      new.model_version := coalesce(new.model_version, v_signal.model_version);
      new.calibration_version := coalesce(new.calibration_version, v_signal.calibration_version);
      new.regime_version := coalesce(new.regime_version, v_signal.regime_version);
      new.execution_adapter_version := coalesce(new.execution_adapter_version, v_signal.execution_adapter_version);
    end if;
  end if;
  new.market_lane := coalesce(new.market_lane,
    case when new.strategy_version_id like 'CRYPTO-%' then 'crypto' else 'us_equity' end);
  return new;
end;
$$;

drop trigger if exists trg_populate_intent_market_attribution on private.trading_order_intents;
create trigger trg_populate_intent_market_attribution
before insert or update on private.trading_order_intents
for each row execute function private.populate_intent_market_attribution();

create or replace function private.populate_order_market_attribution()
returns trigger
language plpgsql
set search_path = private, pg_temp
as $$
declare
  v_intent private.trading_order_intents%rowtype;
begin
  if new.order_intent_id is not null then
    select * into v_intent from private.trading_order_intents where intent_id=new.order_intent_id;
    if found then
      new.market_lane := coalesce(new.market_lane, v_intent.market_lane);
      new.model_version := coalesce(new.model_version, v_intent.model_version);
      new.calibration_version := coalesce(new.calibration_version, v_intent.calibration_version);
      new.regime_version := coalesce(new.regime_version, v_intent.regime_version);
      new.execution_adapter_version := coalesce(new.execution_adapter_version, v_intent.execution_adapter_version);
    end if;
  end if;
  new.market_lane := coalesce(new.market_lane,
    case when new.strategy_version_id like 'CRYPTO-%' or new.client_order_id like 'anevum-crypto-%'
      then 'crypto' else 'us_equity' end);
  return new;
end;
$$;

drop trigger if exists trg_populate_order_market_attribution on private.trading_orders;
create trigger trg_populate_order_market_attribution
before insert or update on private.trading_orders
for each row execute function private.populate_order_market_attribution();

create or replace function private.populate_forward_market_attribution()
returns trigger
language plpgsql
set search_path = private, pg_temp
as $$
declare
  v_candidate private.trading_candidate_evaluations%rowtype;
begin
  select * into v_candidate
  from private.trading_candidate_evaluations
  where candidate_id=new.candidate_id;
  if found then
    new.market_lane := coalesce(new.market_lane, v_candidate.market_lane);
    new.strategy_family := coalesce(new.strategy_family, v_candidate.strategy_family);
    new.strategy_version_id := coalesce(new.strategy_version_id, v_candidate.strategy_version_id);
    new.model_version := coalesce(new.model_version, v_candidate.model_version);
    new.calibration_version := coalesce(new.calibration_version, v_candidate.calibration_version);
    new.regime_version := coalesce(new.regime_version, v_candidate.regime_version);
    new.execution_adapter_version := coalesce(new.execution_adapter_version, v_candidate.execution_adapter_version);
  end if;
  return new;
end;
$$;

drop trigger if exists trg_populate_forward_market_attribution on private.trading_candidate_forward_outcomes;
create trigger trg_populate_forward_market_attribution
before insert or update on private.trading_candidate_forward_outcomes
for each row execute function private.populate_forward_market_attribution();

update private.trading_signals s
set market_lane=c.market_lane,
    model_version=c.model_version,
    calibration_version=c.calibration_version,
    regime_version=c.regime_version,
    execution_adapter_version=c.execution_adapter_version
from private.trading_candidate_evaluations c
where s.candidate_id=c.candidate_id
  and s.market_lane is null;

update private.trading_order_intents i
set market_lane=s.market_lane,
    model_version=s.model_version,
    calibration_version=s.calibration_version,
    regime_version=s.regime_version,
    execution_adapter_version=s.execution_adapter_version
from private.trading_signals s
where i.signal_id=s.signal_id
  and i.market_lane is null;

update private.trading_orders o
set market_lane=i.market_lane,
    model_version=i.model_version,
    calibration_version=i.calibration_version,
    regime_version=i.regime_version,
    execution_adapter_version=i.execution_adapter_version
from private.trading_order_intents i
where o.order_intent_id=i.intent_id
  and o.market_lane is null;

update private.trading_candidate_forward_outcomes o
set market_lane=c.market_lane,
    strategy_family=c.strategy_family,
    strategy_version_id=c.strategy_version_id,
    model_version=c.model_version,
    calibration_version=c.calibration_version,
    regime_version=c.regime_version,
    execution_adapter_version=c.execution_adapter_version
from private.trading_candidate_evaluations c
where o.candidate_id=c.candidate_id
  and o.market_lane is null;

comment on column private.trading_candidate_evaluations.market_lane
is 'Explicit market lane attribution; crypto and us_equity evidence never share calibration populations.';
