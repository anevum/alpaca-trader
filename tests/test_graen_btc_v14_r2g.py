from __future__ import annotations

from datetime import datetime, timedelta, timezone

from graen.crypto.btc_consensus_trend_v14_r2g import (
    COST_SCENARIOS,
    MOMENTUM_LOOKBACK_DAYS,
    SMA_WINDOW_DAYS,
    campaign_manifest,
    evaluate_btc_consensus_trend_discovery,
)

UTC = timezone.utc


def _bars(*, count: int = 2100):
    start = datetime(2021, 1, 1, tzinfo=UTC)
    price = 100.0
    rows = []
    for index in range(count):
        cycle = index % 420
        drift = 0.0035 if cycle < 285 else -0.0018
        next_price = price * (1.0 + drift)
        rows.append(
            {
                "t": (start + timedelta(days=index)).isoformat().replace(
                    "+00:00", "Z"
                ),
                "o": price,
                "h": max(price, next_price) * 1.001,
                "l": min(price, next_price) * 0.999,
                "c": next_price,
                "v": 10.0,
            }
        )
        price = next_price
    return {"BTC/USD": rows}


def test_r2g_manifest_is_adaptive_research_only():
    manifest = campaign_manifest()
    assert manifest["momentum_lookback_days"] == MOMENTUM_LOOKBACK_DAYS
    assert manifest["sma_window_days"] == SMA_WINDOW_DAYS
    assert manifest["evidence_role"] == "ADAPTIVE_DISCOVERY_ONLY"
    assert manifest["independent_historical_validation"] is False
    assert manifest["fresh_confirmation_required"] == "FORWARD_SHADOW_COMPARISON"
    assert manifest["promotion_eligible"] is False
    assert manifest["execution_authority"] is False
    assert manifest["broker_orders_possible"] is False
    assert manifest["live_execution_authorized"] is False


def test_r2g_cost_scenarios_include_severe_stress():
    assert COST_SCENARIOS["maker_15bp"] < COST_SCENARIOS["taker_25bp"]
    assert COST_SCENARIOS["taker_25bp"] < COST_SCENARIOS["taker_stress_30bp"]
    assert COST_SCENARIOS["taker_stress_30bp"] < COST_SCENARIOS["severe_stress_50bp"]


def test_r2g_trending_synthetic_history_survives_only_to_comparison_shadow():
    result = evaluate_btc_consensus_trend_discovery(_bars())
    decisive = result["adaptive_recent_window"]["scenarios"]["taker_stress_30bp"]
    severe = result["adaptive_recent_window"]["scenarios"]["severe_stress_50bp"]
    robustness = result["robustness_neighborhood"]

    assert decisive["bar_count"] >= 600
    assert decisive["entry_count"] >= 2
    assert decisive["total_return"] > 0
    assert decisive["sharpe"] >= 0.5
    assert severe["total_return"] > 0
    assert robustness["positive_return_share"] >= 0.75
    assert result["adaptive_gate"]["survives_to_forward_shadow_comparison"] is True
    assert result["shadow_only"] is True
    assert result["promotion_eligible"] is False
    assert result["live_execution_authorized"] is False


def test_r2g_insufficient_history_is_rejected():
    try:
        evaluate_btc_consensus_trend_discovery(_bars(count=500))
    except ValueError as exc:
        assert str(exc).startswith("v14_r2g_daily_corpus_too_small:")
    else:
        raise AssertionError("expected insufficient corpus rejection")
