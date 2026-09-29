from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.research_agent.ads002_features_v2 import build_ads002_v2_features
from app.research_agent.ads002_v2 import (
    MODEL_ADD,
    MODEL_GEO,
    MODEL_RANK,
    confidence_shrink,
    continuation_value,
    net_expected_return,
    score_cycle_v2,
    select_dynamic_horizon,
)


def _bars():
    start = datetime(2026, 9, 29, 13, 30, tzinfo=timezone.utc)
    rows = []
    price = 100.0
    for i in range(12):
        open_price = price
        price = price * (1.0 + 0.0008 + (i % 3) * 0.0001)
        rows.append(
            {
                "t": (start + timedelta(minutes=i)).isoformat(),
                "o": open_price,
                "h": price * 1.0008,
                "l": open_price * 0.9995,
                "c": price,
                "v": 1000 + i * 80,
            }
        )
    return rows


def test_feature_capture_is_decision_time_only():
    features = build_ads002_v2_features(
        bars=_bars(),
        metadata={
            "symbol": "TEST",
            "fast_average": "101",
            "slow_average": "100",
            "momentum_pct": "0.004",
            "vwap_edge_pct": "0.003",
            "bar_time": "2026-09-29T09:41:00-04:00",
            "confirmations": {
                "SPY": {"ok": True},
                "QQQ": {"ok": False},
            },
            "regime_confirmations": {
                "SPY": {"ok": True},
                "QQQ": {"ok": True},
            },
        },
        market_quality={
            "spread_pct": "0.0004",
            "bar_age_seconds": 4,
            "quote_age_seconds": 1,
        },
    )
    assert features["return_1m"] is not None
    assert features["return_3m"] is not None
    assert features["return_5m"] is not None
    assert features["relative_volume_ratio"] is not None
    assert features["spread_bps"] == 4.0
    assert features["quote_age_ms"] == 1000.0
    assert features["confirmation_ratio"] == 0.5
    assert features["regime_ratio"] == 1.0
    assert features["feature_source"] == "decision_time_observed_bars"


def test_cycle_scoring_produces_three_shadow_challengers():
    candidates = []
    for i in range(5):
        candidates.append(
            {
                "symbol": f"T{i}",
                "raw_features": {
                    "relative_volume_ratio": 1.0 + i * 0.3,
                    "relative_volume_ratio_raw": 1.0 + i * 0.3,
                    "return_1m": 0.0004 + i * 0.0001,
                    "return_3m": 0.001 + i * 0.0002,
                    "return_5m": 0.0015 + i * 0.0003,
                    "abs_return_5m": 0.0015 + i * 0.0003,
                    "accel_1m": 0.00005 * i,
                    "volatility_expansion_ratio": 0.8 + i * 0.1,
                    "range_expansion_ratio": 0.9 + i * 0.1,
                    "dollar_volume_5m": 100000 + i * 25000,
                    "trend_persistence": 0.5 + i * 0.1,
                    "fast_slow_spread_pct": 0.0006 + i * 0.0002,
                    "vwap_edge_pct": 0.001 + i * 0.0005,
                    "confirmation_ratio": 0.5 + i * 0.1,
                    "regime_ratio": 0.5 + i * 0.1,
                    "spread_bps": 6 - i * 0.5,
                    "quote_age_ms": 500,
                    "bar_age_ms": 1000,
                },
            }
        )
    config = {
        "min_momentum_pct": "0.0005",
        "target_pct": "0.005",
        "max_spread_pct": "0.002",
        "max_bar_age_seconds": 90,
        "max_vwap_extension_pct": "0.008",
    }
    scored = score_cycle_v2(candidates, config)
    assert len(scored) == 5
    for row in scored:
        assert row["execution_authority"] is False
        assert row["source_completeness"]["pretrade_complete"] is True
        assert MODEL_ADD in row["challengers"]
        assert MODEL_GEO in row["challengers"]
        assert MODEL_RANK in row["challengers"]
        assert 0 <= row["challengers"][MODEL_ADD]["s_raw"] <= 1
        assert 0 <= row["challengers"][MODEL_GEO]["s_raw"] <= 1
        assert 0 <= row["challengers"][MODEL_RANK]["s_raw"] <= 1


def test_missing_microstructure_keeps_composite_incomplete():
    scored = score_cycle_v2(
        [
            {
                "symbol": "TEST",
                "raw_features": {
                    "relative_volume_ratio": 1.5,
                    "relative_volume_ratio_raw": 1.5,
                    "return_3m": 0.002,
                    "return_5m": 0.003,
                    "abs_return_5m": 0.003,
                    "volatility_expansion_ratio": 1.2,
                    "range_expansion_ratio": 1.1,
                    "dollar_volume_5m": 200000,
                    "trend_persistence": 0.75,
                    "fast_slow_spread_pct": 0.001,
                    "vwap_edge_pct": 0.002,
                    "confirmation_ratio": 1.0,
                    "regime_ratio": 1.0,
                },
            }
        ],
        {
            "min_momentum_pct": "0.0005",
            "target_pct": "0.005",
            "max_spread_pct": "0.002",
            "max_bar_age_seconds": 90,
            "max_vwap_extension_pct": "0.008",
        },
    )[0]
    assert scored["source_completeness"]["pretrade_complete"] is False
    assert scored["challengers"] == {}


def test_confidence_shrink_moves_toward_neutral():
    assert confidence_shrink(0.9, 0.0) == 0.5
    assert confidence_shrink(0.9, 1.0) == 0.9
    assert confidence_shrink(0.1, 0.5) == 0.3


def test_expected_value_horizon_and_exit_primitives():
    ev = net_expected_return(
        probability_up=0.6,
        mean_up_return=0.004,
        mean_down_return=-0.003,
        expected_round_trip_cost=0.0005,
    )
    assert ev["expected_net_return"] > 0

    horizon = select_dynamic_horizon(
        {5: 0.0006, 15: 0.0010, 30: 0.0008},
        uncertainty_penalty={5: 0.0001, 15: 0.0002, 30: 0.0005},
    )
    assert horizon["horizon_minutes"] == 15
    assert horizon["positive_edge"] is True

    x = continuation_value(
        probability_continuation=0.65,
        mean_continuation_return=0.002,
        mean_reversal_return=-0.0015,
        expected_shortfall=0.0003,
        opportunity_cost=0.0001,
        exit_cost=0.0002,
        risk_aversion=1.0,
        temperature=0.001,
    )
    assert x["execution_authority"] is False
    assert 0 <= x["hold_preference"] <= 1
