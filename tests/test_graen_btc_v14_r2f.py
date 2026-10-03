from __future__ import annotations

from datetime import datetime, timedelta, timezone

from graen.crypto.btc_slow_momentum_v14_r2f import (
    COST_SCENARIOS,
    LOOKBACK_DAYS,
    campaign_manifest,
    evaluate_btc_slow_momentum_discovery,
    momentum_long_at,
)

UTC = timezone.utc


def _bars(*, count: int = 2100, start: datetime | None = None):
    current = start or datetime(2021, 1, 1, tzinfo=UTC)
    price = 100.0
    rows = []
    for i in range(count):
        # Multi-year trend with long positive regimes and bounded drawdowns.
        cycle = i % 520
        drift = 0.0020 if cycle < 390 else -0.0012
        next_price = price * (1.0 + drift)
        rows.append(
            {
                "t": (current + timedelta(days=i)).isoformat().replace("+00:00", "Z"),
                "o": price,
                "h": max(price, next_price) * 1.001,
                "l": min(price, next_price) * 0.999,
                "c": next_price,
                "v": 10.0,
            }
        )
        price = next_price
    return {"BTC/USD": rows}


def test_v14_r2f_manifest_discloses_adaptive_evidence_and_blocks_live_promotion():
    manifest = campaign_manifest()
    assert manifest["lookback_days"] == 180
    assert manifest["evidence_role"] == "ADAPTIVE_DISCOVERY_ONLY"
    assert manifest["independent_historical_validation"] is False
    assert manifest["independent_historical_holdout"] is False
    assert manifest["fresh_confirmation_required"] == "FORWARD_SHADOW"
    assert manifest["promotion_eligible"] is False
    assert manifest["execution_authority"] is False
    assert manifest["broker_orders_possible"] is False
    assert manifest["live_execution_authorized"] is False


def test_v14_r2f_cost_scenarios_are_monotonic():
    assert COST_SCENARIOS["maker_15bp"] < COST_SCENARIOS["taker_25bp"]
    assert COST_SCENARIOS["taker_25bp"] < COST_SCENARIOS["taker_stress_30bp"]


def test_v14_r2f_signal_is_causal_and_uses_prior_completed_day_only():
    data = _bars(count=LOOKBACK_DAYS + 4)["BTC/USD"]
    normalized = [
        {
            "timestamp": datetime.fromisoformat(row["t"].replace("Z", "+00:00")),
            "close": float(row["c"]),
        }
        for row in data
    ]
    signal_before = momentum_long_at(normalized, LOOKBACK_DAYS + 1)
    normalized[LOOKBACK_DAYS + 1]["close"] *= 0.01
    signal_after = momentum_long_at(normalized, LOOKBACK_DAYS + 1)
    assert signal_before == signal_after


def test_v14_r2f_trending_synthetic_history_survives_only_to_forward_shadow():
    result = evaluate_btc_slow_momentum_discovery(_bars())
    decisive = result["adaptive_recent_window"]["scenarios"]["taker_stress_30bp"]
    assert decisive["bar_count"] >= 600
    assert decisive["entry_count"] >= 2
    assert decisive["total_return"] > 0
    assert decisive["sharpe"] >= 0.5
    assert result["broker_feasibility_screen"]["survives_to_forward_shadow"] is True
    assert result["shadow_only"] is True
    assert result["promotion_eligible"] is False
    assert result["independent_historical_validation"] is False
    assert result["live_execution_authorized"] is False


def test_v14_r2f_insufficient_history_is_rejected():
    try:
        evaluate_btc_slow_momentum_discovery(_bars(count=400))
    except ValueError as exc:
        assert str(exc).startswith("v14_r2f_daily_corpus_too_small:")
    else:
        raise AssertionError("expected insufficient corpus rejection")
