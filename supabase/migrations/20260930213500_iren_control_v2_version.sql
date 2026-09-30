create or replace function private.iren_control_commit(p jsonb)
returns jsonb
language plpgsql
set search_path to 'private', 'pg_temp'
as $function$
declare r private.iren_control_state%rowtype; e jsonb;
begin
  select * into r from private.iren_control_state where singleton for update;
  if r.observation_key = p->>'observation_key' then
    return jsonb_build_object('committed',true,'idempotent',true,'revision',r.revision);
  end if;
  if r.revision <> (p->>'expected_revision')::bigint then
    return jsonb_build_object('committed',false,'conflict',true,'revision',r.revision);
  end if;
  if p->'state' is null or p->'state'->>'observed_at' is null
     or p->>'observation_key' is null or p->>'expected_revision' is null
     or jsonb_typeof(p->'state') <> 'object'
     or coalesce(p->'state'->>'version','') not in ('iren-control-v1.0.0','iren-control-v2.0.0')
     or length(p->>'observation_key') <> 64
     or (p->'state'->>'observed_at')::timestamptz > now() + interval '30 seconds'
     or (r.state->>'observed_at' is not null and
         (p->'state'->>'observed_at')::timestamptz <= (r.state->>'observed_at')::timestamptz) then
    raise exception 'invalid_iren_observation';
  end if;
  update private.iren_control_state set revision = revision + 1,
    observation_key = p->>'observation_key', state = p->'state', updated_at = now()
    where singleton returning * into r;
  for e in select value from jsonb_array_elements(coalesce(p->'events','[]'::jsonb)) loop
    if e->>'transition' not in ('OPEN','ESCALATED','RECOVERED')
       or e->>'route' <> 'iren-control' or length(e->>'event_key') <> 64 then
      raise exception 'invalid_iren_event';
    end if;
    insert into private.iren_control_events(event_key,revision,event)
      values(e->>'event_key',r.revision,e) on conflict do nothing;
  end loop;
  return jsonb_build_object('committed',true,'revision',r.revision);
end;
$function$;
