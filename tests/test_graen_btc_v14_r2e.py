from __future__ import annotations

from datetime import datetime, timedelta, timezone

from graen.crypto.btc_4h_trend_v14_r2e import (
    COST_SCENARIOS,
    SMA_WINDOWS,
    campaign_manifest,
    evaluate_btc_4h_trend_preflight,
)

UTC = timezone.utc


def _bars(*, start: datetime, count: int):
    rows = []
    price = 100.0
    for i in range(count):
        stamp = start + timedelta(hours=4 * i)
        # Repeated long bull phases and shorter bear phases create multiple
        # causal SMA entries/exits while keeping the full synthetic path positive.
        cycle = i % 600
        drift = 0.0010 if cycle < 420 else -0.0008
        next_price = price * (1.0 + drift)
        rows.append(
            {
                "t": stamp.isoformat().replace("+00:00", "Z"),
                "o": price,
                "h": max(price, next_price) * 1.001,
                "l": min(price, next_price) * 0.999,
                "c": next_price,
                "v": 10.0,
            }
        )
        price = next_price
    return {"BTC/USD": rows}


def test_v14_r2e_manifest_is_low_turnover_cost_aware_and_research_only():
    manifest = campaign_manifest()
    assert manifest["bar_timeframe"] == "4Hour"
    assert manifest["sma_windows"] == list(SMA_WINDOWS)
    assert manifest["cost_scenarios"]["maker_15bp"] == 0.0015
    assert manifest["cost_scenarios"]["taker_25bp"] == 0.0025
    assert manifest["cost_scenarios"]["taker_stress_30bp"] == 0.0030
    assert manifest["execution_authority"] is False
    assert manifest["broker_orders_possible"] is False
    assert manifest["crypto_execution_enabled"] is False
    assert manifest["live_execution_authorized"] is False


def test_v14_r2e_cost_scenarios_are_monotonic():
    assert COST_SCENARIOS["maker_15bp"] < COST_SCENARIOS["taker_25bp"]
    assert COST_SCENARIOS["taker_25bp"] < COST_SCENARIOS["taker_stress_30bp"]


def test_v14_r2e_trending_synthetic_corpus_survives_only_to_shadow():
    result = evaluate_btc_4h_trend_preflight(
        _bars(start=datetime(2021, 1, 1, tzinfo=UTC), count=12600)
    )
    decisive = result["oos"]["scenarios"]["taker_stress_30bp"]
    assert result["development"]["selected_window"] in SMA_WINDOWS
    assert decisive["bar_count"] >= 1800
    assert decisive["total_return"] > 0
    assert decisive["sharpe"] >= 0.5
    assert result["broker_feasibility_gate"]["survives_to_shadow"] is True
    assert result["shadow_only"] is True
    assert result["execution_authority"] is False
    assert result["broker_orders_possible"] is False
    assert result["live_execution_authorized"] is False


def test_v14_r2e_missing_oos_is_rejected():
    data = _bars(start=datetime(2021, 1, 1, tzinfo=UTC), count=2000)
    try:
        evaluate_btc_4h_trend_preflight(data)
    except ValueError as exc:
        assert str(exc) == "v14_r2e_oos_corpus_missing"
    else:
        raise AssertionError("expected missing OOS corpus failure")
