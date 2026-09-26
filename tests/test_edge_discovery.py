from datetime import datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from app.config import Settings
from app.edge_discovery import (
    EdgeDiscoveryStudy,
    detect_families,
    robust_edge_gate,
)


NY = ZoneInfo("America/New_York")


def bar(at, o, h, l, c, v=100):
    return {
        "t": at.isoformat(),
        "o": str(o),
        "h": str(h),
        "l": str(l),
        "c": str(c),
        "v": str(v),
        "vw": str(c),
    }


def settings():
    return Settings(
        ALPACA_API_KEY="x",
        ALPACA_API_SECRET="y",
        TRADING_MODE="paper",
        STRATEGY_NAME="rolling_momentum_vwap",
        STRATEGY_SYMBOL="AAPL",
        SCAN_SYMBOLS="AAPL",
        ALLOWED_SYMBOLS="AAPL,SPY,QQQ,SMH",
        CONFIRMATION_SYMBOLS="SPY,QQQ,SMH",
        MIN_CONFIRMATIONS="1",
        ORDER_NOTIONAL="20",
        MAX_ORDER_NOTIONAL="80",
        MAX_POSITION_NOTIONAL="80",
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
        FAST_WINDOW="3",
        SLOW_WINDOW="8",
        MIN_MOMENTUM_PCT="0.0005",
        MIN_VWAP_EDGE_PCT="0",
        MAX_SPREAD_PCT="0.002",
    )


def confirmation_series(start):
    bars = []
    value = Decimal("100")
    for i in range(30):
        next_value = value * Decimal("1.0002")
        bars.append(
            bar(
                start + timedelta(minutes=i),
                value,
                next_value,
                value * Decimal("0.9998"),
                next_value,
                100 + i,
            )
        )
        value = next_value
    return bars


def test_detect_families_is_not_tied_to_production_signal():
    start = datetime(2026, 9, 1, 9, 30, tzinfo=NY)
    candidate = []
    value = Decimal("100")
    for i in range(24):
        next_value = value * Decimal("1.0003")
        candidate.append(
            bar(
                start + timedelta(minutes=i),
                value,
                next_value * Decimal("1.0002"),
                value * Decimal("0.9998"),
                next_value,
                100 + i,
            )
        )
        value = next_value

    confirmations = {
        symbol: confirmation_series(start)[:24]
        for symbol in ("SPY", "QQQ", "SMH")
    }

    families = detect_families(
        candidate,
        confirmations,
        now=start + timedelta(minutes=24),
    )

    assert isinstance(families, dict)
    assert set(families) <= {
        "controlled_continuation",
        "pullback_reclaim",
        "compression_breakout",
        "relative_strength_impulse",
        "opening_breakout_retest",
    }


def test_edge_study_never_authorizes_live_change():
    start = datetime(2026, 9, 1, 9, 30, tzinfo=NY)
    series = confirmation_series(start)
    bars = {
        "AAPL": series,
        "SPY": series,
        "QQQ": series,
        "SMH": series,
    }

    result = EdgeDiscoveryStudy(
        settings(),
        ("AAPL",),
        event_cooldown_minutes=15,
    ).run(
        bars,
        spread_bps=Decimal("5"),
        slippage_bps=Decimal("2"),
        horizon=15,
    )

    assert result["status"] == "research_only"
    assert result["assumptions"]["broker_orders_possible"] is False


def test_robust_edge_gate_requires_every_cost_scenario():
    passing = {
        "events": 80,
        "expectancy_pct": "0.001",
        "profit_factor": "1.4",
        "positive_periods": 3,
        "session_expectancy_lower_95_pct": "0.0001",
    }
    failing = {
        "events": 80,
        "expectancy_pct": "-0.0002",
        "profit_factor": "0.9",
        "positive_periods": 1,
        "session_expectancy_lower_95_pct": "-0.0005",
    }

    scenarios = [
        {
            "scenario": "base",
            "aggregate_by_family": {
                "controlled_continuation": passing,
            },
        },
        {
            "scenario": "stress",
            "aggregate_by_family": {
                "controlled_continuation": failing,
            },
        },
    ]

    result = robust_edge_gate(
        scenarios,
        min_events=60,
        min_positive_periods=2,
        min_profit_factor=Decimal("1.20"),
    )

    assert "controlled_continuation" not in result["passing_families"]
    assert (
        result["families"]["controlled_continuation"]["robust_historical_edge"]
        is False
    )
    assert result["promotion_authorized"] is False
