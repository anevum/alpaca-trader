
create or replace function private.link_signal_candidate_from_cycle()
returns trigger
language plpgsql
set search_path to 'private','pg_temp'
as $$
declare
  v_cycle_key text;
  v_candidate_id bigint;
begin
  if new.event_type <> 'order_intent' then
    return new;
  end if;
  v_cycle_key := nullif(new.payload#>>'{signal,payload,cycle_key}','');
  if v_cycle_key is null then
    v_cycle_key := nullif(new.payload#>>'{intent,payload,cycle_key}','');
  end if;
  if v_cycle_key is null or coalesce(new.payload#>>'{signal,signal_id}','') = '' then
    return new;
  end if;
  select c.candidate_id into v_candidate_id
  from private.trading_candidate_evaluations c
  join private.trading_scan_cycles sc on sc.scan_cycle_id=c.scan_cycle_id
  where sc.cycle_key=v_cycle_key
    and c.symbol=upper(new.payload#>>'{signal,symbol}')
  limit 1;
  if v_candidate_id is not null then
    update private.trading_signals
    set candidate_id=v_candidate_id,
        payload=payload || jsonb_build_object('cycle_key',v_cycle_key)
    where signal_id=(new.payload#>>'{signal,signal_id}')::uuid;
  end if;
  return new;
end;
$$;
drop trigger if exists trg_link_signal_candidate on private.trading_events;
create trigger trg_link_signal_candidate
after insert on private.trading_events
for each row when (new.event_type='order_intent')
execute function private.link_signal_candidate_from_cycle();
