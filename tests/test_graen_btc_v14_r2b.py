from __future__ import annotations

from datetime import datetime, timedelta, timezone

from graen.crypto.btc_passive_scalping_v14_r2b import (
    CAMPAIGN_ID,
    METHODOLOGY_VERSION,
    campaign_manifest,
    evaluate_passive_scalping_preflight,
)

UTC = timezone.utc


def _flat_bars(count: int = 4000):
    start = datetime(2026, 9, 1, tzinfo=UTC)
    rows = []
    for i in range(count):
        stamp = start + timedelta(minutes=i)
        rows.append({
            "t": stamp.isoformat().replace("+00:00", "Z"),
            "o": 100.0,
            "h": 100.2,
            "l": 99.8,
            "c": 100.0,
        })
    return rows


def test_v14_r2b_manifest_is_research_only_and_source_anchored():
    manifest = campaign_manifest()
    assert manifest["campaign_id"] == CAMPAIGN_ID
    assert manifest["methodology_version"] == METHODOLOGY_VERSION
    assert manifest["source_reference"]["repository"] == "alpacahq/plug-and-play-strategies"
    assert manifest["source_reference"]["path"] == "Scalping/scalping.py"
    assert manifest["round_trip_costs"]["taker_stress_50bp"] == 0.005
    assert manifest["execution_authority"] is False
    assert manifest["broker_orders_possible"] is False
    assert manifest["live_execution_authorized"] is False


def test_v14_r2b_flat_market_does_not_survive_cost_hurdle():
    result = evaluate_passive_scalping_preflight(_flat_bars())
    assert result["broker_feasibility_gate"]["survives_to_shadow"] is False
    assert result["execution_authority"] is False
    assert result["broker_orders_possible"] is False
    assert result["live_execution_authorized"] is False
