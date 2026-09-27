-- Separate support incidents from trading_incidents, whose rows feed weekly
-- operational reporting. This table has no execution, strategy, or broker hook.
create table private.rhen_agent_support_alerts (
  alert_key text primary key,
  reason_code text not null,
  severity text not null check (severity in ('warning', 'critical')),
  source_component text not null,
  evidence_reference text not null default '',
  state text not null check (state in ('OPEN', 'RESOLVED')),
  first_observed_at timestamptz not null,
  last_observed_at timestamptz not null,
  resolved_at timestamptz,
  constraint support_alert_time_order check (
    last_observed_at >= first_observed_at and
    (resolved_at is null or resolved_at >= last_observed_at)
  ),
  constraint support_alert_resolution check (
    (state = 'OPEN' and resolved_at is null) or
    (state = 'RESOLVED' and resolved_at is not null)
  ),
  constraint support_alert_key_length check (length(alert_key) between 3 and 500)
);

create index rhen_agent_support_alerts_open_idx
  on private.rhen_agent_support_alerts (severity, last_observed_at desc)
  where state = 'OPEN';

alter table private.rhen_agent_support_alerts enable row level security;
revoke all on private.rhen_agent_support_alerts from public, anon, authenticated;
-- No Data API grants or SECURITY DEFINER functions. A dedicated server-side
-- connection may be granted the minimal table privileges during activation.

create function private.rhen_agent_support_apply_actions(p_actions jsonb)
returns integer
language plpgsql
security invoker
set search_path = private, pg_temp
as $function$
declare
  action jsonb;
  applied integer := 0;
begin
  if jsonb_typeof(p_actions) <> 'array' or jsonb_array_length(p_actions) > 200 then
    raise exception 'bounded action array required';
  end if;
  for action in select value from jsonb_array_elements(p_actions) loop
    if action->>'state' = 'OPEN' then
      if coalesce(action->>'alert_key','') = '' or
         coalesce(action->>'reason_code','') = '' or
         coalesce(action->>'source_component','') = '' or
         action->>'severity' not in ('critical','warning') then
        raise exception 'invalid support alert';
      end if;
      insert into private.rhen_agent_support_alerts as a (
        alert_key, reason_code, severity, source_component, evidence_reference,
        state, first_observed_at, last_observed_at
      ) values (
        action->>'alert_key', action->>'reason_code', action->>'severity',
        action->>'source_component', coalesce(action->>'evidence_reference',''),
        'OPEN', (action->>'first_observed_at')::timestamptz,
        (action->>'last_observed_at')::timestamptz
      ) on conflict (alert_key) do update set
        reason_code = excluded.reason_code,
        severity = excluded.severity,
        source_component = excluded.source_component,
        evidence_reference = excluded.evidence_reference,
        first_observed_at = case when a.state = 'OPEN'
          then a.first_observed_at else excluded.first_observed_at end,
        last_observed_at = greatest(a.last_observed_at, excluded.last_observed_at),
        state = 'OPEN', resolved_at = null;
      applied := applied + 1;
    elsif action->>'state' = 'RESOLVED' then
      update private.rhen_agent_support_alerts
      set state = 'RESOLVED', resolved_at = (action->>'resolved_at')::timestamptz
      where alert_key = action->>'alert_key' and state = 'OPEN'
        and (action->>'resolved_at')::timestamptz >= last_observed_at;
      applied := applied + 1;
    else
      raise exception 'invalid support action state';
    end if;
  end loop;
  return applied;
end
$function$;

revoke all on function private.rhen_agent_support_apply_actions(jsonb)
  from public, anon, authenticated;
