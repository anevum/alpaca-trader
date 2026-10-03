from __future__ import annotations

from datetime import datetime, timedelta, timezone

from graen.crypto.triangular_arbitrage_v14_r2d import (
    LEG_FEE_RATES,
    MIN_MATCHED_SNAPSHOTS,
    PAIRS,
    campaign_manifest,
    evaluate_triangular_arbitrage_preflight,
)

UTC = timezone.utc


def _quotes(*, profitable: bool, count: int = 900):
    start = datetime(2026, 10, 3, 21, 0, tzinfo=UTC)
    out = {symbol: [] for symbol in PAIRS}
    for i in range(count):
        stamp = start + timedelta(milliseconds=250 * i)
        iso = stamp.isoformat().replace("+00:00", "Z")
        btc_bid, btc_ask = 99.90, 100.00
        ethbtc_bid, ethbtc_ask = 0.0499, 0.0500
        if profitable:
            ethusd_bid, ethusd_ask = 5.10, 5.11
        else:
            ethusd_bid, ethusd_ask = 4.98, 5.02
        out["BTC/USD"].append(
            {"t": iso, "bp": btc_bid, "ap": btc_ask, "bs": 100.0, "as": 100.0}
        )
        out["ETH/BTC"].append(
            {"t": iso, "bp": ethbtc_bid, "ap": ethbtc_ask, "bs": 1000.0, "as": 1000.0}
        )
        out["ETH/USD"].append(
            {"t": iso, "bp": ethusd_bid, "ap": ethusd_ask, "bs": 1000.0, "as": 1000.0}
        )
    return out


def test_v14_r2d_manifest_is_research_only_and_cost_aware():
    manifest = campaign_manifest()
    assert manifest["pairs"] == list(PAIRS)
    assert manifest["leg_fee_rates"]["maker_45bp"] == 0.0015
    assert manifest["leg_fee_rates"]["taker_75bp"] == 0.0025
    assert manifest["execution_authority"] is False
    assert manifest["broker_orders_possible"] is False
    assert manifest["crypto_execution_enabled"] is False
    assert manifest["live_execution_authorized"] is False


def test_v14_r2d_fee_scenarios_are_monotonic():
    assert LEG_FEE_RATES["maker_45bp"] < LEG_FEE_RATES["mixed_60bp"]
    assert LEG_FEE_RATES["mixed_60bp"] < LEG_FEE_RATES["taker_75bp"]


def test_v14_r2d_profitable_synthetic_triangle_survives_only_to_shadow():
    result = evaluate_triangular_arbitrage_preflight(_quotes(profitable=True))
    decisive = result["scenarios"]["taker_75bp"]
    assert decisive["snapshot_count"] >= MIN_MATCHED_SNAPSHOTS
    assert decisive["profitable_snapshot_count"] >= 5
    assert decisive["max_net_edge_bps"] > 5.0
    assert result["broker_feasibility_gate"]["survives_to_shadow"] is True
    assert result["shadow_only"] is True
    assert result["execution_authority"] is False
    assert result["broker_orders_possible"] is False
    assert result["live_execution_authorized"] is False


def test_v14_r2d_cost_and_spread_eliminate_non_arbitrage_case():
    result = evaluate_triangular_arbitrage_preflight(_quotes(profitable=False))
    decisive = result["scenarios"]["taker_75bp"]
    assert decisive["profitable_snapshot_count"] == 0
    assert result["broker_feasibility_gate"]["survives_to_shadow"] is False
    assert result["shadow_only"] is False
