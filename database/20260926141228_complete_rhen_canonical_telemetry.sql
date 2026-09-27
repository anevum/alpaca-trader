-- RHEN canonical research and decision telemetry.
-- Additive and idempotent: no strategy, risk, or execution configuration changes.

alter table private.trading_scan_cycles
  add column if not exists execution_mode text,
  add column if not exists market_session text,
  add column if not exists universe_version text,
  add column if not exists symbols_expected text[],
  add column if not exists symbols_evaluated text[],
  add column if not exists data_source text,
  add column if not exists data_feed text,
  add column if not exists bar_interval text,
  add column if not exists methodology_version text,
  add column if not exists strategy_family text,
  add column if not exists runtime_metrics jsonb not null default '{}'::jsonb,
  add column if not exists blocker_code text,
  add column if not exists build_id text;

alter table private.trading_candidate_evaluations
  add column if not exists candidate_key text,
  add column if not exists qualification_status text,
  add column if not exists final_decision text,
  add column if not exists rejection_reason_codes text[] not null default '{}'::text[],
  add column if not exists candidate_score numeric,
  add column if not exists quote_age_ms bigint,
  add column if not exists market_context jsonb not null default '{}'::jsonb,
  add column if not exists sector_context jsonb not null default '{}'::jsonb,
  add column if not exists eligibility jsonb not null default '{}'::jsonb,
  add column if not exists constraints jsonb not null default '{}'::jsonb,
  add column if not exists strategy_family text,
  add column if not exists methodology_version text,
  add column if not exists data_source text,
  add column if not exists data_feed text,
  add column if not exists bar_interval text,
  add column if not exists signal_id uuid references private.trading_signals(signal_id);

create unique index if not exists trading_candidate_evaluations_candidate_key_uidx
  on private.trading_candidate_evaluations(candidate_key)
  where candidate_key is not null;
create index if not exists trading_candidate_rejection_codes_gin
  on private.trading_candidate_evaluations using gin(rejection_reason_codes);
create index if not exists trading_candidate_observed_symbol_idx
  on private.trading_candidate_evaluations(observed_at desc, symbol);
create index if not exists trading_candidate_signal_idx
  on private.trading_candidate_evaluations(signal_id)
  where signal_id is not null;

alter table private.trading_experiments
  add column if not exists methodology_version text,
  add column if not exists corpus_version text,
  add column if not exists universe_version text,
  add column if not exists forward_horizons_minutes integer[] not null default '{}'::integer[],
  add column if not exists cost_assumptions jsonb not null default '{}'::jsonb,
  add column if not exists sample_count bigint,
  add column if not exists robustness_metrics jsonb not null default '{}'::jsonb,
  add column if not exists terminal_decision text,
  add column if not exists terminal_reason text,
  add column if not exists parent_experiment_id uuid references private.trading_experiments(experiment_id),
  add column if not exists code_build_id text,
  add column if not exists artifact_location text;

alter table private.trading_positions
  add column if not exists entry_reason text,
  add column if not exists realized_return numeric,
  add column if not exists initial_stop_price numeric,
  add column if not exists target_price numeric,
  add column if not exists stop_touched boolean,
  add column if not exists target_touched boolean,
  add column if not exists context_tags jsonb not null default '{}'::jsonb;

create table if not exists private.trading_candidate_forward_outcomes (
  outcome_id bigint generated always as identity primary key,
  candidate_id bigint not null references private.trading_candidate_evaluations(candidate_id) on delete cascade,
  horizon_minutes integer not null check (horizon_minutes > 0),
  observation_end_at timestamptz,
  reference_price numeric,
  forward_price numeric,
  forward_return numeric,
  max_favorable_return numeric,
  max_adverse_return numeric,
  provider text,
  bar_interval text,
  methodology_version text not null,
  status text not null check (status in ('pending','complete','insufficient_future_data','error')),
  computed_at timestamptz not null default now(),
  details jsonb not null default '{}'::jsonb,
  unique(candidate_id, horizon_minutes, methodology_version)
);

create table if not exists private.trading_experiment_results (
  result_id bigint generated always as identity primary key,
  experiment_id uuid not null references private.trading_experiments(experiment_id) on delete cascade,
  result_key text not null,
  family text not null,
  stage text not null,
  period_id text,
  cost_scenario text,
  event_count bigint,
  expectancy numeric,
  profit_factor numeric,
  worst_period_expectancy numeric,
  threshold_floor numeric,
  terminal_decision text,
  terminal_reason_code text,
  metrics jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  unique(experiment_id, result_key)
);

create table if not exists private.trading_experiment_windows (
  experiment_window_id bigint generated always as identity primary key,
  experiment_id uuid not null references private.trading_experiments(experiment_id) on delete cascade,
  window_key text not null,
  role text not null,
  starts_on date not null,
  ends_on date not null,
  status text not null,
  data_source text,
  data_feed text,
  bar_interval text,
  expected_symbols text[] not null default '{}'::text[],
  present_symbols text[] not null default '{}'::text[],
  context_symbols text[] not null default '{}'::text[],
  expected_session_count integer,
  represented_session_count integer,
  pagination_complete boolean,
  missing_sessions jsonb not null default '{}'::jsonb,
  completeness_basis text,
  completeness_ratio numeric,
  density_diagnostic jsonb not null default '{}'::jsonb,
  first_timestamp timestamptz,
  last_timestamp timestamptz,
  details jsonb not null default '{}'::jsonb,
  unique(experiment_id, window_key)
);

create table if not exists private.trading_experiment_symbol_coverage (
  coverage_id bigint generated always as identity primary key,
  experiment_window_id bigint not null references private.trading_experiment_windows(experiment_window_id) on delete cascade,
  symbol text not null,
  expected boolean not null default true,
  present boolean,
  bar_count bigint,
  expected_session_count integer,
  represented_session_count integer,
  session_completeness boolean,
  first_timestamp timestamptz,
  last_timestamp timestamptz,
  missing_sessions date[] not null default '{}'::date[],
  pagination_complete boolean,
  iex_bar_density_ratio numeric,
  diagnostics jsonb not null default '{}'::jsonb,
  unique(experiment_window_id, symbol)
);

create table if not exists private.trading_research_decisions (
  decision_id uuid primary key default gen_random_uuid(),
  decision_key text not null unique,
  decided_at timestamptz not null,
  status text not null,
  decision_type text not null,
  subject text not null,
  conclusion text not null,
  methodology_version text,
  evidence jsonb not null default '{}'::jsonb,
  code_commit text,
  deployment_id text,
  supersedes_decision_id uuid references private.trading_research_decisions(decision_id),
  superseded_by_decision_id uuid references private.trading_research_decisions(decision_id),
  created_at timestamptz not null default now()
);

create table if not exists private.trading_research_decision_experiments (
  decision_id uuid not null references private.trading_research_decisions(decision_id) on delete cascade,
  experiment_id uuid not null references private.trading_experiments(experiment_id) on delete cascade,
  evidence_role text not null default 'supporting',
  primary key(decision_id, experiment_id)
);

create table if not exists private.trading_runtime_instances (
  runtime_instance_id text primary key,
  run_id uuid references private.trading_runs(run_id),
  strategy_version_id text references private.trading_strategy_versions(version_id),
  deployment_id text,
  build_id text,
  git_commit text,
  repository text,
  branch text,
  service_id text,
  service_name text,
  environment_id text,
  environment_name text,
  system_version text,
  started_at timestamptz not null,
  stopped_at timestamptz,
  metadata jsonb not null default '{}'::jsonb
);

alter table private.trading_candidate_forward_outcomes enable row level security;
alter table private.trading_experiment_results enable row level security;
alter table private.trading_experiment_windows enable row level security;
alter table private.trading_experiment_symbol_coverage enable row level security;
alter table private.trading_research_decisions enable row level security;
alter table private.trading_research_decision_experiments enable row level security;
alter table private.trading_runtime_instances enable row level security;

revoke all on private.trading_candidate_forward_outcomes from anon, authenticated;
revoke all on private.trading_experiment_results from anon, authenticated;
revoke all on private.trading_experiment_windows from anon, authenticated;
revoke all on private.trading_experiment_symbol_coverage from anon, authenticated;
revoke all on private.trading_research_decisions from anon, authenticated;
revoke all on private.trading_research_decision_experiments from anon, authenticated;
revoke all on private.trading_runtime_instances from anon, authenticated;

create index if not exists trading_forward_outcomes_horizon_idx
  on private.trading_candidate_forward_outcomes(horizon_minutes, status, computed_at desc);
create index if not exists trading_experiment_results_family_idx
  on private.trading_experiment_results(experiment_id, family, stage);
create index if not exists trading_experiment_windows_role_idx
  on private.trading_experiment_windows(experiment_id, role, starts_on);
create index if not exists trading_symbol_coverage_symbol_idx
  on private.trading_experiment_symbol_coverage(symbol, session_completeness);
create index if not exists trading_research_decisions_time_idx
  on private.trading_research_decisions(decided_at desc);
create index if not exists trading_runtime_instances_started_idx
  on private.trading_runtime_instances(started_at desc);

create or replace function private.enrich_decision_cycle_projection()
returns trigger
language plpgsql
set search_path = private, pg_temp
as $$
declare
  v_cycle_id bigint;
  v_candidate jsonb;
  v_candidate_id bigint;
  v_signal_id uuid;
begin
  select scan_cycle_id into v_cycle_id
  from private.trading_scan_cycles
  where cycle_key = new.payload->>'cycle_key';
  if v_cycle_id is null then
    return new;
  end if;

  update private.trading_scan_cycles
  set execution_mode = nullif(new.payload->>'execution_mode',''),
      market_session = nullif(new.payload->>'market_session',''),
      universe_version = nullif(new.payload->>'universe_version',''),
      symbols_expected = coalesce(array(select jsonb_array_elements_text(new.payload->'symbols_expected')), '{}'::text[]),
      symbols_evaluated = coalesce(array(select jsonb_array_elements_text(new.payload->'symbols_evaluated')), '{}'::text[]),
      data_source = nullif(new.payload->>'data_source',''),
      data_feed = nullif(new.payload->>'data_feed',''),
      bar_interval = nullif(new.payload->>'bar_interval',''),
      methodology_version = nullif(new.payload->>'methodology_version',''),
      strategy_family = nullif(new.payload->>'strategy_family',''),
      runtime_metrics = coalesce(new.payload->'runtime_metrics','{}'::jsonb),
      blocker_code = nullif(new.payload->>'blocker_code',''),
      build_id = coalesce(nullif(new.payload#>>'{runtime,snapshot_id}',''), nullif(new.payload#>>'{runtime,build_id}',''))
  where scan_cycle_id = v_cycle_id;

  if jsonb_typeof(new.payload->'candidates') = 'array' then
    for v_candidate in select value from jsonb_array_elements(new.payload->'candidates') loop
      v_signal_id := nullif(v_candidate->>'signal_id','')::uuid;
      update private.trading_candidate_evaluations
      set candidate_key = nullif(v_candidate->>'candidate_key',''),
          qualification_status = nullif(v_candidate->>'qualification_status',''),
          final_decision = nullif(v_candidate->>'final_decision',''),
          rejection_reason_codes = coalesce(array(select jsonb_array_elements_text(v_candidate->'rejection_reason_codes')), '{}'::text[]),
          candidate_score = nullif(v_candidate->>'candidate_score','')::numeric,
          quote_age_ms = nullif(v_candidate#>>'{quote,age_ms}','')::bigint,
          market_context = coalesce(v_candidate->'market_context','{}'::jsonb),
          sector_context = coalesce(v_candidate->'sector_context','{}'::jsonb),
          eligibility = coalesce(v_candidate->'eligibility','{}'::jsonb),
          constraints = coalesce(v_candidate->'constraints','{}'::jsonb),
          strategy_family = nullif(new.payload->>'strategy_family',''),
          methodology_version = nullif(new.payload->>'methodology_version',''),
          data_source = nullif(new.payload->>'data_source',''),
          data_feed = nullif(new.payload->>'data_feed',''),
          bar_interval = nullif(new.payload->>'bar_interval',''),
          signal_id = coalesce(v_signal_id, signal_id)
      where scan_cycle_id = v_cycle_id
        and symbol = upper(v_candidate->>'symbol')
      returning candidate_id into v_candidate_id;

      if v_signal_id is not null and v_candidate_id is not null then
        update private.trading_signals
        set candidate_id = v_candidate_id,
            payload = payload || jsonb_build_object('cycle_key', new.payload->>'cycle_key')
        where signal_id = v_signal_id;
      end if;
    end loop;
  end if;
  return new;
end;
$$;

drop trigger if exists trg_zz_enrich_decision_cycle on private.trading_events;
create trigger trg_zz_enrich_decision_cycle
after insert on private.trading_events
for each row when (new.event_type = 'decision_cycle')
execute function private.enrich_decision_cycle_projection();

create or replace function private.project_runtime_instance()
returns trigger
language plpgsql
set search_path = private, pg_temp
as $$
declare
  v_runtime jsonb := coalesce(new.payload->'runtime',new.payload,'{}'::jsonb);
  v_instance_id text := coalesce(
    nullif(new.payload#>>'{runtime,runtime_instance_id}',''),
    nullif(new.payload->>'runtime_instance_id','')
  );
begin
  if v_instance_id is null then return new; end if;
  insert into private.trading_runtime_instances (
    runtime_instance_id, run_id, strategy_version_id, deployment_id, build_id,
    git_commit, repository, branch, service_id, service_name, environment_id,
    environment_name, system_version, started_at, stopped_at, metadata
  ) values (
    v_instance_id, new.run_id, new.strategy_version_id,
    nullif(v_runtime->>'deployment_id',''),
    coalesce(nullif(v_runtime->>'snapshot_id',''), nullif(v_runtime->>'build_id','')),
    nullif(v_runtime->>'git_commit',''), nullif(v_runtime->>'repository',''),
    nullif(v_runtime->>'branch',''), nullif(v_runtime->>'service_id',''),
    nullif(v_runtime->>'service_name',''), nullif(v_runtime->>'environment_id',''),
    nullif(v_runtime->>'environment_name',''), nullif(v_runtime->>'system_version',''),
    coalesce(nullif(v_runtime->>'runtime_started_at','')::timestamptz, new.occurred_at),
    case when new.event_type='runtime_stop' then new.occurred_at else null end,
    new.payload
  )
  on conflict (runtime_instance_id) do update
  set stopped_at = coalesce(excluded.stopped_at, private.trading_runtime_instances.stopped_at),
      metadata = private.trading_runtime_instances.metadata || excluded.metadata;
  return new;
end;
$$;

drop trigger if exists trg_project_runtime_instance on private.trading_events;
create trigger trg_project_runtime_instance
after insert on private.trading_events
for each row when (new.event_type in ('runtime_start','runtime_stop'))
execute function private.project_runtime_instance();

create or replace function private.enrich_position_analysis()
returns trigger
language plpgsql
set search_path = private, pg_temp
as $$
declare
  v_signal private.trading_signals%rowtype;
begin
  if coalesce(new.payload->>'entry_order_id','') <> '' then
    select s.* into v_signal
    from private.trading_orders o
    join private.trading_order_intents i on i.intent_id=o.order_intent_id
    join private.trading_signals s on s.signal_id=i.signal_id
    where o.broker_order_id=new.payload->>'entry_order_id'
    limit 1;
    if found then
      new.entry_reason := coalesce(new.entry_reason, v_signal.payload->>'reason');
      new.initial_stop_price := coalesce(new.initial_stop_price, v_signal.stop_price);
      new.target_price := coalesce(new.target_price, v_signal.target_price);
      new.entry_reference_price := coalesce(new.entry_reference_price, v_signal.reference_price);
      new.context_tags := new.context_tags || jsonb_build_object(
        'strategy_checks', coalesce(v_signal.payload#>'{metadata,checks}','{}'::jsonb),
        'market_confirmations', coalesce(v_signal.payload#>'{metadata,confirmations}','{}'::jsonb),
        'regime_confirmations', coalesce(v_signal.payload#>'{metadata,regime_confirmations}','{}'::jsonb)
      );
    end if;
  end if;
  if new.avg_entry_price is not null and new.avg_entry_price > 0 and new.avg_exit_price is not null then
    new.realized_return := (new.avg_exit_price / new.avg_entry_price) - 1;
  end if;
  if new.peak_favorable_price is not null and new.target_price is not null then
    new.target_touched := new.peak_favorable_price >= new.target_price;
  end if;
  if new.peak_adverse_price is not null and new.initial_stop_price is not null then
    new.stop_touched := new.peak_adverse_price <= new.initial_stop_price;
  end if;
  return new;
end;
$$;

drop trigger if exists trg_enrich_position_analysis on private.trading_positions;
create trigger trg_enrich_position_analysis
before insert or update on private.trading_positions
for each row execute function private.enrich_position_analysis();

create or replace function private.rhen_reporting_inputs(p_start date, p_end date)
returns jsonb
language sql
stable
security invoker
set search_path = private, pg_temp
as $$
with bounds as (
  select p_start::timestamptz as starts_at, (p_end + 1)::timestamptz as ends_at
), candidates as (
  select c.* from private.trading_candidate_evaluations c, bounds b
  where c.observed_at >= b.starts_at and c.observed_at < b.ends_at
), reasons as (
  select code, count(*) count
  from candidates c cross join lateral unnest(c.rejection_reason_codes) code
  group by code
), orders as (
  select o.* from private.trading_orders o, bounds b
  where coalesce(o.submitted_at,o.updated_at) >= b.starts_at
    and coalesce(o.submitted_at,o.updated_at) < b.ends_at
), positions as (
  select p.* from private.trading_positions p, bounds b
  where p.opened_at >= b.starts_at and p.opened_at < b.ends_at
), decisions as (
  select d.* from private.trading_research_decisions d, bounds b
  where d.decided_at >= b.starts_at and d.decided_at < b.ends_at
)
select jsonb_build_object(
  'period', jsonb_build_object('start',p_start,'end',p_end),
  'candidate_funnel', jsonb_build_object(
    'scan_cycles', (select count(*) from private.trading_scan_cycles sc,bounds b where sc.observed_at>=b.starts_at and sc.observed_at<b.ends_at),
    'candidates', (select count(*) from candidates),
    'qualified', (select count(*) from candidates where qualified),
    'rejected', (select count(*) from candidates where not qualified),
    'rejection_reason_distribution', coalesce((select jsonb_object_agg(code,count) from reasons),'{}'::jsonb),
    'source', 'canonical_telemetry'
  ),
  'counterfactuals', jsonb_build_object(
    'complete', (select count(*) from private.trading_candidate_forward_outcomes o join candidates c using(candidate_id) where o.status='complete'),
    'pending_candidates', (select count(*) from candidates where not qualified and forward_outcomes_status='pending')
  ),
  'execution_quality', jsonb_build_object(
    'orders', (select count(*) from orders),
    'with_fill_slippage', (select count(*) from orders where reference_price_to_fill_bps is not null),
    'avg_reference_to_fill_bps', (select avg(reference_price_to_fill_bps) from orders where reference_price_to_fill_bps is not null),
    'avg_decision_to_submit_ms', (select avg(decision_to_submit_ms) from orders where decision_to_submit_ms is not null),
    'avg_ack_to_fill_ms', (select avg(ack_to_fill_ms) from orders where ack_to_fill_ms is not null)
  ),
  'trade_analysis', jsonb_build_object(
    'positions', (select count(*) from positions),
    'closed', (select count(*) from positions where status='closed'),
    'realized_pnl', (select coalesce(sum(realized_pnl),0) from positions where status='closed'),
    'avg_realized_return', (select avg(realized_return) from positions where realized_return is not null),
    'with_mfe_mae', (select count(*) from positions where max_favorable_excursion is not null and max_adverse_excursion is not null),
    'target_touched', (select count(*) from positions where target_touched),
    'stop_touched', (select count(*) from positions where stop_touched)
  ),
  'experiment_changes', coalesce((select jsonb_agg(jsonb_build_object('experiment_key',experiment_key,'status',status,'terminal_decision',terminal_decision,'completed_at',completed_at) order by created_at) from private.trading_experiments where created_at < (select ends_at from bounds) and coalesce(completed_at,created_at) >= (select starts_at from bounds)),'[]'::jsonb),
  'research_decisions', coalesce((select jsonb_agg(jsonb_build_object('decision_key',decision_key,'status',status,'decision_type',decision_type,'subject',subject,'conclusion',conclusion,'decided_at',decided_at) order by decided_at) from decisions),'[]'::jsonb)
);
$$;

revoke all on function private.rhen_reporting_inputs(date,date) from public, anon, authenticated;

create or replace view private.rhen_decision_trace
with (security_invoker = true)
as
select
  sc.scan_cycle_id, sc.cycle_key, sc.observed_at as cycle_observed_at,
  sc.strategy_version_id, sc.execution_mode, sc.deployment_id, sc.git_commit,
  c.candidate_id, c.candidate_key, c.symbol, c.qualification_status,
  c.final_decision, c.rejection_reason_codes, c.reason,
  s.signal_id, i.intent_id, i.idempotency_key, i.risk_decision,
  o.broker_order_id, o.status as order_status, o.filled_avg_price,
  o.reference_price_to_fill_bps,
  p.position_id, p.status as position_status, p.realized_pnl,
  p.realized_return, p.holding_duration_ms, p.max_favorable_excursion,
  p.max_adverse_excursion, p.target_touched, p.stop_touched, p.exit_reason,
  e.exit_id,
  coalesce(fo.outcomes,'[]'::jsonb) as forward_outcomes
from private.trading_scan_cycles sc
join private.trading_candidate_evaluations c on c.scan_cycle_id=sc.scan_cycle_id
left join private.trading_signals s on s.candidate_id=c.candidate_id or s.signal_id=c.signal_id
left join private.trading_order_intents i on i.signal_id=s.signal_id
left join private.trading_orders o on o.order_intent_id=i.intent_id
left join private.trading_positions p on p.payload->>'entry_order_id'=o.broker_order_id
left join private.trading_exits e on e.position_id=p.position_id
left join lateral (
  select jsonb_agg(to_jsonb(x) order by x.horizon_minutes) outcomes
  from private.trading_candidate_forward_outcomes x where x.candidate_id=c.candidate_id
) fo on true;

revoke all on private.rhen_decision_trace from anon, authenticated;

-- Exact recovered edge-corpus-v1 evidence.
update private.trading_experiments
set methodology_version='edge-corpus-v1', corpus_version='edge-corpus-v1',
    universe_version='edge-corpus-v1-frozen-36', forward_horizons_minutes=array[15],
    cost_assumptions='{"base":{"spread_bps":5,"slippage_bps_per_side":2},"moderate":{"spread_bps":8,"slippage_bps_per_side":3},"stress":{"spread_bps":12,"slippage_bps_per_side":5,"round_trip_bps":22}}'::jsonb,
    sample_count=6482,
    robustness_metrics=metrics || jsonb_build_object('decisive_period','dev-01','decisive_scenario','stress','worst_period_floor',-0.0015),
    terminal_decision='rejected', terminal_reason='all five families violated the frozen development gate under stress costs',
    code_commit='cd98ea04f48047219ff9bd94c462488aa077b9c6',
    code_build_id='979b8b7e-73e9-469a-802c-032afd301c1e',
    artifact_location='github:anevum/alpaca-trader@exit-idempotency-v1 + railway:7320cae2-11a5-4275-9070-425456094908'
where experiment_key='edge-corpus-v1';

update private.trading_experiments
set methodology_version='residual-downshock-rebound-v2.1-design',
    corpus_version=null, universe_version=null, forward_horizons_minutes=array[30],
    terminal_decision='not_run', terminal_reason='selected research direction; implementation and execution not authorized',
    parent_experiment_id=(select experiment_id from private.trading_experiments where experiment_key='edge-corpus-v1')
where experiment_key='edge-discovery-v2-residual-downshock-rebound-v2.1';

with experiment as (select experiment_id from private.trading_experiments where experiment_key='edge-corpus-v1'), rows(result_key,family,event_count,expectancy,profit_factor) as (
  values
  ('dev-01:stress:controlled_continuation','controlled_continuation',3395,-0.002105588302178535877716568324::numeric,0.08816595204899464967732427305::numeric),
  ('dev-01:stress:pullback_reclaim','pullback_reclaim',675,-0.001910869302183044664709823489::numeric,0.2238205156456083805128215539::numeric),
  ('dev-01:stress:compression_breakout','compression_breakout',1853,-0.002079992539907900039464625277::numeric,0.08470552995283457235425270757::numeric),
  ('dev-01:stress:relative_strength_impulse','relative_strength_impulse',343,-0.002145623061058875343732037924::numeric,0.1611456672589131336825406798::numeric),
  ('dev-01:stress:opening_breakout_retest','opening_breakout_retest',216,-0.002110486225484704787422514003::numeric,0.1680447891393155164890009549::numeric)
)
insert into private.trading_experiment_results(experiment_id,result_key,family,stage,period_id,cost_scenario,event_count,expectancy,profit_factor,worst_period_expectancy,threshold_floor,terminal_decision,terminal_reason_code,metrics)
select experiment_id,result_key,family,'development','dev-01','stress',event_count,expectancy,profit_factor,expectancy,-0.0015,'rejected','worst_period_below_floor',jsonb_build_object('positive_periods_seen',0,'periods_seen',1,'periods_remaining',5,'minimum_positive_periods',4,'irreversibly_rejected',true)
from experiment cross join rows
on conflict (experiment_id,result_key) do update set event_count=excluded.event_count,expectancy=excluded.expectancy,profit_factor=excluded.profit_factor,metrics=excluded.metrics;

with experiment as (select experiment_id from private.trading_experiments where experiment_key='edge-corpus-v1'), universe as (
 select array['AAPL','MSFT','NVDA','AMD','AMZN','META','GOOGL','TSLA','AVGO','MU','JPM','BAC','XOM','CVX','LLY','UNH','COST','WMT','CAT','BA','NKE','MRVL','PLTR','IWM','DIA','XLK','XLF','XLE','XLV','XLI','XLY','XLP','XLC','XBI','KRE','XRT']::text[] symbols
), windows(window_key,role,starts_on,ends_on,status,density) as (
 values
 ('dev-01','development','2026-01-05'::date,'2026-01-23'::date,'evaluated',0.636987::numeric),
 ('dev-02','development','2026-01-26'::date,'2026-02-13'::date,'integrity_verified',0.616968::numeric),
 ('dev-03','development','2026-02-17'::date,'2026-03-06'::date,'integrity_verified',0.574919::numeric),
 ('dev-04','development','2026-03-09'::date,'2026-03-27'::date,'integrity_verified',0.498703::numeric),
 ('dev-05','development','2026-03-30'::date,'2026-04-17'::date,'integrity_verified',0.557012::numeric),
 ('dev-06','development','2026-04-20'::date,'2026-05-08'::date,'integrity_verified',0.460010::numeric),
 ('val-01','validation','2026-05-18'::date,'2026-06-05'::date,'unopened',null::numeric),
 ('val-02','validation','2026-06-08'::date,'2026-06-26'::date,'unopened',null::numeric),
 ('holdout-01','holdout','2026-07-13'::date,'2026-07-31'::date,'unopened',null::numeric),
 ('quarantine-01','quarantine','2026-08-03'::date,'2026-08-21'::date,'unopened',null::numeric),
 ('quarantine-02','quarantine','2026-08-24'::date,'2026-09-11'::date,'unopened',null::numeric),
 ('quarantine-03','quarantine','2026-09-14'::date,'2026-09-25'::date,'unopened',null::numeric)
)
insert into private.trading_experiment_windows(experiment_id,window_key,role,starts_on,ends_on,status,data_source,data_feed,bar_interval,expected_symbols,present_symbols,context_symbols,pagination_complete,missing_sessions,completeness_basis,completeness_ratio,density_diagnostic,details)
select experiment_id,window_key,role,starts_on,ends_on,status,'alpaca','iex','1Min',symbols,
 case when status in ('evaluated','integrity_verified') then symbols else '{}'::text[] end,
 array['SPY','QQQ','SMH'], case when status in ('evaluated','integrity_verified') then true else null end,
 '{}'::jsonb,'expected_regular_session_representation',case when status in ('evaluated','integrity_verified') then 1 else null end,
 case when density is null then '{}'::jsonb else jsonb_build_object('minimum_iex_bar_density_ratio',density,'qualification_role','diagnostic_only') end,
 jsonb_build_object('validation_opened',false,'holdout_opened',false)
from experiment cross join universe cross join windows
on conflict (experiment_id,window_key) do update set status=excluded.status,present_symbols=excluded.present_symbols,pagination_complete=excluded.pagination_complete,completeness_ratio=excluded.completeness_ratio,density_diagnostic=excluded.density_diagnostic;

insert into private.trading_experiment_symbol_coverage(experiment_window_id,symbol,expected,present,session_completeness,pagination_complete,diagnostics)
select w.experiment_window_id,symbol,true,true,true,true,jsonb_build_object('session_counts','unknown','source','corrected Railway corpus-integrity run')
from private.trading_experiment_windows w
join private.trading_experiments e on e.experiment_id=w.experiment_id
cross join lateral unnest(w.expected_symbols) symbol
where e.experiment_key='edge-corpus-v1' and w.status in ('evaluated','integrity_verified')
on conflict (experiment_window_id,symbol) do update set present=excluded.present,session_completeness=excluded.session_completeness,pagination_complete=excluded.pagination_complete;

update private.trading_experiment_symbol_coverage c
set bar_count=v.bar_count, expected_session_count=14, represented_session_count=14,
    diagnostics=c.diagnostics || jsonb_build_object('old_density_rule_role','falsified_diagnostic_only')
from private.trading_experiment_windows w
join private.trading_experiments e on e.experiment_id=w.experiment_id
join (values ('CAT',3806),('DIA',4161),('COST',4188),('LLY',4237),('BA',4428),('XRT',4446),('XLC',4468),('XLY',4578)) v(symbol,bar_count) on true
where c.experiment_window_id=w.experiment_window_id and e.experiment_key='edge-corpus-v1'
  and w.window_key='dev-01' and c.symbol=v.symbol;

insert into private.trading_research_decisions(decision_key,decided_at,status,decision_type,subject,conclusion,methodology_version,evidence,code_commit,deployment_id)
values
('edge-discovery-v1-terminal','2026-09-26T06:21:41.041154Z','final','terminal_rejection','Edge Discovery v1','All five frozen families were rejected in development under edge-corpus-v1 stress costs. Validation and holdout remained unopened because no family survived.','edge-corpus-v1','{"families":["controlled_continuation","pullback_reclaim","compression_breakout","relative_strength_impulse","opening_breakout_retest"],"validation_opened":false,"holdout_opened":false,"shared_panel":"36/36"}'::jsonb,'cd98ea04f48047219ff9bd94c462488aa077b9c6','7320cae2-11a5-4275-9070-425456094908'),
('residual-downshock-rebound-v2.1-selected','2026-09-26T09:04:00-04:00','selected_unexecuted','next_research_direction','Residual Downshock Rebound v2.1','Selected as the next research direction using synchronized five-minute observations and a 30-minute primary forward-return horizon. It has not been implemented or run.','residual-downshock-rebound-v2.1-design','{"observation_interval":"5Min synchronized","primary_forward_horizon_minutes":30,"implemented":false,"executed":false}'::jsonb,null,null)
on conflict (decision_key) do update set conclusion=excluded.conclusion,evidence=excluded.evidence,status=excluded.status;

insert into private.trading_research_decision_experiments(decision_id,experiment_id,evidence_role)
select d.decision_id,e.experiment_id,'terminal_evidence'
from private.trading_research_decisions d join private.trading_experiments e on e.experiment_key='edge-corpus-v1'
where d.decision_key='edge-discovery-v1-terminal'
on conflict do nothing;
insert into private.trading_research_decision_experiments(decision_id,experiment_id,evidence_role)
select d.decision_id,e.experiment_id,'selected_direction'
from private.trading_research_decisions d join private.trading_experiments e on e.experiment_key='edge-discovery-v2-residual-downshock-rebound-v2.1'
where d.decision_key='residual-downshock-rebound-v2.1-selected'
on conflict do nothing;

insert into private.trading_runtime_instances(runtime_instance_id,run_id,strategy_version_id,deployment_id,build_id,git_commit,repository,branch,service_id,service_name,environment_id,environment_name,system_version,started_at,metadata)
select distinct on (payload#>>'{runtime,runtime_instance_id}')
 payload#>>'{runtime,runtime_instance_id}',run_id,strategy_version_id,payload#>>'{runtime,deployment_id}',payload#>>'{runtime,snapshot_id}',payload#>>'{runtime,git_commit}',payload#>>'{runtime,repository}',payload#>>'{runtime,branch}',payload#>>'{runtime,service_id}',payload#>>'{runtime,service_name}',payload#>>'{runtime,environment_id}',payload#>>'{runtime,environment_name}',payload#>>'{runtime,system_version}',coalesce(nullif(payload#>>'{runtime,runtime_started_at}','')::timestamptz,occurred_at),payload
from private.trading_events where event_type='runtime_start' and coalesce(payload#>>'{runtime,runtime_instance_id}','')<>''
order by payload#>>'{runtime,runtime_instance_id}',occurred_at
on conflict (runtime_instance_id) do update set metadata=private.trading_runtime_instances.metadata || excluded.metadata;

-- Honest partial backfill: only actually persisted qualified signals are reconstructed.
insert into private.trading_scan_cycles(cycle_key,run_id,strategy_version_id,observed_at,cycle_started_at,cycle_ended_at,market_is_open,symbol_count,candidate_count,qualified_count,rejected_count,cycle_outcome,data_status,degraded,execution_mode,market_session,symbols_evaluated,data_source,data_feed,bar_interval,methodology_version,strategy_family,metadata)
select 'historical-signal:'||s.signal_id,s.run_id,s.strategy_version_id,s.signal_at,s.signal_at,s.signal_at,true,1,1,1,0,'historical qualified signal backfill','partial_backfill',false,'live','regular',array[s.symbol],'alpaca',null,null,null,v.strategy_name,jsonb_build_object('backfill_scope','qualified signals only','unknown_fields_preserved_as_null',true)
from private.trading_signals s join private.trading_strategy_versions v on v.version_id=s.strategy_version_id
where s.candidate_id is null
on conflict (cycle_key) where cycle_key is not null do nothing;

insert into private.trading_candidate_evaluations(scan_cycle_id,run_id,strategy_version_id,symbol,observed_at,action,qualified,reason,candidate_rank,features,checks,candidate_state,rejection_reasons,data_quality_state,decision_reference_price,forward_outcomes_status,research_attribution,candidate_key,qualification_status,final_decision,rejection_reason_codes,market_context,eligibility,constraints,strategy_family,methodology_version,data_source,signal_id)
select sc.scan_cycle_id,s.run_id,s.strategy_version_id,s.symbol,s.signal_at,'buy',true,s.payload->>'reason',1,coalesce(s.payload->'metadata','{}'::jsonb),coalesce(s.payload#>'{metadata,checks}','{}'::jsonb),'historical_qualified','[]'::jsonb,'historical_unknown',s.reference_price,'not_applicable',jsonb_build_object('backfill','qualified_signal','source_signal_id',s.signal_id),'historical-signal:'||s.signal_id||':'||s.symbol,'strategy_qualified',case when exists(select 1 from private.trading_order_intents i join private.trading_orders o on o.order_intent_id=i.intent_id where i.signal_id=s.signal_id) then 'submitted' else 'qualified' end,'{}'::text[],jsonb_build_object('confirmations',coalesce(s.payload#>'{metadata,confirmations}','{}'::jsonb),'regime_confirmations',coalesce(s.payload#>'{metadata,regime_confirmations}','{}'::jsonb)),jsonb_build_object('historically_recoverable',true),jsonb_build_object('sizing',coalesce(s.payload#>'{metadata,sizing}','{}'::jsonb)),v.strategy_name,null,'alpaca',s.signal_id
from private.trading_signals s
join private.trading_scan_cycles sc on sc.cycle_key='historical-signal:'||s.signal_id
join private.trading_strategy_versions v on v.version_id=s.strategy_version_id
on conflict (scan_cycle_id,symbol) do update set signal_id=coalesce(private.trading_candidate_evaluations.signal_id,excluded.signal_id),candidate_key=coalesce(private.trading_candidate_evaluations.candidate_key,excluded.candidate_key);

update private.trading_signals s set candidate_id=c.candidate_id
from private.trading_candidate_evaluations c
where c.signal_id=s.signal_id and s.candidate_id is null;

update private.trading_positions p
set entry_reason=coalesce(p.entry_reason,s.payload->>'reason'),
    initial_stop_price=coalesce(p.initial_stop_price,s.stop_price),
    target_price=coalesce(p.target_price,s.target_price),
    entry_reference_price=coalesce(p.entry_reference_price,s.reference_price),
    realized_return=case when p.avg_entry_price>0 and p.avg_exit_price is not null then (p.avg_exit_price/p.avg_entry_price)-1 else p.realized_return end,
    target_touched=case when p.peak_favorable_price is not null and s.target_price is not null then p.peak_favorable_price>=s.target_price else p.target_touched end,
    stop_touched=case when p.peak_adverse_price is not null and s.stop_price is not null then p.peak_adverse_price<=s.stop_price else p.stop_touched end,
    context_tags=p.context_tags || jsonb_build_object('strategy_checks',coalesce(s.payload#>'{metadata,checks}','{}'::jsonb),'market_confirmations',coalesce(s.payload#>'{metadata,confirmations}','{}'::jsonb),'regime_confirmations',coalesce(s.payload#>'{metadata,regime_confirmations}','{}'::jsonb))
from private.trading_orders o join private.trading_order_intents i on i.intent_id=o.order_intent_id join private.trading_signals s on s.signal_id=i.signal_id
where p.payload->>'entry_order_id'=o.broker_order_id;

comment on table private.trading_candidate_forward_outcomes is 'Post-event analytics only; never read by live qualification or execution paths.';
comment on view private.rhen_decision_trace is 'Private canonical decision-to-outcome trace. Not granted to anonymous or authenticated clients.';
