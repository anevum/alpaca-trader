import asyncio

from app.config import Settings
from app.strategy import RollingMomentumVwapStrategy
from app.velum_core import bootstrap_trade_distribution
from app.velum_service import VelumRuntime, _build_equity_strategy, _run_blocking


def settings() -> Settings:
    return Settings(
        ALLOWED_SYMBOLS="SPY,QQQ,SMH",
        SCAN_SYMBOLS="SPY,QQQ",
        CONFIRMATION_SYMBOLS="QQQ,SMH",
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


def test_velum_builds_the_equity_champion_strategy():
    strategy = _build_equity_strategy(settings())
    assert isinstance(strategy, RollingMomentumVwapStrategy)


def test_velum_runtime_declares_no_broker_order_authority():
    runtime = VelumRuntime(settings())
    status = runtime.status()
    assert status["system"] == "VELUM"
    assert status["mode"] == "research_replay_only"
    assert status["broker_orders_possible"] is False


def test_velum_event_key_versions_replay_methodology():
    suffix = "equity:2026-10-06"
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


def test_velum_blocking_work_is_thread_offloaded(monkeypatch):
    calls = []

    async def fake_to_thread(func, /, *args, **kwargs):
        calls.append((func, args, kwargs))
        return func(*args, **kwargs)

    monkeypatch.setattr(asyncio, "to_thread", fake_to_thread)

    def add(left, *, right):
        return left + right

    result = asyncio.run(_run_blocking(add, 2, right=3))
    assert result == 5
    assert len(calls) == 1
    assert calls[0][0] is add
