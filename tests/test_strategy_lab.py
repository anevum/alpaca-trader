from decimal import Decimal

from app.config import Settings
from app.strategy_lab import (
    DEFAULT_VARIANTS,
    StrategyTournament,
    rank_results,
    variant_settings,
)


def settings(**overrides):
    base = dict(
        ALPACA_API_KEY="x",
        ALPACA_API_SECRET="y",
        TRADING_MODE="paper",
        STRATEGY_NAME="rolling_momentum_vwap",
        STRATEGY_SYMBOL="SPY",
        SCAN_SYMBOLS="SPY",
        ALLOWED_SYMBOLS="SPY,QQQ",
        CONFIRMATION_SYMBOLS="QQQ",
        MIN_CONFIRMATIONS="1",
        ORDER_NOTIONAL="20",
        MAX_ORDER_NOTIONAL="80.35",
        MAX_POSITION_NOTIONAL="80.35",
        MAX_TOTAL_POSITION_NOTIONAL="80",
        MAX_CONCURRENT_POSITIONS="1",
        MAX_NEW_ENTRIES_PER_CYCLE="1",
        MAX_DAILY_ORDERS="12",
        MAX_DAILY_LOSS="5",
        STOP_PCT="0.0035",
        TARGET_PCT="0.005",
        ENTRY_START="09:31",
        ENTRY_CUTOFF="15:30",
        FORCE_FLAT_TIME="15:55",
        MAX_HOLD_MINUTES="15",
        SIZING_MODE="fixed",
        FAST_WINDOW="3",
        SLOW_WINDOW="8",
        MIN_MOMENTUM_PCT="0.0005",
        MIN_VWAP_EDGE_PCT="0",
        MAX_SPREAD_PCT="0.002",
        MAX_PAIRWISE_CORRELATION="0.85",
        CORRELATION_LOOKBACK_BARS="30",
        CORRELATION_MIN_OBSERVATIONS="8",
        POLL_SECONDS="15",
    )
    base.update(overrides)
    return Settings(**base)


def test_production_variant_preserves_current_parameters():
    base = settings()
    production = DEFAULT_VARIANTS[0]
    variant = variant_settings(base, production)

    assert variant.fast_window == base.fast_window
    assert variant.slow_window == base.slow_window
    assert variant.stop_pct == base.stop_pct
    assert variant.target_pct == base.target_pct
    assert variant.max_hold_minutes == base.max_hold_minutes


def test_variant_overrides_do_not_mutate_base_settings():
    base = settings()
    fast = next(item for item in DEFAULT_VARIANTS if item.name == "fast_2_6")
    variant = variant_settings(base, fast)

    assert variant.fast_window == 2
    assert variant.slow_window == 6
    assert base.fast_window == 3
    assert base.slow_window == 8


def test_small_sample_is_visible_but_ineligible():
    rows = rank_results(
        [
            {
                "variant": "tiny",
                "description": "",
                "parameters": {},
                "summary": {
                    "trades": 2,
                    "expectancy_per_trade": "2",
                    "profit_factor": 10,
                    "gross_profit": "4",
                    "gross_loss": "0.4",
                    "return_pct": "0.1",
                    "max_drawdown_pct": "0.01",
                },
                "assumptions": {},
            },
            {
                "variant": "sampled",
                "description": "",
                "parameters": {},
                "summary": {
                    "trades": 30,
                    "expectancy_per_trade": "0.10",
                    "profit_factor": 1.4,
                    "gross_profit": "6",
                    "gross_loss": "4",
                    "return_pct": "0.03",
                    "max_drawdown_pct": "0.02",
                },
                "assumptions": {},
            },
        ],
        min_trades=20,
    )

    assert rows[0]["variant"] == "sampled"
    assert rows[0]["eligible_for_ranking"] is True
    tiny = next(row for row in rows if row["variant"] == "tiny")
    assert tiny["eligible_for_ranking"] is False
    assert "2/20" in tiny["sample_note"]


def test_default_tournament_has_control_and_research_variants():
    tournament = StrategyTournament(settings())
    names = [variant.name for variant in tournament.variants]

    assert names[0] == "production"
    assert {"fast_2_6", "balanced_4_10", "selective_3_10", "slow_5_15"} <= set(names)
