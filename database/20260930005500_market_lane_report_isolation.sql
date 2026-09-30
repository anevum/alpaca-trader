-- Isolate legacy equity research/report SQL from RHEN's continuous crypto lane.
-- This is an additive performance/correctness hotfix: crypto evidence remains in
-- the canonical tables but legacy equity report functions exclude it explicitly.

do $$
declare
  v_definition text;
  v_patched text;
begin
  select pg_get_functiondef(p.oid)
  into v_definition
  from pg_proc p
  join pg_namespace n on n.oid=p.pronamespace
  where n.nspname='private'
    and p.proname='rhen_post_event_evidence_inputs'
  limit 1;

  if v_definition is null then
    raise exception 'rhen_post_event_evidence_inputs not found';
  end if;

  v_patched := replace(
    v_definition,
    'where c.observed_at>=b.starts_at and c.observed_at<b.ends_at',
    'where c.observed_at>=b.starts_at and c.observed_at<b.ends_at
     and not (
       coalesce(c.market_lane, '''')=''crypto''
       or c.strategy_version_id like ''CRYPTO-%''
       or c.features->>''market''=''crypto''
     )'
  );
  if v_patched = v_definition then
    raise exception 'rhen_post_event_evidence_inputs patch anchor not found';
  end if;
  execute v_patched;

  select pg_get_functiondef(p.oid)
  into v_definition
  from pg_proc p
  join pg_namespace n on n.oid=p.pronamespace
  where n.nspname='private'
    and p.proname='rhen_weekly_report_inputs'
  limit 1;

  if v_definition is null then
    raise exception 'rhen_weekly_report_inputs not found';
  end if;

  v_patched := replace(
    v_definition,
    'where c.observed_at>=b.starts_at and c.observed_at<b.ends_at',
    'where c.observed_at>=b.starts_at and c.observed_at<b.ends_at
     and not (
       coalesce(c.market_lane, '''')=''crypto''
       or c.strategy_version_id like ''CRYPTO-%''
       or c.features->>''market''=''crypto''
     )'
  );
  if v_patched = v_definition then
    raise exception 'rhen_weekly_report_inputs patch anchor not found';
  end if;
  execute v_patched;

  select pg_get_functiondef(p.oid)
  into v_definition
  from pg_proc p
  join pg_namespace n on n.oid=p.pronamespace
  where n.nspname='private'
    and p.proname='rhen_ads002_v2_daily_inputs'
  limit 1;

  if v_definition is null then
    raise exception 'rhen_ads002_v2_daily_inputs not found';
  end if;

  v_patched := replace(
    v_definition,
    'where (observed_at at time zone ''America/New_York'')::date=p_session',
    'where (observed_at at time zone ''America/New_York'')::date=p_session
    and not (
      coalesce(market_lane, '''')=''crypto''
      or strategy_version_id like ''CRYPTO-%''
      or features->>''market''=''crypto''
    )'
  );
  if v_patched = v_definition then
    raise exception 'rhen_ads002_v2_daily_inputs patch anchor not found';
  end if;
  execute v_patched;
end;
$$;

create index if not exists trading_candidate_crypto_observed_idx
  on private.trading_candidate_evaluations(observed_at,candidate_id)
  where market_lane='crypto';

create index if not exists trading_candidate_equity_observed_idx
  on private.trading_candidate_evaluations(observed_at,candidate_id)
  where market_lane='us_equity';

create index if not exists trading_forward_crypto_v1_candidate_idx
  on private.trading_candidate_forward_outcomes(candidate_id,horizon_minutes,status)
  where methodology_version='candidate-forward-crypto-v1';
