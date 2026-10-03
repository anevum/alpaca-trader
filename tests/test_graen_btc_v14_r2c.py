from __future__ import annotations

from datetime import datetime, timedelta, timezone

from graen.crypto.cross_sectional_momentum_v14_r2c import (
    CAMPAIGN_ID,
    METHODOLOGY_VERSION,
    SWITCH_COSTS,
    UNIVERSE,
    campaign_manifest,
    evaluate_cross_sectional_momentum_preflight,
)

UTC = timezone.utc


def _bars(*, days: int = 420, winner: str = "SOL/USD"):
    start = datetime(2025, 1, 1, tzinfo=UTC)
    out = {symbol: [] for symbol in UNIVERSE}
    for i in range(days):
        stamp = start + timedelta(days=i)
        for symbol in UNIVERSE:
            drift = 1.003 if symbol == winner else 1.0002
            px = 100.0 * (drift ** i)
            out[symbol].append({
                "t": stamp.isoformat().replace("+00:00", "Z"),
                "o": px,
                "h": px * 1.01,
                "l": px * 0.99,
                "c": px * drift,
            })
    return out


def test_v14_r2c_manifest_is_source_anchored_and_research_only():
    manifest = campaign_manifest()
    assert manifest["campaign_id"] == CAMPAIGN_ID
    assert manifest["methodology_version"] == METHODOLOGY_VERSION
    assert manifest["source_reference"]["repository"] == "alpacahq/plug-and-play-strategies"
    assert manifest["source_reference"]["path"] == "Websocket_Momentum_Trading/websocket_momentum.ipynb"
    assert manifest["momentum_lookback_days"] == 7
    assert manifest["switch_costs"]["taker_switch_50bp"] == 0.005
    assert manifest["execution_authority"] is False
    assert manifest["broker_orders_possible"] is False
    assert manifest["live_execution_authorized"] is False


def test_v14_r2c_long_history_universe_excludes_short_history_pairs():
    assert "POL/USD" not in UNIVERSE
    assert "ADA/USD" not in UNIVERSE
    assert set(UNIVERSE) == {
        "BTC/USD",
        "ETH/USD",
        "DOGE/USD",
        "SHIB/USD",
        "AVAX/USD",
        "LINK/USD",
        "SOL/USD",
    }


def test_v14_r2c_persistent_winner_can_survive_to_shadow_only():
    result = evaluate_cross_sectional_momentum_preflight(_bars())
    decisive = result["oos"]["scenarios"]["taker_switch_50bp"]
    assert decisive["day_count"] >= 120
    assert decisive["total_return"] > 0.0
    assert result["broker_feasibility_gate"]["survives_to_shadow"] is True
    assert result["shadow_only"] is True
    assert result["execution_authority"] is False
    assert result["broker_orders_possible"] is False
    assert result["live_execution_authorized"] is False


def test_v14_r2c_switch_costs_are_monotonic():
    assert SWITCH_COSTS["maker_switch_30bp"] < SWITCH_COSTS["mixed_switch_40bp"]
    assert SWITCH_COSTS["mixed_switch_40bp"] < SWITCH_COSTS["taker_switch_50bp"]
