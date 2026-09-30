from datetime import datetime, timedelta, timezone
from math import cos, sin

import pytest

from graen.crypto.research_v7 import (
    BETA_LOOKBACK_HOURS,
    HOLD_MINUTES,
    LEADER_IMPULSE_Z_THRESHOLD,
    METHODOLOGY_VERSION,
    STRATEGY_VERSION_ID,
    _candidate_passed,
    build_series,
    candidate_specs,
    opportunity_state,
    run_crypto_research_v7,
    simulate_candidate,
)


SYMBOLS = ("BTC/USD", "ETH/USD", "SOL/USD")


def _panel(underreact=True):
    start = datetime(2025, 11, 20, tzinfo=timezone.utc)
    event_index = 2050
    rows = {symbol: [] for symbol in SYMBOLS}
    prices = {"BTC/USD": 60000.0, "ETH/USD": 3000.0, "SOL/USD": 150.0}
    for index in range(2130):
        stamp = start + timedelta(minutes=5 * index)
        btc = 0.00005 + 0.00045 * sin(index / 7.0)
        eth = 0.60 * btc + 0.00018 * sin(index / 5.0)
        sol = 0.40 * btc + 0.00022 * cos(index / 9.0)
        if index == event_index:
            btc = 0.020
            eth = 0.001 if underreact else 0.012
        elif event_index < index <= event_index + 7:
            eth = 0.0025
        changes = {"BTC/USD": btc, "ETH/USD": eth, "SOL/USD": sol}
        for symbol in SYMBOLS:
            previous = prices[symbol]
            current = previous * (1.0 + changes[symbol])
            rows[symbol].append({
                "t": stamp.isoformat().replace("+00:00", "Z"),
                "o": previous,
                "h": max(previous, current) * 1.0001,
                "l": min(previous, current) * 0.9999,
                "c": current,
                "v": 1000 + index,
                "n": 30 + index % 5,
                "vw": (previous + current) / 2.0,
            })
            prices[symbol] = current
    event_end = start + timedelta(minutes=5 * (event_index + 1))
    return rows, event_end


def test_contract_is_frozen():
    specs = candidate_specs()
    assert METHODOLOGY_VERSION == "graen-crypto-native-v7-leadlag-v1"
    assert STRATEGY_VERSION_ID == "CRYPTO-LEADLAG-001"
    assert BETA_LOOKBACK_HOURS == 168
    assert HOLD_MINUTES == 30
    assert LEADER_IMPULSE_Z_THRESHOLD == 2.0
    assert specs[0]["confirmatory"] is False
    assert specs[1]["confirmatory"] is True


def test_underreaction_creates_opportunity_without_future_lookahead():
    raw, event_end = _panel(True)
    series = build_series(raw, start=event_end - timedelta(hours=1), end=event_end + timedelta(hours=1))
    before = opportunity_state(series, leader="BTC/USD", follower="ETH/USD", end=event_end)
    assert before is not None
    assert before.direction == 1
    assert before.residual < -before.residual_sigma
    future_end = event_end + timedelta(minutes=5)
    series["ETH/USD"][future_end] = {**series["ETH/USD"][future_end], "c": float(series["ETH/USD"][future_end]["c"]) * 4.0}
    after = opportunity_state(series, leader="BTC/USD", follower="ETH/USD", end=event_end)
    assert after == before


def test_leader_impulse_alone_is_not_enough():
    raw, event_end = _panel(False)
    series = build_series(raw, start=event_end - timedelta(hours=1), end=event_end + timedelta(hours=1))
    assert opportunity_state(series, leader="BTC/USD", follower="ETH/USD", end=event_end) is None


def test_research_simulation_uses_thirty_minute_horizon():
    raw, event_end = _panel(True)
    series = build_series(raw, start=event_end - timedelta(hours=1), end=event_end + timedelta(hours=1))
    opportunity = opportunity_state(series, leader="BTC/USD", follower="ETH/USD", end=event_end)
    assert opportunity is not None
    rows = simulate_candidate(series, [opportunity], start=event_end, end=event_end + timedelta(hours=1), scenario="high")
    assert len(rows) == 1
    assert rows[0].entry_at == event_end
    assert rows[0].exit_at == event_end + timedelta(minutes=30)


def test_validation_gate_does_not_relax_thresholds():
    development = {"primary": {"trade_count": 20, "expectancy_per_trade": 0.001}}
    validation = {
        "primary": {
            "trade_count": 30,
            "independent_day_blocks": 20,
            "expectancy_per_trade": 0.001,
            "dependence_adjusted_null": {"p_value": 0.05},
            "profit_factor": 1.01,
            "symbol_concentration": {"max_share": 0.70},
        },
        "one_bar_delay_robustness": {"expectancy_per_trade": 0.0001},
    }
    assert _candidate_passed(development, validation)
    validation["primary"]["expectancy_per_trade"] = 0.0
    assert not _candidate_passed(development, validation)


def test_corpus_must_be_verified_and_uninspected():
    start = datetime(2025, 12, 1, tzinfo=timezone.utc)
    with pytest.raises(ValueError, match="provenance"):
        run_crypto_research_v7(
            bars_by_symbol={},
            development_start=start,
            validation_start=start + timedelta(days=20),
            holdout_start=start + timedelta(days=41),
            holdout_end=start + timedelta(days=61),
        )
    with pytest.raises(ValueError, match="overlaps"):
        run_crypto_research_v7(
            bars_by_symbol={},
            development_start=start,
            validation_start=start + timedelta(days=20),
            holdout_start=start + timedelta(days=41),
            holdout_end=start + timedelta(days=61),
            corpus_provenance_verified=True,
            previously_inspected_ranges=[{
                "id": "prior",
                "start": (start - timedelta(days=1)).isoformat(),
                "end": (start + timedelta(days=1)).isoformat(),
            }],
        )
