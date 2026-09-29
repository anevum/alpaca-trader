create or replace function private.rhen_ads002_v2_confidence_state(p_model_key text)
returns jsonb
language sql
stable
set search_path = private, pg_temp
as $$
with scores as (
  select * from private.trading_ads_challenger_scores
  where model_key=p_model_key
    and methodology_version='ads-shadow-v2'
    and raw_score is not null
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
  from ranked
  group by session
  having count(*)>=10
),
effect as (
  select
    count(*) filter(where rho is not null)::integer as effect_sessions,
    coalesce(
      count(*) filter(where rho>0)::double precision
      / nullif(count(*) filter(where rho is not null),0),0
    ) as positive_rho_consistency,
    coalesce(
      count(*) filter(where quartile_spread>0)::double precision
      / nullif(count(*) filter(where quartile_spread is not null),0),0
    ) as positive_quartile_consistency,
    coalesce(
      percentile_cont(0.5) within group(order by rho)
      filter(where rho is not null),0
    ) as median_rho
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
  where a.methodology_version='ads-attribution-v1'
    and a.attribution_state='DIRECT_COMPLETE'
),
attr as (
  select
    count(*)::integer executable,
    count(*) filter(
      where attribution_state in ('DIRECT_COMPLETE','DIRECT_PARTIAL')
    )::integer direct
  from private.trading_ads_attribution
  where methodology_version='ads-attribution-v1'
    and session in (select distinct session from scores)
),
derived as (
  select counts.*,trade_counts.closed_direct_trades,effect.*,
    case
      when counts.eligible_score_rows=0 then 0::double precision
      else counts.complete_15m_rows::double precision/counts.eligible_score_rows
    end forward_coverage,
    case
      when attr.executable=0 then 1::double precision
      else attr.direct::double precision/attr.executable
    end direct_coverage,
    least(counts.independent_sessions::double precision/10.0,1.0) session_ratio,
    least(counts.complete_15m_rows::double precision/100.0,1.0) candidate_ratio,
    least(trade_counts.closed_direct_trades::double precision/30.0,1.0) trade_ratio
  from counts,trade_counts,effect,attr
),
confidence as (
  select *,
    power(
      greatest(session_ratio*candidate_ratio*trade_ratio,0),
      1.0/3.0
    ) sample_component,
    least(greatest(
      0.60*positive_rho_consistency
      + 0.25*least(greatest(median_rho,0)/0.20,1.0)
      + 0.15*positive_quartile_consistency,
      0
    ),1) stability_component
  from derived
),
final as (
  select *,
    least(greatest(
      0.30*least(direct_coverage,forward_coverage)
      + 0.30*sample_component
      + 0.40*stability_component,
      0
    ),1) raw_confidence,
    (
      independent_sessions>=10
      and complete_15m_rows>=100
      and closed_direct_trades>=30
      and direct_coverage>=0.995
      and forward_coverage>=0.95
    ) minimums_met
  from confidence
)
select jsonb_build_object(
  'model_key',p_model_key,
  'methodology_version','ads-shadow-v2',
  'score',round((
    case
      when minimums_met then raw_confidence
      else least(raw_confidence,0.49)
    end
  )::numeric,6),
  'hard_cap_active',not minimums_met,
  'samples',jsonb_build_object(
    'independent_sessions',independent_sessions,
    'research_eligible_candidates',complete_15m_rows,
    'closed_direct_trades',closed_direct_trades,
    'effect_sessions',effect_sessions
  ),
  'coverage',jsonb_build_object(
    'direct_attribution',round(direct_coverage::numeric,6),
    'forward_15m',round(forward_coverage::numeric,6)
  ),
  'stability',jsonb_build_object(
    'positive_rho_consistency',round(positive_rho_consistency::numeric,6),
    'positive_quartile_consistency',round(positive_quartile_consistency::numeric,6),
    'median_session_spearman',round(median_rho::numeric,6),
    'component',round(stability_component::numeric,6)
  ),
  'sample_component',round(sample_component::numeric,6),
  'minimums_met',minimums_met,
  'promotion_authorized',false
)
from final;
$$;
