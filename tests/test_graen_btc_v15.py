from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from graen.crypto.btc_r2h_breakout_v15 import (
    ENTRY_LOOKBACK_BARS,
    EXIT_LOOKBACK_BARS,
    HARD_STOP_PCT,
    HOLDOUT_END,
    HOLDOUT_START,
    MOMENTUM_LOOKBACK_BARS,
    SMA_WINDOW_BARS,
    campaign_manifest,
    candidate_spec,
    evaluate_btc_r2h_breakout,
)

UTC = timezone.utc


def _bars(count: int = 5000):
    start = datetime(2024, 1, 1, tzinfo=UTC)
    price = 100.0
    rows = []
    for index in range(count):
        cycle = index % 360
        drift = 0.0012 if cycle < 230 else -0.0008
        next_price = max(price * (1.0 + drift), 1.0)
        rows.append(
            {
                "t": (start + timedelta(hours=4 * index)).isoformat(),
                "o": price,
                "h": max(price, next_price) * 1.0015,
                "l": min(price, next_price) * 0.9985,
                "c": next_price,
            }
        )
        price = next_price
    return {"BTC/USD": rows}


def test_v15_candidate_is_frozen_and_research_only():
    spec = candidate_spec()
    manifest = campaign_manifest()

    assert spec.candidate_id == "V15-R1-BTC-R2H-BREAKOUT-42-15"
    assert spec.regime_momentum_bars == MOMENTUM_LOOKBACK_BARS
    assert spec.regime_sma_bars == SMA_WINDOW_BARS
    assert spec.entry_lookback_bars == ENTRY_LOOKBACK_BARS == 42
    assert spec.exit_lookback_bars == EXIT_LOOKBACK_BARS == 15
    assert spec.hard_stop_pct == HARD_STOP_PCT == 0.05

    assert manifest["selection_protocol"]["frozen_before_holdout"] is True
    assert manifest["selection_protocol"]["holdout_window"]["start"] == HOLDOUT_START.isoformat()
    assert manifest["selection_protocol"]["holdout_window"]["end"] == HOLDOUT_END.isoformat()
    assert manifest["research_only"] is True
    assert manifest["promotion_eligible"] is False
    assert manifest["execution_authority"] is False
    assert manifest["broker_orders_possible"] is False
    assert manifest["live_execution_authorized"] is False


def test_v15_evaluation_emits_frozen_development_and_holdout_evidence():
    result = evaluate_btc_r2h_breakout(_bars())

    assert result["methodology_version"] == "graen-btc-r2h-breakout-v15-r1"
    assert result["candidate_spec"]["entry_lookback_bars"] == 42
    assert result["candidate_spec"]["exit_lookback_bars"] == 15
    assert len(result["development"]["grid"]) == 9
    assert set(result["holdout"]["scenarios"]) == {
        "taker_25bp",
        "taker_stress_30bp",
        "severe_stress_50bp",
    }
    assert len(result["holdout"]["neighborhood"]["cells"]) == 9
    assert result["research_only"] is True
    assert result["promotion_eligible"] is False
    assert result["execution_authority"] is False
    assert result["broker_orders_possible"] is False
    assert result["live_execution_authorized"] is False


def test_v15_rejects_insufficient_history():
    with pytest.raises(ValueError, match="v15_r1_corpus_too_small"):
        evaluate_btc_r2h_breakout(
            {
                "BTC/USD": [
                    {
                        "t": "2026-01-01T00:00:00+00:00",
                        "o": 100,
                        "h": 101,
                        "l": 99,
                        "c": 100,
                    }
                ]
            }
        )
