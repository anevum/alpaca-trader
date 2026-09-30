from datetime import datetime, timedelta, timezone
from decimal import Decimal

from app.config import Settings
from app.research_agent.crypto_edge_discovery_v2 import (
    BASE_SPREAD_BPS,
    HIGH_SLIPPAGE_BPS,
    HIGH_SPREAD_BPS,
    METHODOLOGY_VERSION,
    candidate_specs_v2,
    run_crypto_edge_discovery_v2,
)
from app.velum_core import _cost_bps, _cost_payload


def settings() -> Settings:
    return Settings(
        ALLOWED_SYMBOLS="BTC/USD,ETH/USD,SOL/USD",
        SCAN_SYMBOLS="BTC/USD,ETH/USD,SOL/USD",
        CONFIRMATION_SYMBOLS="BTC/USD,ETH/USD",
        STRATEGY_NAME="rolling_momentum_vwap",
        MAX_DAILY_ORDERS=100,
        MAX_CONCURRENT_POSITIONS=5,
        MAX_NEW_ENTRIES_PER_CYCLE=2,
        MAX_TOTAL_POSITION_NOTIONAL="100",
        MAX_SPREAD_PCT="0.002",
    )


def _bars(start: datetime, *, hours: int, base: Decimal) -> list[dict]:
    rows = []
    price = base
    for index in range(hours):
        wave = Decimal((index % 10) - 3) / Decimal("10000")
        drift = Decimal("0.00025")
        previous = price
        price = max(Decimal("0.01"), previous * (Decimal("1") + drift + wave))
        rows.append({
            "t": (start + timedelta(hours=index)).isoformat().replace("+00:00", "Z"),
            "o": str(previous),
            "h": str(max(previous, price) * Decimal("1.002")),
            "l": str(min(previous, price) * Decimal("0.998")),
            "c": str(price),
            "v": str(100 + index % 20),
            "n": 20 + index % 7,
        })
    return rows


def test_symbol_specific_cost_helpers_are_deterministic():
    assert _cost_bps(HIGH_SPREAD_BPS, "BTC/USD") == Decimal("5")
    assert _cost_bps(HIGH_SPREAD_BPS, "SOL/USD") == Decimal("20")
    assert _cost_bps(Decimal("7"), "ETH/USD") == Decimal("7")
    assert _cost_payload(BASE_SPREAD_BPS) == {
        "BTC/USD": "4",
        "ETH/USD": "4",
        "SOL/USD": "13",
    }


def test_v2_candidate_set_is_frozen_to_major_cost_horizon_question():
    specs = candidate_specs_v2()
    assert len(specs) == 7
    assert specs[0].candidate_id == "MAJOR_BENCHMARK_60"
    assert {spec.family for spec in specs} == {
        "rolling_momentum_vwap",
        "volatility_normalized_trend",
        "compression_breakout",
        "relative_strength_impulse",
    }
    assert {spec.max_hold_minutes for spec in specs} == {60, 120, 240}
    assert max(spec.target_pct for spec in specs) == Decimal("0.0120")


def test_v2_archived_walk_forward_contract_and_cost_model():
    cfg = settings()
    start = datetime(2026, 8, 25, 1, 53, tzinfo=timezone.utc)
    hours = 24 * 6
    bars = {
        "BTC/USD": _bars(start, hours=hours, base=Decimal("60000")),
        "ETH/USD": _bars(start, hours=hours, base=Decimal("3000")),
        "SOL/USD": _bars(start, hours=hours, base=Decimal("150")),
    }
    result = run_crypto_edge_discovery_v2(
        settings=cfg,
        bars_by_symbol=bars,
        initial_equity=Decimal("100"),
        min_train_days=1,
        validation_days=1,
        holdout_days=1,
        minimum_validation_trades=2,
    )

    assert result["methodology_version"] == METHODOLOGY_VERSION
    assert result["promotion_authority"] is False
    assert result["live_configuration_changed"] is False
    assert result["corpus"]["nonoverlap_with_v1_evaluation"] is True
    assert result["contract"]["v2_evaluation_corpus_must_not_overlap_v1"] is True
    assert result["multiplicity"]["method"] == "by"
    assert result["dependence_adjusted"] is True
    assert result["multiplicity_adjusted"] is True
    assert result["no_lookahead_verified"] is True
    assert result["cost_model"]["high_spread_bps"] == {
        "BTC/USD": "5",
        "ETH/USD": "5",
        "SOL/USD": "20",
    }
    assert result["cost_model"]["high_slippage_bps_per_side"] == {
        "BTC/USD": "5",
        "ETH/USD": "5",
        "SOL/USD": "7.5",
    }
    assert len(result["candidates"]) == 7


def test_v2_cost_maps_cover_frozen_major_universe():
    assert set(HIGH_SPREAD_BPS) == {"BTC/USD", "ETH/USD", "SOL/USD"}
    assert set(HIGH_SLIPPAGE_BPS) == set(HIGH_SPREAD_BPS)
