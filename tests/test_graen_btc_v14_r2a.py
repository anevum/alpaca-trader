from __future__ import annotations

from datetime import datetime, timedelta, timezone

from graen.crypto.btc_queue_imbalance_v14_r2a import (
    CAMPAIGN_ID,
    METHODOLOGY_VERSION,
    ROUND_TRIP_COSTS,
    bucket_quotes,
    campaign_manifest,
    evaluate_queue_imbalance_preflight,
)

UTC = timezone.utc


def _quotes(*, count: int, rising: bool) -> list[dict]:
    start = datetime(2026, 9, 28, tzinfo=UTC)
    rows = []
    for index in range(count):
        stamp = start + timedelta(seconds=15 * index)
        base = 100.0 * ((1.006 ** index) if rising else 1.0)
        rows.append(
            {
                "t": stamp.isoformat().replace("+00:00", "Z"),
                "bp": base,
                "ap": base * 1.0002,
                "bs": 9.0,
                "as": 1.0,
            }
        )
    return rows


def test_v14_r2a_manifest_is_frozen_and_research_only():
    manifest = campaign_manifest()
    assert manifest["campaign_id"] == CAMPAIGN_ID
    assert manifest["methodology_version"] == METHODOLOGY_VERSION
    assert manifest["bucket_seconds"] == 15
    assert manifest["round_trip_costs"]["taker_base_50bp"] == 0.005
    assert manifest["next_stage_if_pass"] == "LIVE_L2_SHADOW_CONFIRMATION_ONLY"
    assert manifest["execution_authority"] is False
    assert manifest["broker_orders_possible"] is False
    assert manifest["crypto_execution_enabled"] is False
    assert manifest["live_execution_authorized"] is False


def test_v14_r2a_buckets_keep_latest_quote_per_15_second_interval():
    rows = [
        {"t": "2026-09-28T00:00:01Z", "bp": 100, "ap": 101, "bs": 1, "as": 1},
        {"t": "2026-09-28T00:00:12Z", "bp": 102, "ap": 103, "bs": 3, "as": 1},
        {"t": "2026-09-28T00:00:16Z", "bp": 104, "ap": 105, "bs": 1, "as": 3},
    ]
    buckets = bucket_quotes(rows)
    assert len(buckets) == 2
    assert buckets[0]["bid"] == 102.0
    assert buckets[0]["imbalance"] == 0.5
    assert buckets[1]["bid"] == 104.0
    assert buckets[1]["imbalance"] == -0.5


def test_v14_r2a_positive_synthetic_edge_can_only_survive_to_shadow():
    result = evaluate_queue_imbalance_preflight(_quotes(count=2400, rising=True))
    assert result["broker_feasibility_gate"]["survives_to_live_l2_shadow"] is True
    assert result["interpretation"] == "V14_R2A_SURVIVES_TO_LIVE_L2_SHADOW"
    assert result["oos"]["scenarios"]["taker_base_50bp"]["trade_count"] >= 20
    assert result["oos"]["scenarios"]["taker_base_50bp"]["total_return"] > 0
    assert result["shadow_only"] is True
    assert result["promotion_eligible"] is False
    assert result["live_execution_authorized"] is False


def test_v14_r2a_flat_market_fails_after_realistic_costs():
    result = evaluate_queue_imbalance_preflight(_quotes(count=2400, rising=False))
    decisive = result["oos"]["scenarios"]["taker_base_50bp"]
    assert decisive["trade_count"] >= 20
    assert decisive["total_return"] < 0
    assert result["broker_feasibility_gate"]["survives_to_live_l2_shadow"] is False
    assert result["interpretation"] == "V14_R2A_BROKER_FEASIBILITY_FAIL"


def test_v14_r2a_cost_hurdles_are_monotonic():
    assert ROUND_TRIP_COSTS["maker_base_30bp"] < ROUND_TRIP_COSTS["mixed_stress_40bp"]
    assert ROUND_TRIP_COSTS["mixed_stress_40bp"] < ROUND_TRIP_COSTS["taker_base_50bp"]
