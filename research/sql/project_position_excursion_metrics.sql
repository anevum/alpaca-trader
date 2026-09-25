create or replace function private.project_position_metrics_event()
returns trigger
language plpgsql
set search_path = private, pg_temp
as $$
declare
  v_position_id uuid;
  v_mfe numeric;
  v_mae numeric;
begin
  if new.event_type <> 'position_metrics' or new.symbol is null then
    return new;
  end if;

  v_mfe := nullif(new.payload->>'max_favorable_excursion', '')::numeric;
  v_mae := nullif(new.payload->>'max_adverse_excursion', '')::numeric;

  select position_id
    into v_position_id
  from private.trading_positions
  where symbol = upper(new.symbol)
    and opened_at <= new.occurred_at
    and (closed_at is null or closed_at >= new.occurred_at)
  order by opened_at desc
  limit 1;

  if v_position_id is not null then
    update private.trading_positions
    set
      max_favorable_excursion = case
        when v_mfe is null then max_favorable_excursion
        when max_favorable_excursion is null then v_mfe
        else greatest(max_favorable_excursion, v_mfe)
      end,
      max_adverse_excursion = case
        when v_mae is null then max_adverse_excursion
        when max_adverse_excursion is null then v_mae
        else least(max_adverse_excursion, v_mae)
      end,
      payload = coalesce(payload, '{}'::jsonb)
        || jsonb_build_object('excursion_metrics', new.payload)
    where position_id = v_position_id;
  end if;

  return new;
end;
$$;

drop trigger if exists trg_project_position_metrics
on private.trading_events;

create trigger trg_project_position_metrics
after insert on private.trading_events
for each row
when (new.event_type = 'position_metrics')
execute function private.project_position_metrics_event();
