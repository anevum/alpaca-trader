create or replace function private.rhen_ads002_v2_daily_inputs(p_session date)
returns jsonb
language sql
stable
set search_path = private, pg_temp
as $$
with registry as (
  select
    model_key,
    model_kind,
    status,
    primary_horizon_minutes,
    feature_schema_version,
    normalization_version,
    formula_spec,
    validation_spec
  from private.trading_ads_model_registry
  where methodology_version='ads-shadow-v2'
),
session_candidates as (
  select count(*)::integer as candidate_count
  from private.trading_candidate_evaluations
  where (observed_at at time zone 'America/New_York')::date=p_session
),
scores as (
  select *
  from private.trading_ads_challenger_scores
  where session=p_session
    and methodology_version='ads-shadow-v2'
),
eligible as (
  select
    s.model_key,
    s.candidate_id,
    s.raw_score,
    fo.forward_return::double precision as r
  from scores s
  join private.trading_candidate_forward_outcomes fo
    on fo.candidate_id=s.candidate_id
   and fo.methodology_version='candidate-forward-v2'
   and fo.horizon_minutes=15
   and fo.status='complete'
   and fo.forward_return is not null
  where s.raw_score is not null
),
ranked as (
  select
    model_key,
    candidate_id,
    raw_score,
    r,
    rank() over(partition by model_key order by raw_score)::double precision as s_rank,
    rank() over(partition by model_key order by r)::double precision as r_rank,
    ntile(4) over(partition by model_key order by raw_score) as quartile
  from eligible
),
effects as (
  select
    model_key,
    count(*)::integer as outcome_rows,
    corr(s_rank,r_rank) as spearman_15m,
    avg(r) filter(where quartile=4)
      - avg(r) filter(where quartile=1) as q4_minus_q1_return
  from ranked
  group by model_key
),
model_stats as (
  select
    r.model_key,
    r.model_kind,
    r.status,
    r.primary_horizon_minutes,
    r.feature_schema_version,
    r.normalization_version,
    r.formula_spec,
    r.validation_spec,
    count(s.score_id)::integer as score_rows,
    count(s.raw_score)::integer as complete_score_rows,
    count(distinct s.candidate_id)::integer as scored_candidates,
    avg(s.raw_score) as avg_raw_score,
    avg(s.effective_score) as avg_effective_score,
    min(s.raw_score) as min_raw_score,
    max(s.raw_score) as max_raw_score,
    coalesce(e.outcome_rows,0)::integer as complete_15m_outcomes,
    e.spearman_15m,
    e.q4_minus_q1_return
  from registry r
  left join scores s on s.model_key=r.model_key
  left join effects e on e.model_key=r.model_key
  group by
    r.model_key,r.model_kind,r.status,r.primary_horizon_minutes,
    r.feature_schema_version,r.normalization_version,
    r.formula_spec,r.validation_spec,
    e.outcome_rows,e.spearman_15m,e.q4_minus_q1_return
),
active_models as (
  select
    ms.*,
    private.rhen_ads002_v2_confidence_state(ms.model_key) as confidence
  from model_stats ms
  where ms.status='ACTIVE_SHADOW'
    and ms.model_kind in ('PRETRADE_COMPOSITE','PRETRADE_RANK')
),
registry_json as (
  select coalesce(
    jsonb_agg(
      jsonb_build_object(
        'model_key', model_key,
        'model_kind', model_kind,
        'status', status,
        'primary_horizon_minutes', primary_horizon_minutes,
        'feature_schema_version', feature_schema_version,
        'normalization_version', normalization_version,
        'formula_spec', formula_spec,
        'validation_spec', validation_spec
      )
      order by model_key
    ),
    '[]'::jsonb
  ) as value
  from registry
),
models_json as (
  select coalesce(
    jsonb_agg(
      jsonb_build_object(
        'model_key', model_key,
        'model_kind', model_kind,
        'status', status,
        'score_rows', score_rows,
        'complete_score_rows', complete_score_rows,
        'scored_candidates', scored_candidates,
        'complete_15m_outcomes', complete_15m_outcomes,
        'score_coverage', case
          when (select candidate_count from session_candidates)=0 then 0
          else round(
            scored_candidates::numeric
            / (select candidate_count from session_candidates)::numeric,
            6
          )
        end,
        'avg_raw_score', case
          when avg_raw_score is null then null
          else round(avg_raw_score,6)
        end,
        'avg_effective_score', case
          when avg_effective_score is null then null
          else round(avg_effective_score,6)
        end,
        'min_raw_score', case
          when min_raw_score is null then null
          else round(min_raw_score,6)
        end,
        'max_raw_score', case
          when max_raw_score is null then null
          else round(max_raw_score,6)
        end,
        'session_spearman_15m', case
          when spearman_15m is null then null
          else round(spearman_15m::numeric,6)
        end,
        'q4_minus_q1_return_15m', case
          when q4_minus_q1_return is null then null
          else round(q4_minus_q1_return::numeric,8)
        end,
        'confidence', confidence
      )
      order by model_key
    ),
    '[]'::jsonb
  ) as value
  from active_models
)
select jsonb_build_object(
  'session',p_session,
  'program','ADS-002',
  'methodology_version','ads-shadow-v2',
  'feature_schema_version','ads-features-v2',
  'candidate_count',(select candidate_count from session_candidates),
  'models',(select value from models_json),
  'registry',(select value from registry_json),
  'research_only',true,
  'live_configuration_changed',false,
  'promotion_authorized',false
);
$$;
