-- ADS-002 v2 shadow challengers.
-- Research-only additive schema. No live execution, sizing, risk, or capital authority.

create table if not exists private.trading_ads_model_registry (
  model_key text primary key,
  program text not null default 'ADS-002',
  methodology_version text not null,
  model_kind text not null,
  status text not null check (status in ('ACTIVE_SHADOW','SPEC_ONLY','FROZEN_VALIDATION','RETIRED')),
  primary_horizon_minutes integer,
  feature_schema_version text,
  normalization_version text,
  formula_spec jsonb not null default '{}'::jsonb,
  validation_spec jsonb not null default '{}'::jsonb,
  execution_authority boolean not null default false check (execution_authority = false),
  promotion_authorized boolean not null default false check (promotion_authorized = false),
  created_at timestamptz not null default now(),
  frozen_at timestamptz
);

create table if not exists private.trading_ads_challenger_scores (
  score_id bigint generated always as identity primary key,
  candidate_id bigint not null
    references private.trading_candidate_evaluations(candidate_id) on delete cascade,
  candidate_key text not null,
  run_id uuid not null references private.trading_runs(run_id),
  strategy_version_id text not null references private.trading_strategy_versions(version_id),
  session date not null,
  symbol text not null,
  observed_at timestamptz not null,
  model_key text not null references private.trading_ads_model_registry(model_key),
  methodology_version text not null,
  feature_schema_version text not null,
  normalization_version text not null,
  attention_score numeric check (attention_score between 0 and 1),
  qualification_score numeric check (qualification_score between 0 and 1),
  timing_score numeric check (timing_score between 0 and 1),
  raw_score numeric check (raw_score between 0 and 1),
  confidence_score numeric check (confidence_score between 0 and 1),
  effective_score numeric check (effective_score between 0 and 1),
  source_completeness jsonb not null default '{}'::jsonb,
  feature_vector jsonb not null default '{}'::jsonb,
  component_payload jsonb not null default '{}'::jsonb,
  forward_methodology_version text not null default 'candidate-forward-v2',
  research_only boolean not null default true check (research_only = true),
  execution_authority boolean not null default false check (execution_authority = false),
  computed_at timestamptz not null default now(),
  unique(candidate_id, model_key, methodology_version)
);

create index if not exists trading_ads_challenger_scores_session_model_idx
  on private.trading_ads_challenger_scores(session, model_key, observed_at);
create index if not exists trading_ads_challenger_scores_candidate_idx
  on private.trading_ads_challenger_scores(candidate_id);
create index if not exists trading_ads_challenger_scores_model_score_idx
  on private.trading_ads_challenger_scores(model_key, raw_score)
  where raw_score is not null;

alter table private.trading_ads_model_registry enable row level security;
alter table private.trading_ads_challenger_scores enable row level security;
revoke all on private.trading_ads_model_registry from anon, authenticated;
revoke all on private.trading_ads_challenger_scores from anon, authenticated;

comment on table private.trading_ads_model_registry is
'ADS shadow model registry. Entries have no execution authority and require separate human-reviewed promotion.';
comment on table private.trading_ads_challenger_scores is
'ADS-002 v2 immutable shadow challenger outputs. Never consumed by live execution.';

insert into private.trading_ads_model_registry (
  model_key, methodology_version, model_kind, status,
  primary_horizon_minutes, feature_schema_version, normalization_version,
  formula_spec, validation_spec, frozen_at
) values
(
  'ads002-add-v2','ads-shadow-v2','PRETRADE_COMPOSITE','ACTIVE_SHADOW',15,
  'ads-features-v2','hybrid-cross-sectional-v1',
  '{"formula":"0.20*A + 0.50*Q + 0.30*T","range":[0,1],"role":"normalized additive baseline challenger"}'::jsonb,
  '{"primary_endpoint":"session Spearman(raw_score, forward_return_15m)","secondary_endpoint":"Q4-Q1 forward return","min_sessions":10,"min_candidates":100,"min_closed_direct_trades":30}'::jsonb,
  now()
),
(
  'ads002-geo-v2','ads-shadow-v2','PRETRADE_COMPOSITE','ACTIVE_SHADOW',15,
  'ads-features-v2','hybrid-cross-sectional-v1',
  '{"formula":"A^0.20 * Q^0.50 * T^0.30","range":[0,1],"role":"weak-link penalizing challenger"}'::jsonb,
  '{"primary_endpoint":"session Spearman(raw_score, forward_return_15m)","secondary_endpoint":"Q4-Q1 forward return","min_sessions":10,"min_candidates":100,"min_closed_direct_trades":30}'::jsonb,
  now()
),
(
  'ads002-rank-v2','ads-shadow-v2','PRETRADE_RANK','ACTIVE_SHADOW',15,
  'ads-features-v2','hybrid-cross-sectional-v1',
  '{"formula":"0.20*rank(A) + 0.50*rank(Q) + 0.30*rank(T)","range":[0,1],"role":"cross-sectional opportunity-ranking challenger"}'::jsonb,
  '{"primary_endpoint":"session Spearman(raw_score, forward_return_15m)","secondary_endpoint":"top-decile minus bottom-decile forward return","min_sessions":10,"min_candidates":100,"min_closed_direct_trades":30}'::jsonb,
  now()
),
(
  'ads002-netev-v2','ads-shadow-v2','EXPECTED_VALUE','SPEC_ONLY',15,
  'ads-features-v2','future-calibrated-probability-v1',
  '{"formula":"P(up)*mu_up + (1-P(up))*mu_down - expected_round_trip_cost","state":"UNFITTED"}'::jsonb,
  '{"requires":"calibrated probabilities, return magnitudes, transaction-cost model, temporal holdout"}'::jsonb,
  null
),
(
  'ads002-horizon-v2','ads-shadow-v2','HORIZON_SELECTOR','SPEC_ONLY',null,
  'ads-features-v2','future-multihorizon-v1',
  '{"formula":"argmax_h(E[net_return_h] - uncertainty_penalty_h)","horizons":[1,3,5,10,15,30,60],"state":"UNFITTED"}'::jsonb,
  '{"requires":"separately validated multi-horizon forecasts and purged temporal validation"}'::jsonb,
  null
),
(
  'ads002-exitcv-v2','ads-shadow-v2','EXIT_CONTINUATION_VALUE','SPEC_ONLY',null,
  'ads-features-v2','future-exit-v1',
  '{"formula":"EV_hold - EV_exit with downside, opportunity-cost and exit-cost terms","state":"UNFITTED"}'::jsonb,
  '{"requires":"position-state snapshots, hold-vs-exit counterfactual outcomes, temporal holdout"}'::jsonb,
  null
),
(
  'ads002-confidence-v2','ads-shadow-v2','CONFIDENCE_META','ACTIVE_SHADOW',15,
  'ads-features-v2','cross-session-confidence-v1',
  '{"formula":"S_eff = 0.5 + C*(S_raw-0.5)","range":[0,1],"role":"uncertainty shrinkage toward neutral"}'::jsonb,
  '{"minimums":{"sessions":10,"candidates":100,"closed_direct_trades":30,"forward_coverage":0.95,"direct_attribution_coverage":0.995}}'::jsonb,
  now()
)
on conflict (model_key) do nothing;

create or replace function private.project_ads002_v2_challenger_scores()
returns trigger
language plpgsql
set search_path = private, pg_temp
as $$
declare
  v_cycle_key text;
  v_scan_cycle_id bigint;
  v_candidate jsonb;
  v_v2 jsonb;
  v_challenger record;
  v_candidate_id bigint;
  v_candidate_key text;
  v_observed_at timestamptz;
  v_symbol text;
begin
  if new.event_type <> 'decision_cycle' then
    return new;
  end if;
  v_cycle_key := nullif(new.payload->>'cycle_key','');
  if v_cycle_key is null or jsonb_typeof(new.payload->'candidates') <> 'array' then
    return new;
  end if;

  select scan_cycle_id into v_scan_cycle_id
  from private.trading_scan_cycles where cycle_key=v_cycle_key;
  if v_scan_cycle_id is null then
    return new;
  end if;

  for v_candidate in select value from jsonb_array_elements(new.payload->'candidates')
  loop
    v_v2 := coalesce(v_candidate->'ads002_v2','{}'::jsonb);
    if jsonb_typeof(v_v2) <> 'object'
       or v_v2->>'methodology_version' <> 'ads-shadow-v2'
       or jsonb_typeof(v_v2->'challengers') <> 'object' then
      continue;
    end if;

    v_symbol := upper(v_candidate->>'symbol');
    v_candidate_key := coalesce(nullif(v_candidate->>'candidate_key',''),v_cycle_key||':'||v_symbol);
    select candidate_id, observed_at into v_candidate_id,v_observed_at
    from private.trading_candidate_evaluations
    where scan_cycle_id=v_scan_cycle_id and symbol=v_symbol;
    if v_candidate_id is null then continue; end if;

    for v_challenger in
      select key as model_key, value as payload
      from jsonb_each(v_v2->'challengers')
    loop
      if not exists (
        select 1 from private.trading_ads_model_registry
        where model_key=v_challenger.model_key and status='ACTIVE_SHADOW'
      ) then
        continue;
      end if;
      insert into private.trading_ads_challenger_scores (
        candidate_id,candidate_key,run_id,strategy_version_id,session,symbol,observed_at,
        model_key,methodology_version,feature_schema_version,normalization_version,
        attention_score,qualification_score,timing_score,raw_score,
        source_completeness,feature_vector,component_payload,
        forward_methodology_version,research_only,execution_authority,computed_at
      ) values (
        v_candidate_id,v_candidate_key,new.run_id,new.strategy_version_id,
        (v_observed_at at time zone 'America/New_York')::date,v_symbol,v_observed_at,
        v_challenger.model_key,'ads-shadow-v2',
        coalesce(v_v2->>'feature_schema_version','ads-features-v2'),
        coalesce(v_v2->>'normalization_version','hybrid-cross-sectional-v1'),
        nullif(v_v2#>>'{attention,score}','')::numeric,
        nullif(v_v2#>>'{qualification,score}','')::numeric,
        nullif(v_v2#>>'{timing,score}','')::numeric,
        nullif(v_challenger.payload->>'s_raw','')::numeric,
        coalesce(v_v2->'source_completeness','{}'::jsonb),
        coalesce(v_v2->'raw_features','{}'::jsonb),
        jsonb_build_object(
          'attention',v_v2->'attention','qualification',v_v2->'qualification',
          'timing',v_v2->'timing','component_ranks',v_v2->'component_ranks',
          'confidence',v_v2->'confidence'
        ),
        'candidate-forward-v2',true,false,now()
      )
      on conflict (candidate_id,model_key,methodology_version) do update set
        candidate_key=excluded.candidate_key,
        attention_score=excluded.attention_score,
        qualification_score=excluded.qualification_score,
        timing_score=excluded.timing_score,
        raw_score=excluded.raw_score,
        source_completeness=excluded.source_completeness,
        feature_vector=excluded.feature_vector,
        component_payload=excluded.component_payload,
        computed_at=excluded.computed_at;
    end loop;
  end loop;
  return new;
end;
$$;

create or replace function private.rhen_ads002_v2_confidence_state(p_model_key text)
returns jsonb
language sql
stable
set search_path = private, pg_temp
as $$
with scores as (
  select * from private.trading_ads_challenger_scores
  where model_key=p_model_key and methodology_version='ads-shadow-v2' and raw_score is not null
),
eligible as (
  select s.*,fo.forward_return::double precision as r
  from scores s
  join private.trading_candidate_forward_outcomes fo
    on fo.candidate_id=s.candidate_id
   and fo.methodology_version='candidate-forward-v2'
   and fo.horizon_minutes=15
   and fo.status='complete'
   and fo.forward_return is not null
),
ranked as (
  select *,
    rank() over(partition by session order by raw_score)::double precision as s_rank,
    rank() over(partition by session order by r)::double precision as r_rank,
    ntile(4) over(partition by session order by raw_score) as quartile
  from eligible
),
per_session as (
  select session,count(*)::integer n,corr(s_rank,r_rank) rho,
    avg(r) filter(where quartile=4)-avg(r) filter(where quartile=1) quartile_spread
  from ranked group by session having count(*)>=10
),
effect as (
  select
    count(*) filter(where rho is not null)::integer as effect_sessions,
    coalesce(count(*) filter(where rho>0)::double precision/nullif(count(*) filter(where rho is not null),0),0) as positive_rho_consistency,
    coalesce(count(*) filter(where quartile_spread>0)::double precision/nullif(count(*) filter(where quartile_spread is not null),0),0) as positive_quartile_consistency,
    coalesce(percentile_cont(0.5) within group(order by rho) filter(where rho is not null),0) as median_rho
  from per_session
),
counts as (
  select
    count(*)::integer eligible_score_rows,
    count(distinct session)::integer independent_sessions,
    (select count(*)::integer from eligible) complete_15m_rows
  from scores
),
trade_counts as (
  select count(distinct a.candidate_id)::integer closed_direct_trades
  from private.trading_ads_attribution a
  join scores s on s.candidate_id=a.candidate_id
  where a.methodology_version='ads-attribution-v1' and a.attribution_state='DIRECT_COMPLETE'
),
attr as (
  select
    count(*)::integer executable,
    count(*) filter(where attribution_state in ('DIRECT_COMPLETE','DIRECT_PARTIAL'))::integer direct
  from private.trading_ads_attribution
  where methodology_version='ads-attribution-v1'
),
derived as (
  select counts.*,trade_counts.closed_direct_trades,effect.*,
    case when counts.eligible_score_rows=0 then 0::double precision else counts.complete_15m_rows::double precision/counts.eligible_score_rows end forward_coverage,
    case when attr.executable=0 then 1::double precision else attr.direct::double precision/attr.executable end direct_coverage,
    least(counts.independent_sessions::double precision/10.0,1.0) session_ratio,
    least(counts.complete_15m_rows::double precision/100.0,1.0) candidate_ratio,
    least(trade_counts.closed_direct_trades::double precision/30.0,1.0) trade_ratio
  from counts,trade_counts,effect,attr
),
confidence as (
  select *,
    power(greatest(session_ratio*candidate_ratio*trade_ratio,0),1.0/3.0) sample_component,
    least(greatest(0.60*positive_rho_consistency + 0.25*least(greatest(median_rho,0)/0.20,1.0) + 0.15*positive_quartile_consistency,0),1) stability_component
  from derived
),
final as (
  select *,
    least(greatest(0.30*least(direct_coverage,forward_coverage) + 0.30*sample_component + 0.40*stability_component,0),1) raw_confidence,
    (independent_sessions>=10 and complete_15m_rows>=100 and closed_direct_trades>=30 and direct_coverage>=0.995 and forward_coverage>=0.95) minimums_met
  from confidence
)
select jsonb_build_object(
  'model_key',p_model_key,
  'methodology_version','ads-shadow-v2',
  'score',round((case when minimums_met then raw_confidence else least(raw_confidence,0.49) end)::numeric,6),
  'hard_cap_active',not minimums_met,
  'samples',jsonb_build_object('independent_sessions',independent_sessions,'research_eligible_candidates',complete_15m_rows,'closed_direct_trades',closed_direct_trades,'effect_sessions',effect_sessions),
  'coverage',jsonb_build_object('direct_attribution',round(direct_coverage::numeric,6),'forward_15m',round(forward_coverage::numeric,6)),
  'stability',jsonb_build_object('positive_rho_consistency',round(positive_rho_consistency::numeric,6),'positive_quartile_consistency',round(positive_quartile_consistency::numeric,6),'median_session_spearman',round(median_rho::numeric,6),'component',round(stability_component::numeric,6)),
  'sample_component',round(sample_component::numeric,6),
  'minimums_met',minimums_met,
  'promotion_authorized',false
) from final;
$$;

create or replace function private.rhen_ads002_v2_refresh_session(p_session date)
returns jsonb
language plpgsql
set search_path = private, pg_temp
as $$
declare
  r record;
  v_conf jsonb;
  v_models jsonb := '{}'::jsonb;
begin
  for r in
    select model_key from private.trading_ads_model_registry
    where methodology_version='ads-shadow-v2' and status='ACTIVE_SHADOW'
      and model_kind in ('PRETRADE_COMPOSITE','PRETRADE_RANK')
    order by model_key
  loop
    v_conf := private.rhen_ads002_v2_confidence_state(r.model_key);
    update private.trading_ads_challenger_scores
    set confidence_score=nullif(v_conf->>'score','')::numeric,
        effective_score=case when raw_score is null then null else
          0.5 + nullif(v_conf->>'score','')::numeric*(raw_score-0.5)
        end,
        computed_at=now()
    where session=p_session and model_key=r.model_key and methodology_version='ads-shadow-v2';
    v_models := v_models || jsonb_build_object(r.model_key,v_conf);
  end loop;
  return jsonb_build_object(
    'session',p_session,'methodology_version','ads-shadow-v2','models',v_models,
    'live_configuration_changed',false,'promotion_authorized',false
  );
end;
$$;

create or replace function private.project_ads002_v2_postclose_refresh()
returns trigger
language plpgsql
set search_path = private, pg_temp
as $$
declare v_session date;
begin
  if new.event_type <> 'ads002_postclose_refresh' then return new; end if;
  v_session := nullif(new.payload->>'session','')::date;
  if v_session is not null then perform private.rhen_ads002_v2_refresh_session(v_session); end if;
  return new;
end;
$$;

drop trigger if exists trg_zzzz_ads002_v2_challengers on private.trading_events;
create trigger trg_zzzz_ads002_v2_challengers
after insert on private.trading_events
for each row when (new.event_type='decision_cycle')
execute function private.project_ads002_v2_challenger_scores();

drop trigger if exists trg_zz_ads002_v2_postclose_refresh on private.trading_events;
create trigger trg_zz_ads002_v2_postclose_refresh
after insert on private.trading_events
for each row when (new.event_type='ads002_postclose_refresh')
execute function private.project_ads002_v2_postclose_refresh();
