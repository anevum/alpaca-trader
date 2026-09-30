-- Register CRR-001 as a shadow-only strategy identity before its events are ingested.
-- This is data registration only. It grants no broker, risk, sizing, or execution authority.

insert into private.trading_strategy_versions (
  version_id,
  strategy_name,
  status,
  repository,
  git_commit,
  deployment_id,
  environment,
  hypothesis,
  parameters,
  symbol_universe,
  risk_limits,
  activated_at
) values (
  'CRYPTO-RESIDUAL-RECLAIM-001',
  'controlled_residual_reversal',
  'shadow',
  'anevum/alpaca-trader',
  'a4b5147c55c1ffcf46c1b1640b9affde2403fedc',
  '91c695ce-affb-48d7-8379-968419f501ec',
  'shadow',
  'Test whether a large negative 15-minute leave-one-out crypto market residual, followed by a frozen reclaim confirmation, produces positive stressed-cost expectancy over a fixed 120-minute horizon.',
  jsonb_build_object(
    'market_lane','crypto',
    'methodology_version','graen-crypto-native-v6',
    'feature_set_version','crypto-residual-reclaim-v6-snapshot-v1',
    'cost_snapshot_version','graen-crypto-cost-snapshot-v5',
    'validation_state','DEVELOPMENT_CONTINUES',
    'mode','shadow',
    'bar_minutes',5,
    'shock_lookback_minutes',15,
    'residual_vol_lookback_minutes',360,
    'shock_sigma_multiple',1.5,
    'cost_hurdle_multiple',2.0,
    'reclaim_window_minutes',15,
    'hold_minutes',120,
    'execution_authority',false,
    'broker_orders_possible',false,
    'crypto_execution_enabled',false,
    'holdout_opened',false
  ),
  array['BTC/USD','ETH/USD','SOL/USD'],
  jsonb_build_object(
    'research_only',true,
    'live_execution_gated',true,
    'graen_promotion_required',true,
    'validation_passed',false,
    'holdout_passed',false,
    'broker_orders_possible',false
  ),
  '2026-09-30T15:53:53.463Z'::timestamptz
)
on conflict (version_id) do update
set strategy_name = excluded.strategy_name,
    status = excluded.status,
    repository = excluded.repository,
    git_commit = excluded.git_commit,
    deployment_id = excluded.deployment_id,
    environment = excluded.environment,
    hypothesis = excluded.hypothesis,
    parameters = excluded.parameters,
    symbol_universe = excluded.symbol_universe,
    risk_limits = excluded.risk_limits,
    activated_at = excluded.activated_at;
