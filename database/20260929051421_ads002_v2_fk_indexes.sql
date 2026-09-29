create index if not exists trading_ads_challenger_scores_run_idx
  on private.trading_ads_challenger_scores(run_id);

create index if not exists trading_ads_challenger_scores_strategy_idx
  on private.trading_ads_challenger_scores(strategy_version_id);
