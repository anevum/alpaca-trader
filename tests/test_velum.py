import asyncio
from datetime import date, datetime, timezone

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



def test_one_shot_velum_replay_validates_session_and_exits(monkeypatch):
    runtime = VelumRuntime(settings())

    async def calendar_details(*, start, end):
        assert start == date(2026, 10, 7)
        assert end == date(2026, 10, 7)
        return [{"date": "2026-10-07", "close": "16:00"}]

    async def run_equity(session):
        runtime.last_equity_session = session.isoformat()
        runtime.last_equity_run_id = "velum:test"
        runtime.last_equity_summary = {"trades": 3}

    monkeypatch.setattr(runtime.market_data, "market_calendar_details", calendar_details)
    monkeypatch.setattr(runtime, "_run_equity", run_equity)

    result = asyncio.run(
        runtime.run_equity_session(
            date(2026, 10, 7),
            now=datetime(2026, 10, 7, 21, 0, tzinfo=timezone.utc),
        )
    )

    assert result["ok"] is True
    assert result["duplicate"] is False
    assert result["session"] == "2026-10-07"
    assert result["velum_run_id"] == "velum:test"
    assert result["summary"] == {"trades": 3}
    assert result["broker_orders_possible"] is False
    assert result["execution_authority"] is False
    assert runtime.task is None

    duplicate = asyncio.run(
        runtime.run_equity_session(
            date(2026, 10, 7),
            now=datetime(2026, 10, 7, 21, 1, tzinfo=timezone.utc),
        )
    )
    assert duplicate["duplicate"] is True
    assert duplicate["broker_orders_possible"] is False


def test_one_shot_velum_replay_refuses_incomplete_session(monkeypatch):
    runtime = VelumRuntime(settings())

    async def calendar_details(*, start, end):
        return [{"date": "2026-10-07", "close": "16:00"}]

    monkeypatch.setattr(runtime.market_data, "market_calendar_details", calendar_details)

    try:
        asyncio.run(
            runtime.run_equity_session(
                date(2026, 10, 7),
                now=datetime(2026, 10, 7, 19, 0, tzinfo=timezone.utc),
            )
        )
    except RuntimeError as exc:
        assert str(exc) == "equity session is not complete"
    else:
        raise AssertionError("incomplete session should not replay")
