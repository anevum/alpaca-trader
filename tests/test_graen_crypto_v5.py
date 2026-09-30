from datetime import datetime, timedelta, timezone

from graen.crypto.research_v5 import (
    COSTS_BPS,
    FEATURE_NAMES,
    CandidateSpec,
    _round_trip_cost_bps,
    candidate_specs,
    feature_snapshot,
    holdout_evaluation,
)


def _bars(base: float):
    start = datetime(2026, 1, 31, 18, tzinfo=timezone.utc)
    rows = []
    price = base
    for index in range(80):
        stamp = start + timedelta(minutes=5 * index)
        new_price = price * (1.0 + 0.0002 + (index % 3) * 0.00001)
        rows.append(
            {
                "t": stamp.isoformat().replace("+00:00", "Z"),
                "o": price,
                "h": max(price, new_price) * 1.0001,
                "l": min(price, new_price) * 0.9999,
                "c": new_price,
                "v": 10 + index,
                "n": 5 + index % 4,
                "vw": (price + new_price) / 2,
            }
        )
        price = new_price
    return rows


def test_candidate_family_is_frozen_and_complete():
    specs = candidate_specs()
    assert len(specs) == 17
    assert {spec.candidate_id for spec in specs} == {
        "MOM_CONT",
        "RS_CONT",
        "RS_DELAY15",
        "RS_PULLBACK",
        "MR_BTC_NONBEAR",
        "VNT_CONT",
    }
    assert len(FEATURE_NAMES) == 9


def test_cost_hurdle_uses_full_spread_plus_two_sided_slippage():
    expected = COSTS_BPS["BTC/USD"]["spread"]["high"] + 2 * COSTS_BPS["BTC/USD"]["slippage"]["high"]
    assert _round_trip_cost_bps("BTC/USD", "high") == expected
    assert _round_trip_cost_bps("AVAX/USD", "high") > _round_trip_cost_bps("BTC/USD", "high")


def test_feature_snapshot_uses_only_completed_history_at_timestamp():
    symbols = ("BTC/USD", "ETH/USD", "SOL/USD", "XRP/USD", "AVAX/USD", "LINK/USD")
    raw = {symbol: _bars(100 + 10 * index) for index, symbol in enumerate(symbols)}
    from graen.crypto.research_v5 import build_series

    series = build_series(raw)
    decision = datetime(2026, 2, 1, 0, 0, tzinfo=timezone.utc)
    before = feature_snapshot(series, decision)

    future_end = decision + timedelta(minutes=5)
    series["ETH/USD"][future_end] = {
        **series["ETH/USD"][future_end],
        "c": 999999.0,
    }
    after = feature_snapshot(series, decision)
    assert before == after


def test_holdout_remains_sealed_without_validation_winner():
    state = holdout_evaluation({}, None)
    assert state["opened"] is False
    assert "no validation candidate" in state["reason"]
