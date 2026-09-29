from app.research_agent.nostra_regime import infer_market_regime


def summary(**overrides):
    base = {
        "breadth_above_vwap": 0.78,
        "breadth_positive_5m": 0.72,
        "median_fast_slow_spread_pct": 0.0015,
        "median_vwap_edge_pct": 0.0018,
        "median_abs_return_5m": 0.003,
        "cross_sectional_dispersion_5m": 0.004,
        "median_relative_volume": 1.4,
        "eligible_candidate_ratio": 0.12,
    }
    base.update(overrides)
    return base


def test_broad_positive_market_is_not_unknown():
    result = infer_market_regime(
        benchmark_returns={
            "SPY_5m": 0.002,
            "QQQ_5m": 0.003,
            "IWM_5m": 0.0015,
            "SPY_15m": 0.004,
            "QQQ_15m": 0.005,
            "IWM_15m": 0.003,
        },
        candidate_summary=summary(),
        market_quality={
            "realized_volatility_5m": 0.0035,
            "average_spread_bps": 5,
        },
        session_context={"time_segment": "OPENING", "minutes_from_open": 35},
    )
    assert result["regime"] in {"BROAD_ADVANCE", "TREND_EXPANSION", "OPENING_DISCOVERY"}
    assert result["regime"] != "UNKNOWN"
    assert result["market_familiarity"] > 0.5
    assert result["execution_authority"] is False


def test_broad_negative_market_identifies_decline_or_trend():
    result = infer_market_regime(
        benchmark_returns={
            "SPY_5m": -0.003,
            "QQQ_5m": -0.004,
            "IWM_5m": -0.0035,
            "SPY_15m": -0.005,
            "QQQ_15m": -0.006,
            "IWM_15m": -0.005,
        },
        candidate_summary=summary(
            breadth_above_vwap=0.18,
            breadth_positive_5m=0.15,
            median_fast_slow_spread_pct=-0.002,
            median_vwap_edge_pct=-0.002,
        ),
        market_quality={"realized_volatility_5m": 0.005},
        session_context={"time_segment": "MIDDAY"},
    )
    assert result["regime"] in {"BROAD_DECLINE", "TREND_EXPANSION", "HIGH_VOLATILITY"}


def test_missing_features_raise_unknown_and_lower_familiarity():
    result = infer_market_regime(
        benchmark_returns={"SPY_5m": 0.001},
        candidate_summary={},
        market_quality={},
        session_context={"time_segment": "OTHER"},
    )
    assert result["unknown_probability"] > 0.15
    assert result["market_familiarity"] < 0.5


def test_midday_quiet_state_can_surface_compression():
    result = infer_market_regime(
        benchmark_returns={
            "SPY_5m": 0.0001,
            "QQQ_5m": -0.0001,
            "IWM_5m": 0.0,
            "SPY_15m": 0.0002,
            "QQQ_15m": 0.0001,
            "IWM_15m": -0.0001,
        },
        candidate_summary=summary(
            breadth_above_vwap=0.51,
            breadth_positive_5m=0.49,
            median_fast_slow_spread_pct=0.00005,
            median_vwap_edge_pct=0.0001,
            median_abs_return_5m=0.0005,
            cross_sectional_dispersion_5m=0.001,
            median_relative_volume=0.8,
        ),
        market_quality={"realized_volatility_5m": 0.0006},
        session_context={"time_segment": "MIDDAY"},
    )
    probs = result["probabilities"]
    assert probs["MIDDAY_COMPRESSION"] > probs["TREND_EXPANSION"]


def test_conflicting_short_and_medium_horizon_state_lowers_confidence():
    coherent = infer_market_regime(
        benchmark_returns={
            "SPY_5m": 0.002,
            "QQQ_5m": 0.002,
            "IWM_5m": 0.002,
            "SPY_15m": 0.004,
            "QQQ_15m": 0.004,
            "IWM_15m": 0.004,
        },
        candidate_summary=summary(),
        session_context={"time_segment": "OTHER"},
    )
    conflict = infer_market_regime(
        benchmark_returns={
            "SPY_5m": -0.002,
            "QQQ_5m": -0.002,
            "IWM_5m": -0.002,
            "SPY_15m": 0.004,
            "QQQ_15m": 0.004,
            "IWM_15m": 0.004,
        },
        candidate_summary=summary(
            breadth_above_vwap=0.50,
            breadth_positive_5m=0.50,
            median_fast_slow_spread_pct=0.0,
            median_vwap_edge_pct=0.0,
        ),
        session_context={"time_segment": "OTHER"},
    )
    assert conflict["confidence"] < coherent["confidence"]
    assert conflict["diagnostics"]["contradiction"] > coherent["diagnostics"]["contradiction"]
