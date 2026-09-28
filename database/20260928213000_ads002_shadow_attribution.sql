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

-- Complete the candidate<->signal identity repair in both insertion orders.
-- The signal row may be projected after the order_intent event trigger has already
-- persisted the candidate. This BEFORE trigger fills candidate_id by exact
-- candidate_key only; it never time-matches or mutates execution behavior.
create or replace function private.link_ads002_signal_candidate_by_key()
returns trigger
language plpgsql
set search_path = private, pg_temp
as $$
declare
  v_candidate_key text;
  v_candidate_id bigint;
begin
  v_candidate_key := nullif(new.payload->>'candidate_key','');
  if v_candidate_key is null then
    return new;
  end if;

  select candidate_id
  into v_candidate_id
  from private.trading_candidate_evaluations
  where candidate_key = v_candidate_key;

  if v_candidate_id is null then
    return new;
  end if;

  if new.candidate_id is null then
    new.candidate_id := v_candidate_id;
  elsif new.candidate_id <> v_candidate_id then
    new.payload := coalesce(new.payload,'{}'::jsonb)
      || jsonb_build_object(
        'ads002_identity_conflict', true,
        'ads002_expected_candidate_id', v_candidate_id
      );
    return new;
  end if;

  update private.trading_candidate_evaluations
  set signal_id = coalesce(signal_id, new.signal_id),
      research_attribution = research_attribution || jsonb_build_object(
        'ads002_identity_source', 'candidate_key_signal_trigger',
        'identity_is_direct', true
      )
  where candidate_id = v_candidate_id
    and (signal_id is null or signal_id = new.signal_id);

  return new;
end;
$$;

drop trigger if exists trg_ads002_link_signal_candidate_by_key
on private.trading_signals;

create trigger trg_ads002_link_signal_candidate_by_key
before insert or update of payload, candidate_id
on private.trading_signals
for each row
execute function private.link_ads002_signal_candidate_by_key();

comment on function private.link_ads002_signal_candidate_by_key() is
'ADS-002 exact-key candidate/signal linker. No fuzzy or nearest-time attribution.';

-- Project deterministic pretrade ADS-002 scores only after the canonical
-- decision-cycle candidate rows have been created/enriched.
create or replace function private.project_ads002_shadow_scores()
returns trigger
language plpgsql
set search_path = private, pg_temp
as $$
declare
  v_cycle_key text;
  v_scan_cycle_id bigint;
  v_candidate jsonb;
  v_ads jsonb;
  v_candidate_id bigint;
  v_candidate_key text;
  v_observed_at timestamptz;
  v_symbol text;
  v_signal_id uuid;
begin
  if new.event_type <> 'decision_cycle' then
    return new;
  end if;

  v_cycle_key := nullif(new.payload->>'cycle_key','');
  if v_cycle_key is null
     or jsonb_typeof(new.payload->'candidates') <> 'array' then
    return new;
  end if;

  select scan_cycle_id
  into v_scan_cycle_id
  from private.trading_scan_cycles
  where cycle_key = v_cycle_key;

  if v_scan_cycle_id is null then
    return new;
  end if;

  for v_candidate in
    select value from jsonb_array_elements(new.payload->'candidates')
  loop
    v_ads := coalesce(v_candidate->'ads002','{}'::jsonb);
    if jsonb_typeof(v_ads) <> 'object'
       or coalesce(v_ads->>'methodology_version','') = '' then
      continue;
    end if;

    v_symbol := upper(v_candidate->>'symbol');
    v_candidate_key := coalesce(
      nullif(v_candidate->>'candidate_key',''),
      v_cycle_key || ':' || v_symbol
    );

    select candidate_id, observed_at, signal_id
    into v_candidate_id, v_observed_at, v_signal_id
    from private.trading_candidate_evaluations
    where scan_cycle_id = v_scan_cycle_id
      and symbol = v_symbol;

    if v_candidate_id is null then
      continue;
    end if;

    insert into private.trading_ads_shadow_scores (
      candidate_id,
      candidate_key,
      run_id,
      strategy_version_id,
      session,
      symbol,
      observed_at,
      attention_score,
      qualification_score,
      timing_score,
      exit_health_score,
      confidence_score,
      pretrade_composite,
      legacy_quality_score,
      feature_vector,
      score_components,
      source_completeness,
      attribution_state,
      methodology_version,
      forward_methodology_version,
      computed_at
    ) values (
      v_candidate_id,
      v_candidate_key,
      new.run_id,
      new.strategy_version_id,
      (v_observed_at at time zone 'America/New_York')::date,
      v_symbol,
      v_observed_at,
      nullif(v_ads#>>'{attention,score}','')::numeric,
      nullif(v_ads#>>'{qualification,score}','')::numeric,
      nullif(v_ads#>>'{timing,score}','')::numeric,
      null,
      null,
      nullif(v_ads->>'pretrade_composite','')::numeric,
      nullif(v_ads->>'legacy_quality_score','')::numeric,
      coalesce(v_ads->'feature_vector','{}'::jsonb),
      jsonb_build_object(
        'attention', v_ads->'attention',
        'qualification', v_ads->'qualification',
        'timing', v_ads->'timing'
      ),
      coalesce(v_ads->'source_completeness','{}'::jsonb),
      case
        when v_signal_id is null then 'CANDIDATE_ONLY'
        else 'DIRECT_PARTIAL'
      end,
      v_ads->>'methodology_version',
      'candidate-forward-v2',
      now()
    )
    on conflict (candidate_id, methodology_version) do update
    set
      candidate_key = excluded.candidate_key,
      attention_score = excluded.attention_score,
      qualification_score = excluded.qualification_score,
      timing_score = excluded.timing_score,
      pretrade_composite = excluded.pretrade_composite,
      legacy_quality_score = excluded.legacy_quality_score,
      feature_vector = excluded.feature_vector,
      score_components = excluded.score_components,
      source_completeness = excluded.source_completeness,
      attribution_state = case
        when private.trading_ads_shadow_scores.attribution_state = 'DIRECT_COMPLETE'
          then private.trading_ads_shadow_scores.attribution_state
        else excluded.attribution_state
      end,
      forward_methodology_version = excluded.forward_methodology_version,
      computed_at = excluded.computed_at;
  end loop;

  return new;
end;
$$;

drop trigger if exists trg_zzz_ads002_shadow_scores
on private.trading_events;

create trigger trg_zzz_ads002_shadow_scores
after insert on private.trading_events
for each row
when (new.event_type = 'decision_cycle')
execute function private.project_ads002_shadow_scores();

comment on function private.project_ads002_shadow_scores() is
'Projects research-only ADS-002 A/Q/T shadow scores from decision-cycle telemetry.';

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


create unique index if not exists trading_ads_attribution_signal_method_uidx
  on private.trading_ads_attribution(signal_id, methodology_version)
  where signal_id is not null;

create or replace function private.ads002_exit_health(
  p_realized_return numeric,
  p_mfe numeric,
  p_mae numeric
)
returns numeric
language sql
immutable
set search_path = private, pg_temp
as $ads002$
  select case
    when p_mfe is null or p_mae is null or p_realized_return is null then null
    else least(
      100::numeric,
      greatest(
        0::numeric,
        100::numeric * (
          0.45::numeric * greatest(
            0::numeric,
            least(
              1::numeric,
              (
                p_realized_return - least(p_mae, 0::numeric)
              ) / greatest(
                greatest(p_mfe, 0::numeric) - least(p_mae, 0::numeric),
                0.000001::numeric
              )
            )
          )
          + 0.35::numeric * (
            case
              when greatest(p_mfe, 0::numeric) > 0 then greatest(
                0::numeric,
                least(
                  1::numeric,
                  p_realized_return / greatest(p_mfe, 0::numeric)
                )
              )
              when p_realized_return >= 0 then 1::numeric
              else 0::numeric
            end
          )
          + 0.20::numeric * (
            1::numeric - greatest(
              0::numeric,
              least(
                1::numeric,
                abs(least(p_realized_return, 0::numeric))
                / greatest(abs(least(p_mae, 0::numeric)), 0.000001::numeric)
              )
            )
          )
        )
      )
    )
  end;
$ads002$;

revoke all on function private.ads002_exit_health(numeric,numeric,numeric)
from public, anon, authenticated;

create or replace function private.rhen_ads002_confidence_state()
returns jsonb
language sql
stable
security invoker
set search_path = private, pg_temp
as $ads002$
with counts as (
  select
    count(distinct s.session)::integer as independent_sessions,
    count(*) filter (
      where s.pretrade_composite is not null
    )::integer as research_eligible_candidates
  from private.trading_ads_shadow_scores s
  where s.methodology_version='ads-shadow-v1'
),
trade_counts as (
  select
    count(*) filter (
      where a.attribution_state='DIRECT_COMPLETE'
    )::integer as closed_direct_trades,
    count(*) filter (
      where a.signal_id is not null
    )::integer as executable_signals,
    count(*) filter (
      where a.signal_id is not null
        and a.attribution_state in ('DIRECT_COMPLETE','DIRECT_PARTIAL')
    )::integer as directly_attributed_signals
  from private.trading_ads_attribution a
  where a.methodology_version='ads-attribution-v1'
),
completion as (
  select
    count(*)::integer as eligible,
    count(*) filter (
      where exists (
        select 1
        from private.trading_candidate_forward_outcomes fo
        where fo.candidate_id=s.candidate_id
          and fo.methodology_version='candidate-forward-v2'
          and fo.horizon_minutes=15
          and fo.status='complete'
      )
    )::integer as forward_complete
  from private.trading_ads_shadow_scores s
  where s.methodology_version='ads-shadow-v1'
    and s.pretrade_composite is not null
),
primary_rows as (
  select
    s.session,
    s.candidate_id,
    s.qualification_score::double precision as q,
    fo.forward_return::double precision as r
  from private.trading_ads_shadow_scores s
  join private.trading_candidate_forward_outcomes fo
    on fo.candidate_id=s.candidate_id
   and fo.methodology_version='candidate-forward-v2'
   and fo.horizon_minutes=15
   and fo.status='complete'
  where s.methodology_version='ads-shadow-v1'
    and s.qualification_score is not null
    and fo.forward_return is not null
),
ranked as (
  select
    session,
    candidate_id,
    q,
    r,
    rank() over(partition by session order by q)::double precision as q_rank,
    rank() over(partition by session order by r)::double precision as r_rank
  from primary_rows
),
session_effects as (
  select
    session,
    count(*)::integer as n,
    corr(q_rank,r_rank) as rho
  from ranked
  group by session
),
stability as (
  select
    count(*) filter(where rho is not null)::integer as effect_sessions,
    case
      when count(*) filter(where rho is not null)=0 then 0::double precision
      else greatest(
        count(*) filter(where rho > 0)::double precision,
        count(*) filter(where rho < 0)::double precision
      ) / count(*) filter(where rho is not null)::double precision
    end as sign_consistency,
    coalesce(
      percentile_cont(0.5) within group(order by abs(rho))
        filter(where rho is not null),
      0
    )::double precision as median_abs_rho
  from session_effects
),
base as (
  select
    counts.*,
    trade_counts.*,
    completion.eligible,
    completion.forward_complete,
    stability.effect_sessions,
    stability.sign_consistency,
    stability.median_abs_rho,
    case
      when trade_counts.executable_signals=0 then 0::double precision
      else trade_counts.directly_attributed_signals::double precision
        / trade_counts.executable_signals::double precision
    end as c_attribution,
    case
      when completion.eligible=0 then 0::double precision
      else completion.forward_complete::double precision
        / completion.eligible::double precision
    end as c_completeness,
    least(counts.independent_sessions::double precision / 10.0,1.0) as sr,
    least(counts.research_eligible_candidates::double precision / 100.0,1.0) as cr,
    least(trade_counts.closed_direct_trades::double precision / 30.0,1.0) as tr,
    case
      when counts.independent_sessions < 2 then 0::double precision
      else (
        0.60 * least(greatest(stability.sign_consistency,0),1)
        + 0.40 * least(greatest(stability.median_abs_rho / 0.20,0),1)
      )
    end as c_stability
  from counts, trade_counts, completion, stability
),
scored as (
  select
    *,
    case when sr <= 0 or cr <= 0 or tr <= 0 then 0::double precision
      else power(sr*cr*tr, 1.0/3.0)
    end as c_sample
  from base
),
final as (
  select
    *,
    least(
      case
        when independent_sessions < 10
          or research_eligible_candidates < 100
          or closed_direct_trades < 30
        then 49.0
        else 100.0
      end,
      100.0 * (
        0.30 * least(greatest(c_attribution,0),1)
        + 0.25 * least(greatest(c_completeness,0),1)
        + 0.20 * least(greatest(c_sample,0),1)
        + 0.15 * least(greatest(c_stability,0),1)
        + 0.10 * 0.0
      )
    ) as confidence_score
  from scored
)
select jsonb_build_object(
  'methodology_version','ads-shadow-v1',
  'score',round(confidence_score::numeric,4),
  'hard_cap_active',(
    independent_sessions < 10
    or research_eligible_candidates < 100
    or closed_direct_trades < 30
  ),
  'components',jsonb_build_object(
    'attribution',round(c_attribution::numeric,6),
    'completeness',round(c_completeness::numeric,6),
    'sample',round(c_sample::numeric,6),
    'stability',round(c_stability::numeric,6),
    'regime',0
  ),
  'samples',jsonb_build_object(
    'independent_sessions',independent_sessions,
    'research_eligible_candidates',research_eligible_candidates,
    'closed_direct_trades',closed_direct_trades,
    'effect_sessions',effect_sessions
  ),
  'primary_effect',jsonb_build_object(
    'sign_consistency',round(sign_consistency::numeric,6),
    'median_abs_session_spearman',round(median_abs_rho::numeric,6)
  ),
  'missing_requirements',to_jsonb(array_remove(array[
    case when independent_sessions < 10 then 'MIN_INDEPENDENT_SESSIONS' end,
    case when research_eligible_candidates < 100 then 'MIN_RESEARCH_ELIGIBLE_CANDIDATES' end,
    case when closed_direct_trades < 30 then 'MIN_CLOSED_DIRECT_TRADES' end,
    'REGIME_CLASSIFIER_UNFROZEN'
  ],null))
)
from final;
$ads002$;

revoke all on function private.rhen_ads002_confidence_state()
from public, anon, authenticated;

create or replace function private.rhen_ads002_refresh_session(
  p_session date
)
returns jsonb
language plpgsql
security invoker
set search_path = private, pg_temp
as $ads002$
declare
  r record;
  v_state text;
  v_reasons text[];
  v_confidence jsonb;
begin
  for r in
    select
      s.signal_id,
      s.payload->>'candidate_key' as signal_candidate_key,
      c.candidate_id,
      c.candidate_key,
      i.intent_id,
      o.broker_order_id as entry_order_id,
      f.fill_id as entry_fill_id,
      p.position_id,
      p.status as position_status,
      p.realized_return,
      p.max_favorable_excursion,
      p.max_adverse_excursion,
      x.exit_id,
      x.broker_order_id as exit_order_id,
      xf.fill_id as exit_fill_id,
      coalesce((s.payload->>'ads002_identity_conflict')::boolean,false) as identity_conflict
    from private.trading_signals s
    left join private.trading_candidate_evaluations c
      on c.candidate_id=s.candidate_id
      or (c.signal_id=s.signal_id and s.candidate_id is null)
    left join lateral (
      select ii.intent_id
      from private.trading_order_intents ii
      where ii.signal_id=s.signal_id and lower(ii.side)='buy'
      order by ii.intended_at desc
      limit 1
    ) i on true
    left join lateral (
      select oo.broker_order_id
      from private.trading_orders oo
      where oo.order_intent_id=i.intent_id and lower(oo.side)='buy'
      order by coalesce(oo.submitted_at,oo.updated_at) desc
      limit 1
    ) o on true
    left join lateral (
      select ff.fill_id
      from private.trading_fills ff
      where ff.broker_order_id=o.broker_order_id and lower(ff.side)='buy'
      order by ff.filled_at
      limit 1
    ) f on true
    left join lateral (
      select pp.position_id,pp.status,pp.realized_return,
             pp.max_favorable_excursion,pp.max_adverse_excursion
      from private.trading_positions pp
      where pp.payload->>'entry_order_id'=o.broker_order_id
      order by pp.opened_at desc
      limit 1
    ) p on true
    left join lateral (
      select xx.exit_id,xx.broker_order_id
      from private.trading_exits xx
      where xx.position_id=p.position_id
      order by coalesce(xx.filled_at,xx.requested_at) desc
      limit 1
    ) x on true
    left join lateral (
      select ff.fill_id
      from private.trading_fills ff
      where ff.broker_order_id=x.broker_order_id and lower(ff.side)='sell'
      order by ff.filled_at desc
      limit 1
    ) xf on true
    where (s.signal_at at time zone 'America/New_York')::date=p_session
  loop
    v_reasons := '{}'::text[];
    if r.identity_conflict then
      v_state := 'AMBIGUOUS';
      v_reasons := array_append(v_reasons,'IDENTITY_CONFLICT');
    elsif r.candidate_id is null then
      v_state := 'UNLINKED';
      v_reasons := array_append(v_reasons,'CANDIDATE_ID_MISSING');
    elsif r.intent_id is null then
      v_state := 'DIRECT_PARTIAL';
      v_reasons := array_append(v_reasons,'ENTRY_INTENT_MISSING');
    elsif r.entry_order_id is null then
      v_state := 'DIRECT_PARTIAL';
      v_reasons := array_append(v_reasons,'ENTRY_ORDER_MISSING');
    elsif r.entry_fill_id is null then
      v_state := 'DIRECT_PARTIAL';
      v_reasons := array_append(v_reasons,'ENTRY_FILL_MISSING');
    elsif r.position_id is null then
      v_state := 'DIRECT_PARTIAL';
      v_reasons := array_append(v_reasons,'POSITION_MISSING');
    elsif r.position_status='closed'
      and r.exit_fill_id is not null then
      v_state := 'DIRECT_COMPLETE';
    else
      v_state := 'DIRECT_PARTIAL';
      if r.position_status='closed' and r.exit_fill_id is null then
        v_reasons := array_append(v_reasons,'EXIT_FILL_MISSING');
      else
        v_reasons := array_append(v_reasons,'POSITION_NOT_CLOSED');
      end if;
    end if;

    insert into private.trading_ads_attribution (
      candidate_id,candidate_key,signal_id,intent_id,entry_order_id,
      entry_fill_id,position_id,exit_id,exit_order_id,exit_fill_id,
      attribution_state,link_method,link_confidence,reason_codes,
      methodology_version,session,computed_at,details
    ) values (
      r.candidate_id,
      coalesce(r.candidate_key,r.signal_candidate_key),
      r.signal_id,r.intent_id,r.entry_order_id,r.entry_fill_id,
      r.position_id,r.exit_id,r.exit_order_id,r.exit_fill_id,
      v_state,
      case when r.candidate_id is null then 'none' else 'direct_id_chain' end,
      case when v_state in ('DIRECT_COMPLETE','DIRECT_PARTIAL') then 1 else 0 end,
      v_reasons,
      'ads-attribution-v1',
      p_session,
      now(),
      jsonb_build_object('postclose_refresh',true)
    )
    on conflict (signal_id,methodology_version)
      where signal_id is not null
    do update set
      candidate_id=excluded.candidate_id,
      candidate_key=excluded.candidate_key,
      intent_id=excluded.intent_id,
      entry_order_id=excluded.entry_order_id,
      entry_fill_id=excluded.entry_fill_id,
      position_id=excluded.position_id,
      exit_id=excluded.exit_id,
      exit_order_id=excluded.exit_order_id,
      exit_fill_id=excluded.exit_fill_id,
      attribution_state=excluded.attribution_state,
      link_method=excluded.link_method,
      link_confidence=excluded.link_confidence,
      reason_codes=excluded.reason_codes,
      session=excluded.session,
      computed_at=excluded.computed_at,
      details=private.trading_ads_attribution.details || excluded.details;
  end loop;

  update private.trading_ads_shadow_scores ss
  set
    attribution_state=a.attribution_state,
    exit_health_score=private.ads002_exit_health(
      p.realized_return,
      p.max_favorable_excursion,
      p.max_adverse_excursion
    ),
    computed_at=now()
  from private.trading_ads_attribution a
  left join private.trading_positions p
    on p.position_id=a.position_id
  where ss.session=p_session
    and ss.methodology_version='ads-shadow-v1'
    and a.methodology_version='ads-attribution-v1'
    and a.candidate_id=ss.candidate_id;

  v_confidence := private.rhen_ads002_confidence_state();

  update private.trading_ads_shadow_scores
  set confidence_score=nullif(v_confidence->>'score','')::numeric,
      computed_at=now()
  where session=p_session
    and methodology_version='ads-shadow-v1';

  return private.rhen_ads002_daily_inputs(p_session);
end;
$ads002$;

revoke all on function private.rhen_ads002_refresh_session(date)
from public, anon, authenticated;

create or replace function private.rhen_ads002_daily_inputs(
  p_session date
)
returns jsonb
language sql
stable
security invoker
set search_path = private, pg_temp
as $ads002$
with session_scores as (
  select *
  from private.trading_ads_shadow_scores
  where session=p_session and methodology_version='ads-shadow-v1'
),
session_attr as (
  select *
  from private.trading_ads_attribution
  where session=p_session and methodology_version='ads-attribution-v1'
),
coverage as (
  select
    (select count(*)::integer from session_scores) as score_rows,
    (select count(*)::integer from session_scores where pretrade_composite is not null) as research_eligible,
    (select count(*)::integer from session_scores where attention_score is not null) as attention_rows,
    (select count(*)::integer from session_scores where qualification_score is not null) as qualification_rows,
    (select count(*)::integer from session_scores where timing_score is not null) as timing_rows,
    (select count(*)::integer from session_attr where signal_id is not null) as executable_signals,
    (select count(*)::integer from session_attr where attribution_state in ('DIRECT_COMPLETE','DIRECT_PARTIAL')) as direct_signals,
    (select count(*)::integer from session_attr where attribution_state='AMBIGUOUS') as ambiguous_signals,
    (select count(*)::integer from session_attr where attribution_state='UNLINKED') as unlinked_signals,
    (select count(*)::integer from session_attr where attribution_state='DIRECT_COMPLETE') as closed_direct_trades,
    (
      select count(*)::integer
      from session_scores s
      where s.pretrade_composite is not null
        and exists (
          select 1
          from private.trading_candidate_forward_outcomes fo
          where fo.candidate_id=s.candidate_id
            and fo.methodology_version='candidate-forward-v2'
            and fo.horizon_minutes=15
            and fo.status='complete'
        )
    ) as forward_15_complete
),
primary_rows as (
  select
    s.session,
    s.candidate_id,
    s.qualification_score::double precision as q,
    fo.forward_return::double precision as r
  from private.trading_ads_shadow_scores s
  join private.trading_candidate_forward_outcomes fo
    on fo.candidate_id=s.candidate_id
   and fo.methodology_version='candidate-forward-v2'
   and fo.horizon_minutes=15
   and fo.status='complete'
  where s.methodology_version='ads-shadow-v1'
    and s.qualification_score is not null
    and fo.forward_return is not null
),
ranked as (
  select
    *,
    rank() over(partition by session order by q)::double precision as q_rank,
    rank() over(partition by session order by r)::double precision as r_rank,
    ntile(4) over(partition by session order by q) as q_quartile
  from primary_rows
),
per_session as (
  select
    session,
    count(*)::integer as n,
    corr(q_rank,r_rank) as rho,
    avg(r) filter(where q_quartile=4)
      - avg(r) filter(where q_quartile=1) as quartile_spread
  from ranked
  group by session
),
effect_summary as (
  select
    count(*) filter(where rho is not null)::integer as rho_sessions,
    case when count(*) filter(where rho is not null)=0 then 0::double precision
      else greatest(
        count(*) filter(where rho>0)::double precision,
        count(*) filter(where rho<0)::double precision
      ) / count(*) filter(where rho is not null)::double precision
    end as rho_sign_consistency,
    count(*) filter(where quartile_spread is not null)::integer as quartile_sessions,
    case when count(*) filter(where quartile_spread is not null)=0 then 0::double precision
      else greatest(
        count(*) filter(where quartile_spread>0)::double precision,
        count(*) filter(where quartile_spread<0)::double precision
      ) / count(*) filter(where quartile_spread is not null)::double precision
    end as quartile_sign_consistency
  from per_session
),
trade_share as (
  select coalesce(max(n)::double precision / nullif(sum(n),0),0) as max_session_share
  from (
    select session,count(*)::integer as n
    from private.trading_ads_attribution
    where methodology_version='ads-attribution-v1'
      and attribution_state='DIRECT_COMPLETE'
    group by session
  ) x
),
confidence as (
  select private.rhen_ads002_confidence_state() as value
),
derived as (
  select
    coverage.*,
    effect_summary.*,
    trade_share.max_session_share,
    confidence.value as confidence,
    case when coverage.executable_signals=0 then 1::double precision
      else coverage.direct_signals::double precision/coverage.executable_signals
    end as direct_coverage,
    case when coverage.research_eligible=0 then 0::double precision
      else coverage.forward_15_complete::double precision/coverage.research_eligible
    end as forward_coverage
  from coverage,effect_summary,trade_share,confidence
),
readiness as (
  select
    *,
    (
      ambiguous_signals=0
      and unlinked_signals=0
      and direct_coverage >= 0.995
      and forward_coverage >= 0.95
    ) as data_valid,
    (
      coalesce((confidence#>>'{samples,independent_sessions}')::integer,0) >= 10
      and coalesce((confidence#>>'{samples,research_eligible_candidates}')::integer,0) >= 100
      and coalesce((confidence#>>'{samples,closed_direct_trades}')::integer,0) >= 30
      and rho_sign_consistency >= 0.70
      and quartile_sign_consistency >= 0.70
      and max_session_share <= 0.25
      and coalesce((confidence->>'score')::numeric,0) >= 60
    ) as stable
  from derived
)
select jsonb_build_object(
  'methodology_version','ADS-002-v1',
  'score_methodology_version','ads-shadow-v1',
  'forward_methodology_version','candidate-forward-v2',
  'primary_horizon_minutes',15,
  'session',p_session,
  'attribution',jsonb_build_object(
    'executable_signals',executable_signals,
    'direct_signals',direct_signals,
    'ambiguous_signals',ambiguous_signals,
    'unlinked_signals',unlinked_signals,
    'closed_direct_trades',closed_direct_trades,
    'direct_coverage',round(direct_coverage::numeric,6)
  ),
  'score_coverage',jsonb_build_object(
    'score_rows',score_rows,
    'research_eligible_candidates',research_eligible,
    'attention_rows',attention_rows,
    'qualification_rows',qualification_rows,
    'timing_rows',timing_rows
  ),
  'forward_outcomes',jsonb_build_object(
    'eligible_15m',research_eligible,
    'complete_15m',forward_15_complete,
    'coverage_15m',round(forward_coverage::numeric,6)
  ),
  'primary_effect',coalesce(
    (
      select jsonb_build_object(
        'n',n,
        'spearman_q_15m',rho,
        'q4_minus_q1_return',quartile_spread
      )
      from per_session where session=p_session
    ),
    jsonb_build_object(
      'n',0,
      'spearman_q_15m',null,
      'q4_minus_q1_return',null
    )
  ),
  'cross_session',jsonb_build_object(
    'rho_sessions',rho_sessions,
    'rho_sign_consistency',round(rho_sign_consistency::numeric,6),
    'quartile_sessions',quartile_sessions,
    'quartile_sign_consistency',round(quartile_sign_consistency::numeric,6),
    'max_single_session_closed_trade_share',round(max_session_share::numeric,6)
  ),
  'confidence',confidence,
  'readiness',jsonb_build_object(
    'state',case
      when data_valid and stable then 'SHADOW_STABLE'
      when data_valid then 'SHADOW_DATA_VALID'
      else 'RESEARCH_ONLY'
    end,
    'data_valid',data_valid,
    'stable',stable,
    'frozen_validation_ready',false,
    'reason_codes',to_jsonb(array_remove(array[
      case when ambiguous_signals>0 then 'AMBIGUOUS_ATTRIBUTION' end,
      case when unlinked_signals>0 then 'UNLINKED_EXECUTABLE_SIGNAL' end,
      case when direct_coverage<0.995 then 'DIRECT_ATTRIBUTION_COVERAGE' end,
      case when forward_coverage<0.95 then 'FORWARD_15M_COVERAGE' end,
      case when not stable then 'STABILITY_GATES_NOT_MET' end,
      'REGIME_CLASSIFIER_UNFROZEN'
    ],null))
  ),
  'live_configuration_changed',false,
  'promotion_authorized',false
)
from readiness;
$ads002$;

revoke all on function private.rhen_ads002_daily_inputs(date)
from public, anon, authenticated;

create or replace function private.project_ads002_postclose_refresh()
returns trigger
language plpgsql
security invoker
set search_path = private, pg_temp
as $ads002$
declare
  v_session date;
begin
  if new.event_type <> 'ads002_postclose_refresh' then
    return new;
  end if;
  v_session := nullif(new.payload->>'session','')::date;
  if v_session is null then
    return new;
  end if;
  perform private.rhen_ads002_refresh_session(v_session);
  return new;
end;
$ads002$;

drop trigger if exists trg_ads002_postclose_refresh
on private.trading_events;

create trigger trg_ads002_postclose_refresh
after insert on private.trading_events
for each row
when (new.event_type='ads002_postclose_refresh')
execute function private.project_ads002_postclose_refresh();

comment on function private.project_ads002_postclose_refresh() is
'Refreshes ADS-002 research-only attribution, X, confidence, and readiness after close.';

-- Preserve candidate-forward-v1 while allowing ADS-002 v2 to require all seven
-- frozen horizons before a candidate is marked complete.
create or replace function private.project_candidate_forward_outcome()
returns trigger
language plpgsql
set search_path = private, pg_temp
as $$
declare
  v_candidate_id bigint;
  v_methodology text;
  v_total integer;
  v_complete integer;
  v_expected integer;
  v_payload jsonb;
begin
  if new.event_type <> 'candidate_forward_outcome' then
    return new;
  end if;

  v_candidate_id := nullif(new.payload->>'candidate_id','')::bigint;
  v_methodology := nullif(new.payload->>'methodology_version','');
  if v_candidate_id is null or v_methodology is null then
    raise exception 'candidate_forward_outcome requires candidate_id and methodology_version';
  end if;

  v_expected := case
    when v_methodology = 'candidate-forward-v2' then 7
    else 4
  end;

  insert into private.trading_candidate_forward_outcomes (
    candidate_id,
    horizon_minutes,
    observation_end_at,
    reference_price,
    forward_price,
    forward_return,
    max_favorable_return,
    max_adverse_return,
    provider,
    bar_interval,
    methodology_version,
    status,
    computed_at,
    details
  ) values (
    v_candidate_id,
    nullif(new.payload->>'horizon_minutes','')::integer,
    nullif(new.payload->>'observation_end_at','')::timestamptz,
    nullif(new.payload->>'reference_price','')::numeric,
    nullif(new.payload->>'forward_price','')::numeric,
    nullif(new.payload->>'forward_return','')::numeric,
    nullif(new.payload->>'max_favorable_return','')::numeric,
    nullif(new.payload->>'max_adverse_return','')::numeric,
    nullif(new.payload->>'provider',''),
    nullif(new.payload->>'bar_interval',''),
    v_methodology,
    new.payload->>'status',
    coalesce(nullif(new.payload->>'computed_at','')::timestamptz,new.occurred_at),
    coalesce(new.payload->'details','{}'::jsonb)
      || jsonb_build_object('source_event_id',new.event_id)
  )
  on conflict (candidate_id,horizon_minutes,methodology_version) do update
  set observation_end_at=excluded.observation_end_at,
      reference_price=coalesce(
        excluded.reference_price,
        private.trading_candidate_forward_outcomes.reference_price
      ),
      forward_price=excluded.forward_price,
      forward_return=excluded.forward_return,
      max_favorable_return=excluded.max_favorable_return,
      max_adverse_return=excluded.max_adverse_return,
      provider=coalesce(
        excluded.provider,
        private.trading_candidate_forward_outcomes.provider
      ),
      bar_interval=coalesce(
        excluded.bar_interval,
        private.trading_candidate_forward_outcomes.bar_interval
      ),
      status=excluded.status,
      computed_at=excluded.computed_at,
      details=private.trading_candidate_forward_outcomes.details || excluded.details;

  select count(*), count(*) filter(where status='complete')
  into v_total, v_complete
  from private.trading_candidate_forward_outcomes
  where candidate_id=v_candidate_id and methodology_version=v_methodology;

  select coalesce(
    jsonb_object_agg(
      horizon_minutes::text,
      jsonb_build_object(
        'status',status,
        'observation_end_at',observation_end_at,
        'forward_return',forward_return,
        'max_favorable_return',max_favorable_return,
        'max_adverse_return',max_adverse_return,
        'methodology_version',methodology_version
      )
      order by horizon_minutes
    ),
    '{}'::jsonb
  )
  into v_payload
  from private.trading_candidate_forward_outcomes
  where candidate_id=v_candidate_id and methodology_version=v_methodology;

  update private.trading_candidate_evaluations
  set forward_outcomes=v_payload,
      forward_outcomes_status=case
        when v_total < v_expected then 'pending'
        when v_complete = v_expected then 'complete'
        else 'incomplete'
      end,
      forward_enriched_at=now()
  where candidate_id=v_candidate_id;

  return new;
end;
$$;

comment on function private.project_candidate_forward_outcome() is
'Projects versioned candidate forward outcomes. v1 expects four horizons; ADS-002 candidate-forward-v2 expects seven.';
