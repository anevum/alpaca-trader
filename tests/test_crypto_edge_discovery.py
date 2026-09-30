from datetime import datetime, timedelta, timezone
from decimal import Decimal

from app.config import Settings
from app.crypto_edge_service import CryptoEdgeDiscoveryRuntime, _research_settings
from app.research_agent.crypto_edge_discovery import (
    METHODOLOGY_VERSION,
    candidate_specs,
    run_crypto_edge_discovery,
)


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
    )


def _bars(start: datetime, *, hours: int, base: Decimal) -> list[dict]:
    rows = []
    price = base
    for index in range(hours):
        wave = Decimal((index % 12) - 4) / Decimal("10000")
        drift = Decimal("0.00035")
        previous = price
        price = max(
            Decimal("0.01"),
            previous * (Decimal("1") + drift + wave),
        )
        high = max(previous, price) * Decimal("1.0015")
        low = min(previous, price) * Decimal("0.9985")
        rows.append(
            {
                "t": (start + timedelta(hours=index)).isoformat().replace("+00:00", "Z"),
                "o": str(previous),
                "h": str(high),
                "l": str(low),
                "c": str(price),
                "v": str(100 + (index % 17) * 10),
                "n": 20 + (index % 5),
            }
        )
    return rows


def test_crypto_edge_candidate_set_is_frozen_and_diverse():
    specs = candidate_specs()
    ids = [spec.candidate_id for spec in specs]
    families = {spec.family for spec in specs}

    assert len(ids) == len(set(ids)) == 9
    assert ids[0] == "BENCHMARK_RMVWAP"
    assert {
        "rolling_momentum_vwap",
        "volatility_normalized_trend",
        "impulse_pullback_reclaim",
        "compression_breakout",
        "relative_strength_impulse",
    }.issubset(families)


def test_crypto_edge_discovery_fails_closed_on_short_corpus():
    cfg = settings()
    start = datetime(2026, 9, 1, tzinfo=timezone.utc)
    bars = {
        "BTC/USD": _bars(start, hours=48, base=Decimal("60000")),
        "ETH/USD": _bars(start, hours=48, base=Decimal("3000")),
        "SOL/USD": _bars(start, hours=48, base=Decimal("150")),
    }
    result = run_crypto_edge_discovery(
        settings=cfg,
        bars_by_symbol=bars,
        initial_equity=Decimal("100"),
        spread_bps=Decimal("10"),
        slippage_bps=Decimal("5"),
    )
    assert result["methodology_version"] == METHODOLOGY_VERSION
    assert result["status"] == "INSUFFICIENT_CORPUS"
    assert result["promotion_authority"] is False


def test_crypto_edge_walk_forward_contract_keeps_holdout_untouched():
    cfg = settings()
    start = datetime(2026, 9, 1, tzinfo=timezone.utc)
    hours = 24 * 6
    bars = {
        "BTC/USD": _bars(start, hours=hours, base=Decimal("60000")),
        "ETH/USD": _bars(start, hours=hours, base=Decimal("3000")),
        "SOL/USD": _bars(start, hours=hours, base=Decimal("150")),
    }
    result = run_crypto_edge_discovery(
        settings=cfg,
        bars_by_symbol=bars,
        initial_equity=Decimal("100"),
        spread_bps=Decimal("10"),
        slippage_bps=Decimal("5"),
        min_train_days=1,
        validation_days=1,
        holdout_days=1,
        minimum_validation_trades=2,
    )

    assert result["methodology_version"] == METHODOLOGY_VERSION
    assert result["promotion_authority"] is False
    assert result["live_configuration_changed"] is False
    assert result["contract"]["candidate_set_frozen_before_validation"] is True
    assert result["contract"]["holdout_opened_only_after_multiplicity_adjusted_selection"] is True
    assert result["multiplicity"]["method"] == "by"
    assert result["multiplicity_adjusted"] is True
    assert result["dependence_adjusted"] is True
    assert result["no_lookahead_verified"] is True
    assert len(result["candidates"]) == 9

    holdout_start = datetime.fromisoformat(result["holdout_range"]["start"])
    for fold in result["folds"]:
        validation_end = datetime.fromisoformat(fold["validation_end"])
        assert validation_end <= holdout_start


def test_crypto_edge_worker_declares_no_execution_authority(monkeypatch):
    monkeypatch.setenv("CRYPTO_EDGE_DISCOVERY_INTERVAL_SECONDS", "3600")
    cfg = settings()
    runtime = CryptoEdgeDiscoveryRuntime(cfg)
    state = runtime.status()

    assert state["system"] == "GRAEN"
    assert state["program"] == "Crypto Edge Discovery v1"
    assert state["broker_orders_possible"] is False
    assert state["execution_authority"] is False


def test_crypto_edge_research_settings_do_not_mutate_live_settings():
    cfg = settings()
    research = _research_settings(
        cfg,
        ("BTC/USD", "ETH/USD", "SOL/USD", "LINK/USD"),
    )

    assert research.scan_symbols == (
        "BTC/USD",
        "ETH/USD",
        "SOL/USD",
        "LINK/USD",
    )
    assert research.confirmation_symbols == ("BTC/USD", "ETH/USD")
    assert research.dynamic_universe_enabled is False
    assert cfg.scan_symbols == ("BTC/USD", "ETH/USD", "SOL/USD")
