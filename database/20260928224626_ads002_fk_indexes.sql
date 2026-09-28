create index if not exists trading_ads_attribution_intent_idx
  on private.trading_ads_attribution(intent_id)
  where intent_id is not null;

create index if not exists trading_ads_attribution_entry_order_idx
  on private.trading_ads_attribution(entry_order_id)
  where entry_order_id is not null;

create index if not exists trading_ads_attribution_entry_fill_idx
  on private.trading_ads_attribution(entry_fill_id)
  where entry_fill_id is not null;

create index if not exists trading_ads_attribution_position_idx
  on private.trading_ads_attribution(position_id)
  where position_id is not null;

create index if not exists trading_ads_attribution_exit_idx
  on private.trading_ads_attribution(exit_id)
  where exit_id is not null;

create index if not exists trading_ads_attribution_exit_order_idx
  on private.trading_ads_attribution(exit_order_id)
  where exit_order_id is not null;

create index if not exists trading_ads_attribution_exit_fill_idx
  on private.trading_ads_attribution(exit_fill_id)
  where exit_fill_id is not null;

create index if not exists trading_ads_shadow_scores_run_idx
  on private.trading_ads_shadow_scores(run_id);

create index if not exists trading_ads_shadow_scores_strategy_idx
  on private.trading_ads_shadow_scores(strategy_version_id);
