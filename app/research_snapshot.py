from __future__ import annotations

from typing import Any

from .config import Settings


def research_config_snapshot(settings: Settings) -> dict[str, Any]:
    """Sanitized strategy/runtime configuration needed for reproducible research.

    Credentials, tokens, URLs containing auth material, and administrative
    secrets are intentionally excluded.
    """
    return {
        "strategy": {
            "strategy_name": settings.strategy_name,
            "strategy_symbol": settings.strategy_symbol,
            "scan_symbols": list(settings.scan_symbols),
            "confirmation_symbols": list(settings.confirmation_symbols),
            "fast_window": settings.fast_window,
            "slow_window": settings.slow_window,
            "min_momentum_pct": str(settings.min_momentum_pct),
            "min_vwap_edge_pct": str(settings.min_vwap_edge_pct),
            "min_confirmations": settings.min_confirmations,
            "regime_window": settings.regime_window,
            "regime_min_confirmations": settings.regime_min_confirmations,
            "regime_min_return_pct": str(settings.regime_min_return_pct),
            "max_vwap_extension_pct": str(settings.max_vwap_extension_pct),
            "signal_persistence_bars": settings.signal_persistence_bars,
            "entry_start": settings.entry_start_raw,
            "entry_cutoff": settings.entry_cutoff_raw,
            "force_flat_time": settings.force_flat_time_raw,
            "max_hold_minutes": settings.max_hold_minutes,
            "reentry_cooldown_minutes": settings.reentry_cooldown_minutes,
            "stop_pct": str(settings.stop_pct),
            "target_pct": str(settings.target_pct),
            "min_quality_score": str(settings.min_quality_score),
        },
        "market_data": {
            "bar_timeframe": settings.bar_timeframe,
            "lookback_bars": settings.lookback_bars,
            "lookback_days": settings.lookback_days,
            "data_feed": settings.data_feed,
            "poll_seconds": settings.poll_seconds,
            "max_bar_age_seconds": settings.max_bar_age_seconds,
            "max_spread_pct": str(settings.max_spread_pct),
            "market_data_batch_size": settings.market_data_batch_size,
        },
        "universe": {
            "dynamic_universe_enabled": settings.dynamic_universe_enabled,
            "universe_size": settings.universe_size,
            "candidate_pool_size": settings.universe_candidate_pool_size,
            "refresh_seconds": settings.universe_refresh_seconds,
            "daily_lookback": settings.universe_daily_lookback,
            "data_batch_size": settings.universe_data_batch_size,
            "min_price": str(settings.universe_min_price),
            "min_avg_volume": str(settings.universe_min_avg_volume),
            "min_avg_dollar_volume": str(
                settings.universe_min_avg_dollar_volume
            ),
            "exchanges": list(settings.universe_exchanges),
            "always_include": list(settings.universe_always_include),
        },
        "risk_and_sizing": {
            "sizing_mode": settings.sizing_mode,
            "portfolio_limit_mode": settings.portfolio_limit_mode,
            "risk_per_trade_pct": str(settings.risk_per_trade_pct),
            "max_position_gross_pct": str(settings.max_position_gross_pct),
            "max_gross_exposure_pct": str(settings.max_gross_exposure_pct),
            "max_portfolio_stop_risk_pct": str(
                settings.max_portfolio_stop_risk_pct
            ),
            "max_order_notional": str(settings.max_order_notional),
            "max_position_notional": str(settings.max_position_notional),
            "max_total_position_notional": str(
                settings.max_total_position_notional
            ),
            "min_order_notional": str(settings.min_order_notional),
            "min_ready_cash": str(settings.min_ready_cash),
            "max_concurrent_positions": settings.max_concurrent_positions,
            "max_new_entries_per_cycle": settings.max_new_entries_per_cycle,
            "max_daily_orders": settings.max_daily_orders,
            "max_daily_loss": str(settings.max_daily_loss),
        },
        "execution_and_exits": {
            "broker_protective_stop_enabled": (
                settings.broker_protective_stop_enabled
            ),
            "volatility_stop_enabled": settings.volatility_stop_enabled,
            "volatility_stop_multiplier": str(
                settings.volatility_stop_multiplier
            ),
            "volatility_stop_lookback_bars": (
                settings.volatility_stop_lookback_bars
            ),
            "max_dynamic_stop_pct": str(settings.max_dynamic_stop_pct),
            "profit_protect_enabled": settings.profit_protect_enabled,
            "profit_protect_activation_pct": str(
                settings.profit_protect_activation_pct
            ),
            "profit_protect_retain_fraction": str(
                settings.profit_protect_retain_fraction
            ),
            "profit_protect_min_pct": str(settings.profit_protect_min_pct),
            "profit_stop_step_pct": str(settings.profit_stop_step_pct),
            "thesis_exit_enabled": settings.thesis_exit_enabled,
            "thesis_failure_cycles": settings.thesis_failure_cycles,
            "thesis_exit_max_return_pct": str(
                settings.thesis_exit_max_return_pct
            ),
            "loss_streak_limit": settings.loss_streak_limit,
            "loss_streak_cooldown_minutes": (
                settings.loss_streak_cooldown_minutes
            ),
        },
        "correlation": {
            "max_pairwise_correlation": str(
                settings.max_pairwise_correlation
            ),
            "lookback_bars": settings.correlation_lookback_bars,
            "min_observations": settings.correlation_min_observations,
        },
    }
