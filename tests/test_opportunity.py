from datetime import datetime, timedelta, timezone
from decimal import Decimal

from app.config import Settings
from app.opportunity import return_correlation, score_opportunity
from app.strategy import Signal


def settings(**overrides):
    base = dict(
        ALPACA_API_KEY="x",
        ALPACA_API_SECRET="y",
        TRADING_MODE="paper",
        STRATEGY_SYMBOL="SPY",
        SCAN_SYMBOLS="SPY",
        ALLOWED_SYMBOLS="SPY",
        CONFIRMATION_SYMBOLS="QQQ,SMH",
        MIN_CONFIRMATIONS="1",
        MIN_MOMENTUM_PCT="0.0005",
        TARGET_PCT="0.005",
        MAX_SPREAD_PCT="0.002",
        MAX_PAIRWISE_CORRELATION="0.85",
        CORRELATION_LOOKBACK_BARS="30",
        CORRELATION_MIN_OBSERVATIONS="8",
        POLL_SECONDS="15",
    )
    base.update(overrides)
    return Settings(**base)


def bars(prices, volumes=None):
    base = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    volumes = volumes or [1000] * len(prices)
    return [
        {
            "t": (base - timedelta(minutes=len(prices) - index)).isoformat(),
            "c": str(price),
            "v": str(volumes[index]),
        }
        for index, price in enumerate(prices)
    ]


def signal(momentum, vwap):
    return Signal(
        action="buy",
        symbol="SPY",
        metadata={
            "momentum_pct": str(momentum),
            "vwap_edge_pct": str(vwap),
            "confirmation_passes": 2,
            "confirmations": {
                "QQQ": {"ok": True},
                "SMH": {"ok": True},
            },
        },
    )


def test_quality_score_rewards_stronger_signal_and_tighter_spread():
    s = settings()
    observed = bars(
        [100, 100.04, 100.02, 100.10, 100.08, 100.18, 100.16, 100.28, 100.35],
        [800, 850, 900, 950, 1000, 1050, 1100, 1200, 1800],
    )
    strong = score_opportunity(
        s,
        signal("0.002", "0.004"),
        observed,
        {
            "spread_pct": "0.0002",
            "bar_age_seconds": 5,
            "fresh_confirmation_passes": 2,
        },
    )
    weak = score_opportunity(
        s,
        signal("0.0005", "0.0002"),
        observed,
        {
            "spread_pct": "0.0018",
            "bar_age_seconds": 80,
            "fresh_confirmation_passes": 1,
        },
    )
    assert 0 <= weak["score"] <= 100
    assert 0 <= strong["score"] <= 100
    assert strong["score"] > weak["score"]


def test_return_correlation_detects_nearly_identical_intraday_returns():
    left = bars([100, 101, 100.5, 102, 101.5, 103, 102.7, 104, 103.4, 105, 104.5])
    right = bars([50, 50.5, 50.25, 51, 50.75, 51.5, 51.35, 52, 51.7, 52.5, 52.25])
    coefficient, observations = return_correlation(
        left,
        right,
        lookback_bars=30,
        min_observations=8,
    )
    assert observations == 10
    assert coefficient is not None
    assert coefficient > 0.99


def test_return_correlation_fails_open_when_history_is_insufficient():
    left = bars([100, 101, 102])
    right = bars([50, 50.5, 51])
    coefficient, observations = return_correlation(
        left,
        right,
        lookback_bars=30,
        min_observations=8,
    )
    assert coefficient is None
    assert observations == 2
