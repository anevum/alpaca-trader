from datetime import datetime, timedelta, timezone
from decimal import Decimal

from app.config import Settings
from app.research_agent.crypto_edge_discovery_v3 import (
    HIGH_SPREAD_BPS,
    HIGH_SLIPPAGE_BPS,
    METHODOLOGY_VERSION,
    V2_EVALUATION_START,
    candidate_specs_v3,
    run_crypto_edge_discovery_v3,
)


def settings() -> Settings:
    return Settings(
        ALLOWED_SYMBOLS="BTC/USD,ETH/USD",
        SCAN_SYMBOLS="BTC/USD,ETH/USD",
        CONFIRMATION_SYMBOLS="BTC/USD,ETH/USD",
        STRATEGY_NAME="rolling_momentum_vwap",
        MAX_DAILY_ORDERS=100,
        MAX_CONCURRENT_POSITIONS=2,
        MAX_NEW_ENTRIES_PER_CYCLE=1,
        MAX_TOTAL_POSITION_NOTIONAL="100",
        MAX_SPREAD_PCT="0.002",
    )


def _bars(start: datetime, *, hours: int, base: Decimal) -> list[dict]:
    rows = []
    price = base
    for index in range(hours):
        wave = Decimal((index % 16) - 6) / Decimal("10000")
        drift = Decimal("0.00022")
        previous = price
        price = max(Decimal("0.01"), previous * (Decimal("1") + drift + wave))
        rows.append({
            "t": (start + timedelta(hours=index)).isoformat().replace("+00:00", "Z"),
            "o": str(previous),
            "h": str(max(previous, price) * Decimal("1.0025")),
            "l": str(min(previous, price) * Decimal("0.9975")),
            "c": str(price),
            "v": str(100 + index % 20),
            "n": 20 + index % 7,
        })
    return rows


def test_v3_candidate_set_preserves_v2_best_and_tests_slower_major_trends():
    specs = candidate_specs_v3()
    assert len(specs) == 6
    assert specs[0].candidate_id == "V2_BEST_VNT_LONG"
    assert specs[0].max_hold_minutes == 240
    assert {spec.family for spec in specs} == {
        "volatility_normalized_trend",
        "relative_strength_impulse",
        "market_confirmed_volatility_trend",
    }
    assert max(spec.max_hold_minutes for spec in specs) == 480


def test_v3_cost_model_is_frozen_to_low_friction_majors():
    assert HIGH_SPREAD_BPS == {
        "BTC/USD": Decimal("5"),
        "ETH/USD": Decimal("5"),
    }
    assert HIGH_SLIPPAGE_BPS == {
        "BTC/USD": Decimal("5"),
        "ETH/USD": Decimal("5"),
    }


def test_v3_uses_nonoverlapping_archive_and_frozen_validation_contract():
    cfg = settings()
    start = V2_EVALUATION_START - timedelta(days=6)
    bars = {
        "BTC/USD": _bars(start, hours=24 * 6, base=Decimal("60000")),
        "ETH/USD": _bars(start, hours=24 * 6, base=Decimal("3000")),
    }
    result = run_crypto_edge_discovery_v3(
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
    assert result["corpus"]["nonoverlap_with_v2_evaluation"] is True
    assert result["contract"]["candidate_set_frozen_before_validation"] is True
    assert result["multiplicity"]["method"] == "by"
    assert result["dependence_adjusted"] is True
    assert result["multiplicity_adjusted"] is True
    assert result["no_lookahead_verified"] is True
    assert len(result["candidates"]) == 6


def test_v3_fails_closed_on_inclusive_provider_boundary():
    cfg = settings()
    start = V2_EVALUATION_START - timedelta(days=6)
    bars = {
        "BTC/USD": _bars(start, hours=24 * 6 + 1, base=Decimal("60000")),
        "ETH/USD": _bars(start, hours=24 * 6 + 1, base=Decimal("3000")),
    }
    result = run_crypto_edge_discovery_v3(
        settings=cfg,
        bars_by_symbol=bars,
        initial_equity=Decimal("100"),
        min_train_days=1,
        validation_days=1,
        holdout_days=1,
        minimum_validation_trades=2,
    )

    assert result["status"] == "CORPUS_OVERLAP_INVALID"
    assert result["promotion_authority"] is False
