from datetime import datetime, timezone
from decimal import Decimal

from app.config import Settings
from app.replay import ReplayPosition
from app.velum_core import ContinuousReplayEngine, bootstrap_trade_distribution
from app.velum_service import VelumRuntime, _crypto_settings


class HoldStrategy:
    pass


def settings() -> Settings:
    return Settings(
        ALLOWED_SYMBOLS="BTC/USD,ETH/USD,SOL/USD",
        SCAN_SYMBOLS="BTC/USD,SOL/USD",
        CONFIRMATION_SYMBOLS="ETH/USD",
        STRATEGY_NAME="rolling_momentum_vwap",
        MAX_DAILY_ORDERS=20,
        MAX_CONCURRENT_POSITIONS=5,
        MAX_NEW_ENTRIES_PER_CYCLE=2,
        MAX_TOTAL_POSITION_NOTIONAL="100",
    )


def test_bootstrap_trade_distribution_is_deterministic():
    trades = [
        {"net_pnl": "1.00"},
        {"net_pnl": "-0.50"},
        {"net_pnl": "0.25"},
    ]
    left = bootstrap_trade_distribution(trades, paths=100, seed=42)
    right = bootstrap_trade_distribution(trades, paths=100, seed=42)
    assert left == right
    assert left["forecast"] is False
    assert left["trade_count"] == 3


def test_continuous_engine_has_no_equity_force_flat_exit():
    cfg = settings()
    engine = ContinuousReplayEngine(cfg, HoldStrategy())
    position = ReplayPosition(
        symbol="BTC/USD",
        qty=Decimal("0.001"),
        entry_price=Decimal("100"),
        entry_reference=Decimal("100"),
        entry_at=datetime(2026, 9, 29, 19, 50, tzinfo=timezone.utc),
        notional=Decimal("0.1"),
        quality_score=50.0,
    )
    bar = {
        "t": "2026-09-29T20:00:00Z",
        "o": 100,
        "h": 100.2,
        "l": 99.9,
        "c": 100.1,
    }
    assert engine._exit_decision(
        position,
        bar,
        datetime(2026, 9, 29, 20, 1, tzinfo=timezone.utc),
    ) is None


def test_crypto_replay_settings_are_isolated_from_equity_symbols(monkeypatch):
    cfg = settings()
    monkeypatch.setenv("VELUM_CRYPTO_SYMBOLS", "BTC/USD,ETH/USD,SOL/USD")
    monkeypatch.setenv("VELUM_CRYPTO_CONFIRMATION_SYMBOLS", "BTC/USD,ETH/USD")
    crypto = _crypto_settings(cfg)
    assert crypto.scan_symbols == ("BTC/USD", "ETH/USD", "SOL/USD")
    assert crypto.confirmation_symbols == ("BTC/USD", "ETH/USD")
    assert crypto.dynamic_universe_enabled is False
    assert cfg.scan_symbols == ("BTC/USD", "SOL/USD")


def test_velum_runtime_declares_no_broker_order_authority():
    runtime = VelumRuntime(settings())
    status = runtime.status()
    assert status["system"] == "VELUM"
    assert status["mode"] == "research_replay_only"
    assert status["broker_orders_possible"] is False


def test_crypto_bucket_is_hourly_by_default(monkeypatch):
    monkeypatch.delenv("VELUM_CRYPTO_INTERVAL_MINUTES", raising=False)
    runtime = VelumRuntime(settings())
    bucket = runtime._crypto_bucket_end(
        datetime(2026, 9, 29, 23, 22, 45, tzinfo=timezone.utc)
    )
    assert bucket == datetime(2026, 9, 29, 23, 0, tzinfo=timezone.utc)


def test_velum_event_key_versions_replay_methodology():
    suffix = "crypto:2026-09-30T00:00:00+00:00:24h"
    v1 = VelumRuntime._event_key(
        "LIVE-2026-09-25-003",
        "velum_replay_result",
        "velum-replay-v1",
        suffix,
    )
    v2 = VelumRuntime._event_key(
        "LIVE-2026-09-25-003",
        "velum_replay_result",
        "velum-replay-v2",
        suffix,
    )
    assert v1 != v2
    assert "velum-replay-v2" in v2
    assert len(v2) <= 200
