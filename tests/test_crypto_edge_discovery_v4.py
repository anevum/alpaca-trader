from datetime import datetime, timedelta, timezone
from decimal import Decimal

from app.config import Settings
from app.research_agent.crypto_edge_discovery_v4 import (
    HIGH_SLIPPAGE_BPS,
    HIGH_SPREAD_BPS,
    METHODOLOGY_VERSION,
    V3_EVALUATION_START,
    candidate_specs_v4,
    run_crypto_edge_discovery_v4,
)


def settings() -> Settings:
    return Settings(
        ALLOWED_SYMBOLS="BTC/USD,ETH/USD",
        SCAN_SYMBOLS="ETH/USD",
        CONFIRMATION_SYMBOLS="BTC/USD",
        STRATEGY_NAME="rolling_momentum_vwap",
        MAX_DAILY_ORDERS=100,
        MAX_CONCURRENT_POSITIONS=1,
        MAX_NEW_ENTRIES_PER_CYCLE=1,
        MAX_TOTAL_POSITION_NOTIONAL="100",
        MAX_SPREAD_PCT="0.002",
    )


def _bars(start: datetime, *, hours: int, base: Decimal, phase: int = 0) -> list[dict]:
    rows = []
    price = base
    for index in range(hours):
        wave = Decimal(((index + phase) % 18) - 7) / Decimal("10000")
        drift = Decimal("0.00024")
        previous = price
        price = max(Decimal("0.01"), previous * (Decimal("1") + drift + wave))
        rows.append({
            "t": (start + timedelta(hours=index)).isoformat().replace("+00:00", "Z"),
            "o": str(previous),
            "h": str(max(previous, price) * Decimal("1.0025")),
            "l": str(min(previous, price) * Decimal("0.9975")),
            "c": str(price),
            "v": str(100 + index % 25),
            "n": 20 + index % 9,
        })
    return rows


def test_v4_candidate_set_preserves_eth_controls_and_adds_12h_variants():
    specs = candidate_specs_v4()

    assert len(specs) == 6
    assert [spec.candidate_id for spec in specs[:4]] == [
        "V3_VNT_6H_ETH",
        "V3_VNT_8H_ETH",
        "V3_RS_6H_ETH",
        "V3_RS_8H_ETH",
    ]
    assert {spec.family for spec in specs} == {
        "volatility_normalized_trend",
        "relative_strength_impulse",
    }
    assert max(spec.max_hold_minutes for spec in specs) == 720


def test_v4_cost_model_applies_only_to_eth_execution():
    assert HIGH_SPREAD_BPS == {"ETH/USD": Decimal("5")}
    assert HIGH_SLIPPAGE_BPS == {"ETH/USD": Decimal("5")}


def test_v4_nonoverlap_and_validation_contract():
    cfg = settings()
    start = V3_EVALUATION_START - timedelta(days=6)
    bars = {
        "BTC/USD": _bars(start, hours=24 * 6, base=Decimal("60000"), phase=3),
        "ETH/USD": _bars(start, hours=24 * 6, base=Decimal("3000"), phase=0),
    }

    result = run_crypto_edge_discovery_v4(
        settings=cfg,
        bars_by_symbol=bars,
        initial_equity=Decimal("100"),
        min_train_days=1,
        validation_days=1,
        holdout_days=2,
        minimum_validation_trades=2,
        minimum_holdout_trades=2,
    )

    assert result["methodology_version"] == METHODOLOGY_VERSION
    assert result["promotion_authority"] is False
    assert result["live_configuration_changed"] is False
    assert result["corpus"]["nonoverlap_with_v3_evaluation"] is True
    assert result["universe_policy"]["execution_symbols"] == ["ETH/USD"]
    assert result["universe_policy"]["context_symbols"] == ["BTC/USD"]
    assert result["contract"]["candidate_set_frozen_before_validation"] is True
    assert result["contract"]["holdout_opened_only_after_multiplicity_adjusted_selection"] is True
    assert result["multiplicity"]["method"] == "by"
    assert result["dependence_adjusted"] is True
    assert result["multiplicity_adjusted"] is True
    assert result["no_lookahead_verified"] is True
    assert len(result["candidates"]) == 6


def test_v4_fails_closed_on_inclusive_provider_boundary():
    cfg = settings()
    start = V3_EVALUATION_START - timedelta(days=6)
    bars = {
        "BTC/USD": _bars(start, hours=24 * 6 + 1, base=Decimal("60000"), phase=3),
        "ETH/USD": _bars(start, hours=24 * 6 + 1, base=Decimal("3000"), phase=0),
    }

    result = run_crypto_edge_discovery_v4(
        settings=cfg,
        bars_by_symbol=bars,
        initial_equity=Decimal("100"),
        min_train_days=1,
        validation_days=1,
        holdout_days=2,
        minimum_validation_trades=2,
        minimum_holdout_trades=2,
    )

    assert result["status"] == "CORPUS_OVERLAP_INVALID"
    assert result["promotion_authority"] is False
