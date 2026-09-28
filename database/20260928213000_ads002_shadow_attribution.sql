-- ADS-002 v1: research-only attribution and shadow-score persistence.
-- Additive only. No live strategy, risk, sizing, broker, or capital behavior changes.

create table if not exists private.trading_ads_attribution (
  attribution_id bigint generated always as identity primary key,
  candidate_id bigint references private.trading_candidate_evaluations(candidate_id) on delete cascade,
  candidate_key text,
  signal_id uuid references private.trading_signals(signal_id),
  intent_id uuid references private.trading_order_intents(intent_id),
  entry_order_id text references private.trading_orders(broker_order_id),
  entry_fill_id bigint references private.trading_fills(fill_id),
  position_id uuid references private.trading_positions(position_id),
  exit_id uuid references private.trading_exits(exit_id),
  exit_order_id text references private.trading_orders(broker_order_id),
  exit_fill_id bigint references private.trading_fills(fill_id),
  attribution_state text not null
    check (attribution_state in (
      'DIRECT_COMPLETE',
      'DIRECT_PARTIAL',
      'HISTORICAL_PROVISIONAL',
      'AMBIGUOUS',
      'UNLINKED'
    )),
  link_method text not null,
  link_confidence numeric not null default 0
    check (link_confidence >= 0 and link_confidence <= 1),
  reason_codes text[] not null default '{}'::text[],
  methodology_version text not null,
  session date not null,
  computed_at timestamptz not null default now(),
  details jsonb not null default '{}'::jsonb
);

create unique index if not exists trading_ads_attribution_candidate_key_method_uidx
  on private.trading_ads_attribution(candidate_key, methodology_version)
  where candidate_key is not null;

create index if not exists trading_ads_attribution_candidate_idx
  on private.trading_ads_attribution(candidate_id)
  where candidate_id is not null;

create index if not exists trading_ads_attribution_signal_idx
  on private.trading_ads_attribution(signal_id)
  where signal_id is not null;

create index if not exists trading_ads_attribution_session_state_idx
  on private.trading_ads_attribution(session, attribution_state);

create table if not exists private.trading_ads_shadow_scores (
  score_id bigint generated always as identity primary key,
  candidate_id bigint not null
    references private.trading_candidate_evaluations(candidate_id) on delete cascade,
  candidate_key text not null,
  run_id uuid not null references private.trading_runs(run_id),
  strategy_version_id text not null
    references private.trading_strategy_versions(version_id),
  session date not null,
  symbol text not null,
  observed_at timestamptz not null,
  attention_score numeric check (attention_score between 0 and 100),
  qualification_score numeric check (qualification_score between 0 and 100),
  timing_score numeric check (timing_score between 0 and 100),
  exit_health_score numeric check (exit_health_score between 0 and 100),
  confidence_score numeric check (confidence_score between 0 and 100),
  pretrade_composite numeric check (pretrade_composite between 0 and 100),
  legacy_quality_score numeric,
  feature_vector jsonb not null default '{}'::jsonb,
  score_components jsonb not null default '{}'::jsonb,
  source_completeness jsonb not null default '{}'::jsonb,
  attribution_state text not null,
  methodology_version text not null,
  forward_methodology_version text,
  computed_at timestamptz not null default now(),
  unique(candidate_id, methodology_version)
);

create index if not exists trading_ads_shadow_scores_session_idx
  on private.trading_ads_shadow_scores(session, observed_at);

create index if not exists trading_ads_shadow_scores_symbol_idx
  on private.trading_ads_shadow_scores(symbol, session);

create index if not exists trading_ads_shadow_scores_attribution_idx
  on private.trading_ads_shadow_scores(attribution_state, session);

alter table private.trading_ads_attribution enable row level security;
alter table private.trading_ads_shadow_scores enable row level security;

revoke all on private.trading_ads_attribution from anon, authenticated;
revoke all on private.trading_ads_shadow_scores from anon, authenticated;

comment on table private.trading_ads_attribution is
'ADS-002 research-only durable attribution audit. Never consumed by live execution.';

comment on table private.trading_ads_shadow_scores is
'ADS-002 research-only A/Q/T/X/C shadow scores. Never consumed by live execution.';

-- Persist the selected candidate on the same synchronous critical order_intent
-- event that already must be acknowledged before broker submission.
-- This removes executed-entry attribution's dependency on the later
-- noncritical decision_cycle transport while preserving the full population
-- in the decision_cycle stream.
create or replace function private.project_ads002_selected_candidate()
returns trigger
language plpgsql
set search_path = private, pg_temp
as $$
declare
  v_snapshot jsonb;
  v_cycle_key text;
  v_candidate_key text;
  v_signal_id uuid;
  v_scan_cycle_id bigint;
  v_candidate_id bigint;
begin
  if new.event_type <> 'order_intent' then
    return new;
  end if;

  v_snapshot := coalesce(new.payload#>'{intent,payload,candidate_snapshot}', '{}'::jsonb);
  v_cycle_key := nullif(new.payload#>>'{intent,payload,cycle_key}', '');
  v_candidate_key := nullif(new.payload#>>'{intent,payload,candidate_key}', '');
  v_signal_id := nullif(new.payload#>>'{signal,signal_id}', '')::uuid;

  if v_cycle_key is null
     or v_candidate_key is null
     or v_signal_id is null
     or jsonb_typeof(v_snapshot) <> 'object'
     or coalesce(v_snapshot->>'symbol','') = '' then
    return new;
  end if;

  insert into private.trading_scan_cycles (
    cycle_key,
    run_id,
    strategy_version_id,
    observed_at,
    cycle_started_at,
    market_is_open,
    symbol_count,
    candidate_count,
    qualified_count,
    rejected_count,
    cycle_outcome,
    data_status,
    degraded,
    metadata
  ) values (
    v_cycle_key,
    new.run_id,
    new.strategy_version_id,
    coalesce(nullif(v_snapshot->>'observed_at','')::timestamptz, new.occurred_at),
    coalesce(nullif(v_snapshot->>'observed_at','')::timestamptz, new.occurred_at),
    true,
    1,
    1,
    1,
    0,
    'critical_selected_candidate_persisted',
    'partial_until_decision_cycle',
    false,
    jsonb_build_object(
      'ads002_prebroker_identity', true,
      'telemetry_source', 'order_intent'
    )
  )
  on conflict (cycle_key) where cycle_key is not null do update
  set
    run_id = coalesce(private.trading_scan_cycles.run_id, excluded.run_id),
    strategy_version_id = coalesce(
      private.trading_scan_cycles.strategy_version_id,
      excluded.strategy_version_id
    ),
    observed_at = least(private.trading_scan_cycles.observed_at, excluded.observed_at),
    metadata = private.trading_scan_cycles.metadata || excluded.metadata
  returning scan_cycle_id into v_scan_cycle_id;

  insert into private.trading_candidate_evaluations (
    scan_cycle_id,
    run_id,
    strategy_version_id,
    symbol,
    observed_at,
    action,
    qualified,
    reason,
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
    research_attribution,
    candidate_key,
    qualification_status,
    final_decision,
    rejection_reason_codes,
    market_context,
    constraints,
    strategy_family,
    methodology_version,
    data_source,
    data_feed,
    bar_interval,
    signal_id
  ) values (
    v_scan_cycle_id,
    new.run_id,
    new.strategy_version_id,
    upper(v_snapshot->>'symbol'),
    coalesce(nullif(v_snapshot->>'observed_at','')::timestamptz, new.occurred_at),
    coalesce(nullif(v_snapshot->>'action',''), 'buy'),
    true,
    nullif(v_snapshot->>'reason',''),
    coalesce(nullif(v_snapshot->>'candidate_state',''), 'qualified'),
    '[]'::jsonb,
    'available',
    nullif(v_snapshot->>'decision_reference_price','')::numeric,
    nullif(v_snapshot#>>'{quote,bid}','')::numeric,
    nullif(v_snapshot#>>'{quote,ask}','')::numeric,
    nullif(v_snapshot#>>'{quote,midpoint}','')::numeric,
    nullif(v_snapshot#>>'{quote,spread_pct}','')::numeric,
    nullif(v_snapshot#>>'{quote,observed_at}','')::timestamptz,
    coalesce(v_snapshot->'features','{}'::jsonb),
    coalesce(v_snapshot->'checks','{}'::jsonb),
    '{}'::jsonb,
    'pending',
    jsonb_build_object(
      'live_strategy_version', new.strategy_version_id,
      'ads002_identity_source', 'critical_order_intent',
      'identity_is_direct', true
    ),
    v_candidate_key,
    coalesce(nullif(v_snapshot->>'qualification_status',''), 'qualified'),
    coalesce(nullif(v_snapshot->>'final_decision',''), 'selected_for_entry'),
    '{}'::text[],
    coalesce(v_snapshot->'market_context','{}'::jsonb),
    coalesce(v_snapshot->'constraints','{}'::jsonb),
    nullif(v_snapshot->>'strategy_family',''),
    nullif(v_snapshot->>'methodology_version',''),
    nullif(v_snapshot->>'data_source',''),
    nullif(v_snapshot->>'data_feed',''),
    nullif(v_snapshot->>'bar_interval',''),
    v_signal_id
  )
  on conflict (scan_cycle_id, symbol) do update
  set
    candidate_key = coalesce(
      private.trading_candidate_evaluations.candidate_key,
      excluded.candidate_key
    ),
    signal_id = coalesce(
      private.trading_candidate_evaluations.signal_id,
      excluded.signal_id
    ),
    qualified = private.trading_candidate_evaluations.qualified or excluded.qualified,
    action = case
      when excluded.qualified then excluded.action
      else private.trading_candidate_evaluations.action
    end,
    reason = coalesce(
      private.trading_candidate_evaluations.reason,
      excluded.reason
    ),
    decision_reference_price = coalesce(
      private.trading_candidate_evaluations.decision_reference_price,
      excluded.decision_reference_price
    ),
    decision_bid = coalesce(
      private.trading_candidate_evaluations.decision_bid,
      excluded.decision_bid
    ),
    decision_ask = coalesce(
      private.trading_candidate_evaluations.decision_ask,
      excluded.decision_ask
    ),
    decision_midpoint = coalesce(
      private.trading_candidate_evaluations.decision_midpoint,
      excluded.decision_midpoint
    ),
    observed_spread = coalesce(
      private.trading_candidate_evaluations.observed_spread,
      excluded.observed_spread
    ),
    quote_observed_at = coalesce(
      private.trading_candidate_evaluations.quote_observed_at,
      excluded.quote_observed_at
    ),
    features = private.trading_candidate_evaluations.features || excluded.features,
    checks = private.trading_candidate_evaluations.checks || excluded.checks,
    research_attribution =
      private.trading_candidate_evaluations.research_attribution
      || excluded.research_attribution
  returning candidate_id into v_candidate_id;

  update private.trading_signals
  set
    candidate_id = coalesce(candidate_id, v_candidate_id),
    payload = payload || jsonb_build_object(
      'candidate_key', v_candidate_key,
      'cycle_key', v_cycle_key,
      'ads002_identity_source', 'critical_order_intent'
    )
  where signal_id = v_signal_id;

  return new;
end;
$$;

drop trigger if exists trg_ads002_selected_candidate on private.trading_events;
create trigger trg_ads002_selected_candidate
after insert on private.trading_events
for each row
when (new.event_type = 'order_intent')
execute function private.project_ads002_selected_candidate();

comment on function private.project_ads002_selected_candidate() is
'ADS-002 telemetry-only projection: persists exact selected candidate identity from the already-critical pre-broker order_intent event.';

-- Read-only chain audit used by the post-close research pipeline.
create or replace function private.rhen_ads002_attribution_inputs(
  p_start date,
  p_end date
)
returns jsonb
language sql
stable
security invoker
set search_path = private, pg_temp
as $$
with bounds as (
  select
    (p_start::timestamp at time zone 'America/New_York') as starts_at,
    ((p_end + 1)::timestamp at time zone 'America/New_York') as ends_at
),
candidates as (
  select c.*
  from private.trading_candidate_evaluations c, bounds b
  where c.observed_at >= b.starts_at and c.observed_at < b.ends_at
),
chains as (
  select
    c.candidate_id,
    c.candidate_key,
    c.run_id,
    c.strategy_version_id,
    c.symbol,
    c.observed_at,
    c.signal_id,
    s.payload->>'cycle_key' as signal_cycle_key,
    i.intent_id,
    o.broker_order_id as entry_order_id,
    f.fill_id as entry_fill_id,
    p.position_id,
    p.status as position_status,
    p.closed_at,
    p.realized_return,
    p.max_favorable_excursion,
    p.max_adverse_excursion,
    x.exit_id,
    x.broker_order_id as exit_order_id,
    xf.fill_id as exit_fill_id
  from candidates c
  left join private.trading_signals s
    on s.signal_id = c.signal_id
  left join private.trading_order_intents i
    on i.signal_id = s.signal_id and lower(i.side) = 'buy'
  left join private.trading_orders o
    on o.order_intent_id = i.intent_id and lower(o.side) = 'buy'
  left join private.trading_fills f
    on f.broker_order_id = o.broker_order_id and lower(f.side) = 'buy'
  left join private.trading_positions p
    on p.payload->>'entry_order_id' = o.broker_order_id
  left join private.trading_exits x
    on x.position_id = p.position_id
  left join private.trading_fills xf
    on xf.broker_order_id = x.broker_order_id and lower(xf.side) = 'sell'
)
select jsonb_build_object(
  'period', jsonb_build_object('start', p_start, 'end', p_end),
  'candidate_count', (select count(*) from candidates),
  'chains', coalesce(
    (
      select jsonb_agg(to_jsonb(chains) order by observed_at, symbol)
      from chains
    ),
    '[]'::jsonb
  )
);
$$;

revoke all on function private.rhen_ads002_attribution_inputs(date,date)
from public, anon, authenticated;

comment on function private.rhen_ads002_attribution_inputs(date,date) is
'Private read-only ADS-002 attribution inputs. No live trading dependency.';
