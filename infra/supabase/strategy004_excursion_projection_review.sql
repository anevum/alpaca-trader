-- Strategy 004 data-integrity review artifact.
-- REVIEW ONLY: do not apply to production until the offline branch passes CI
-- and the Edge Function identity/ingest changes have been reviewed.
--
-- Purpose:
-- 1) Persist live peak/trough return telemetry into trading_positions as
--    max_favorable_excursion / max_adverse_excursion.
-- 2) Work for software exits and broker-side protective-stop exits by
--    projecting from every account snapshot while the position is observable.

begin;

create or replace function private.project_position_excursions_from_snapshot()
returns trigger
language plpgsql
security invoker
set search_path = private, pg_catalog
as $$
declare
  metric record;
  peak_value numeric;
  trough_value numeric;
  target_position_id uuid;
begin
  if new.run_id is null then
    return new;
  end if;

  for metric in
    select key as symbol, value as payload
    from jsonb_each(coalesce(new.payload -> 'position_metrics', '{}'::jsonb))
  loop
    begin
      peak_value := nullif(metric.payload ->> 'peak_return_pct', '')::numeric;
      trough_value := nullif(metric.payload ->> 'trough_return_pct', '')::numeric;
    exception
      when invalid_text_representation then
        -- Malformed noncritical excursion telemetry must not corrupt the
        -- canonical order/fill ledger.
        peak_value := null;
        trough_value := null;
    end;

    if peak_value is null and trough_value is null then
      continue;
    end if;

    select p.position_id
      into target_position_id
    from private.trading_positions p
    where p.run_id = new.run_id
      and upper(p.symbol) = upper(metric.symbol)
      and p.opened_at <= new.observed_at
    order by p.opened_at desc
    limit 1;

    if target_position_id is null then
      continue;
    end if;

    update private.trading_positions p
    set
      max_favorable_excursion = case
        when peak_value is null then p.max_favorable_excursion
        when p.max_favorable_excursion is null then peak_value
        else greatest(p.max_favorable_excursion, peak_value)
      end,
      max_adverse_excursion = case
        when trough_value is null then p.max_adverse_excursion
        when p.max_adverse_excursion is null then trough_value
        else least(p.max_adverse_excursion, trough_value)
      end
    where p.position_id = target_position_id;
  end loop;

  return new;
end;
$$;

drop trigger if exists trading_account_snapshot_excursions
  on private.trading_account_snapshots;

create trigger trading_account_snapshot_excursions
after insert on private.trading_account_snapshots
for each row
execute function private.project_position_excursions_from_snapshot();

commit;
