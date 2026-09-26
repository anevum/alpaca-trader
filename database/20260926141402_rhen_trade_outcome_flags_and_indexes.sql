-- Complete traceability indexes and derive target/stop interactions only from
-- durable observed excursion or exit evidence. Unknown history remains null.

create index if not exists trading_experiments_parent_idx
  on private.trading_experiments(parent_experiment_id)
  where parent_experiment_id is not null;
create index if not exists trading_research_decision_experiments_experiment_idx
  on private.trading_research_decision_experiments(experiment_id);
create index if not exists trading_research_decisions_supersedes_idx
  on private.trading_research_decisions(supersedes_decision_id)
  where supersedes_decision_id is not null;
create index if not exists trading_research_decisions_superseded_by_idx
  on private.trading_research_decisions(superseded_by_decision_id)
  where superseded_by_decision_id is not null;
create index if not exists trading_runtime_instances_run_idx
  on private.trading_runtime_instances(run_id)
  where run_id is not null;
create index if not exists trading_runtime_instances_strategy_idx
  on private.trading_runtime_instances(strategy_version_id)
  where strategy_version_id is not null;

create or replace function private.enrich_position_analysis()
returns trigger
language plpgsql
set search_path = private, pg_temp
as $$
declare
  v_signal private.trading_signals%rowtype;
begin
  if coalesce(new.payload->>'entry_order_id','') <> '' then
    select s.* into v_signal
    from private.trading_orders o
    join private.trading_order_intents i on i.intent_id=o.order_intent_id
    join private.trading_signals s on s.signal_id=i.signal_id
    where o.broker_order_id=new.payload->>'entry_order_id'
    limit 1;
    if found then
      new.entry_reason := coalesce(new.entry_reason, v_signal.payload->>'reason');
      new.initial_stop_price := coalesce(new.initial_stop_price, v_signal.stop_price);
      new.target_price := coalesce(new.target_price, v_signal.target_price);
      new.entry_reference_price := coalesce(new.entry_reference_price, v_signal.reference_price);
      new.context_tags := new.context_tags || jsonb_build_object(
        'strategy_checks', coalesce(v_signal.payload#>'{metadata,checks}','{}'::jsonb),
        'market_confirmations', coalesce(v_signal.payload#>'{metadata,confirmations}','{}'::jsonb),
        'regime_confirmations', coalesce(v_signal.payload#>'{metadata,regime_confirmations}','{}'::jsonb)
      );
    end if;
  end if;
  if new.avg_entry_price is not null and new.avg_entry_price > 0 and new.avg_exit_price is not null then
    new.realized_return := (new.avg_exit_price / new.avg_entry_price) - 1;
  end if;
  if new.avg_entry_price is not null and new.avg_entry_price > 0
     and new.max_favorable_excursion is not null and new.target_price is not null then
    new.target_touched := new.max_favorable_excursion >= (new.target_price / new.avg_entry_price) - 1;
  elsif new.peak_favorable_price is not null and new.target_price is not null then
    new.target_touched := new.peak_favorable_price >= new.target_price;
  end if;
  if new.avg_entry_price is not null and new.avg_entry_price > 0
     and new.max_adverse_excursion is not null and new.initial_stop_price is not null then
    new.stop_touched := new.max_adverse_excursion <= (new.initial_stop_price / new.avg_entry_price) - 1;
  elsif new.peak_adverse_price is not null and new.initial_stop_price is not null then
    new.stop_touched := new.peak_adverse_price <= new.initial_stop_price;
  end if;
  if new.exit_reason = 'broker protective stop filled' then
    new.stop_touched := true;
  end if;
  return new;
end;
$$;

update private.trading_positions
set target_touched = case
      when avg_entry_price > 0 and max_favorable_excursion is not null and target_price is not null
        then max_favorable_excursion >= (target_price / avg_entry_price) - 1
      else target_touched
    end,
    stop_touched = case
      when exit_reason = 'broker protective stop filled' then true
      when avg_entry_price > 0 and max_adverse_excursion is not null and initial_stop_price is not null
        then max_adverse_excursion <= (initial_stop_price / avg_entry_price) - 1
      else stop_touched
    end;
