import asyncio
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from app.config import Settings
from app.replay import ReplayPosition
from app.velum_core import ContinuousReplayEngine, bootstrap_trade_distribution
from app.velum_service import (
    VelumRuntime,
    _crypto_settings,
    _fetch_candidate_replay_bars,
    _run_blocking,
    require_graen_token,
)
import app.velum_graen as velum_graen
from app.velum_graen import engineering_gate
from graen.crypto.autonomous_campaign import adaptive_candidate_specs
from graen.crypto.activity_shock_v9 import (
    METHODOLOGY_VERSION as V9_METHODOLOGY_VERSION,
    candidate_specs as v9_candidate_specs,
)
from graen.crypto.trend_pullback_v10 import (
    FAMILY as V10_FAMILY,
    METHODOLOGY_VERSION as V10_METHODOLOGY_VERSION,
    candidate_specs as v10_candidate_specs,
)
from graen.crypto.btc_trend_pullback_v11 import (
    FAMILY as V11_FAMILY,
    METHODOLOGY_VERSION as V11_METHODOLOGY_VERSION,
    candidate_specs as v11_candidate_specs,
)
from graen.crypto.btc_mechanisms_v12 import (
    FAMILY as V12_FAMILY,
    METHODOLOGY_VERSION as V12_METHODOLOGY_VERSION,
    candidate_specs as v12_candidate_specs,
)
from graen.crypto.btc_hypotheses_v13 import (
    FAMILY as V13_FAMILY,
    METHODOLOGY_VERSION as V13_METHODOLOGY_VERSION,
    candidate_specs as v13_candidate_specs,
)
from graen.crypto.btc_4h_consensus_v14_r2h import (
    FAMILY as V14_R2H_FAMILY,
    METHODOLOGY_VERSION as V14_R2H_METHODOLOGY_VERSION,
    candidate_spec as v14_r2h_candidate_spec,
)


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


def test_crypto_replay_blank_env_uses_crypto_defaults_not_equity_symbols(monkeypatch):
    cfg = Settings(
        ALLOWED_SYMBOLS="SPY,QQQ,SMH",
        SCAN_SYMBOLS="SPY",
        CONFIRMATION_SYMBOLS="QQQ,SMH",
        CRYPTO_ALWAYS_INCLUDE="BTC/USD,ETH/USD,SOL/USD",
        CRYPTO_CONFIRMATION_SYMBOLS="BTC/USD,ETH/USD",
        STRATEGY_NAME="rolling_momentum_vwap",
        MAX_DAILY_ORDERS=20,
        MAX_CONCURRENT_POSITIONS=5,
        MAX_NEW_ENTRIES_PER_CYCLE=2,
        MAX_TOTAL_POSITION_NOTIONAL="100",
    )
    monkeypatch.setenv("VELUM_CRYPTO_SYMBOLS", "")
    monkeypatch.setenv("VELUM_CRYPTO_CONFIRMATION_SYMBOLS", "")

    crypto = _crypto_settings(cfg)

    assert crypto.scan_symbols == ("BTC/USD", "ETH/USD", "SOL/USD")
    assert crypto.confirmation_symbols == ("BTC/USD", "ETH/USD")
    assert "SPY" not in crypto.scan_symbols
    assert "QQQ" not in crypto.confirmation_symbols


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


def test_graen_candidate_replay_gate_is_research_only_contract():
    spec = adaptive_candidate_specs(1)[0]
    summary = {
        "trade_count": 25,
        "independent_day_blocks": 12,
        "trades_per_day": 0.5,
        "expectancy_per_trade": 0.001,
        "profit_factor": 1.2,
        "symbol_concentration": {"max_share": 0.5},
    }
    scenarios = {
        "low": {
            "primary": {**summary, "expectancy_per_trade": 0.0015},
            "one_bar_delay": {**summary, "expectancy_per_trade": 0.0010},
        },
        "base": {
            "primary": {**summary, "expectancy_per_trade": 0.0012},
            "one_bar_delay": {**summary, "expectancy_per_trade": 0.0008},
        },
        "high": {
            "primary": summary,
            "one_bar_delay": {**summary, "expectancy_per_trade": 0.0005},
        },
    }
    passed, reasons = engineering_gate(spec, scenarios)
    assert passed is True
    assert reasons == []


def test_graen_candidate_replay_token_fails_closed(monkeypatch):
    monkeypatch.delenv("VELUM_GRAEN_TOKEN", raising=False)
    try:
        require_graen_token("not-a-real-token")
    except Exception as exc:
        assert getattr(exc, "status_code", None) == 503
    else:
        raise AssertionError("missing VELUM_GRAEN_TOKEN must fail closed")


def test_velum_dispatches_v9_candidate_without_execution_authority(monkeypatch):
    summary = {
        "trade_count": 30,
        "independent_day_blocks": 15,
        "trades_per_day": 0.6,
        "expectancy_per_trade": 0.001,
        "profit_factor": 1.3,
        "symbol_concentration": {"max_share": 0.5},
        "dependence_adjusted_null": {"p_value": 0.01},
    }

    def fake_v9(*args, **kwargs):
        return {
            "primary": dict(summary),
            "one_bar_delay": {**summary, "expectancy_per_trade": 0.0005},
            "candidate": kwargs["spec"].to_dict(),
        }

    monkeypatch.setattr(velum_graen, "evaluate_v9_candidate", fake_v9)
    result = velum_graen.replay_candidate(
        {},
        candidate_spec=v9_candidate_specs()[0].to_dict(),
        candidate_methodology=V9_METHODOLOGY_VERSION,
        start=datetime(2024, 1, 1, tzinfo=timezone.utc),
        end=datetime(2024, 1, 31, tzinfo=timezone.utc),
    )

    assert result["candidate_methodology"] == V9_METHODOLOGY_VERSION
    assert result["candidate_family"] == "activity_confirmed_momentum_continuation"
    assert result["engineering_gate"]["passed"] is True
    assert result["research_only"] is True
    assert result["execution_authority"] is False
    assert result["broker_orders_possible"] is False
    assert result["promotion_authorized"] is False


def test_velum_dispatches_v10_candidate_without_execution_authority(monkeypatch):
    summary = {
        "trade_count": 30,
        "independent_day_blocks": 15,
        "trades_per_day": 0.5,
        "expectancy_per_trade": 0.001,
        "profit_factor": 1.3,
        "symbol_concentration": {"max_share": 0.5},
        "dependence_adjusted_null": {"p_value": 0.01},
    }

    def fake_v10(*args, **kwargs):
        return {
            "primary": dict(summary),
            "one_bar_delay": {**summary, "expectancy_per_trade": 0.0005},
            "candidate": kwargs["spec"].to_dict(),
        }

    monkeypatch.setattr(velum_graen, "evaluate_v10_candidate", fake_v10)
    result = velum_graen.replay_candidate(
        {},
        candidate_spec=v10_candidate_specs()[0].to_dict(),
        candidate_methodology=V10_METHODOLOGY_VERSION,
        start=datetime(2024, 1, 1, tzinfo=timezone.utc),
        end=datetime(2024, 1, 31, tzinfo=timezone.utc),
    )

    assert result["candidate_methodology"] == V10_METHODOLOGY_VERSION
    assert result["candidate_family"] == V10_FAMILY
    assert result["engineering_gate"]["passed"] is True
    assert result["research_only"] is True
    assert result["execution_authority"] is False
    assert result["broker_orders_possible"] is False
    assert result["promotion_authorized"] is False


def test_velum_dispatches_v11_btc_candidate_without_execution_authority(monkeypatch):
    summary = {
        "trade_count": 30,
        "independent_day_blocks": 15,
        "trades_per_day": 0.5,
        "expectancy_per_trade": 0.001,
        "profit_factor": 1.3,
        "symbol_concentration": {"max_share": 1.0},
        "dependence_adjusted_null": {"p_value": 0.01},
    }

    def fake_v11(*args, **kwargs):
        return {
            "primary": dict(summary),
            "one_bar_delay": {**summary, "expectancy_per_trade": 0.0005},
            "candidate": kwargs["spec"].to_dict(),
        }

    monkeypatch.setattr(velum_graen, "evaluate_v11_candidate", fake_v11)
    result = velum_graen.replay_candidate(
        {},
        candidate_spec=v11_candidate_specs()[0].to_dict(),
        candidate_methodology=V11_METHODOLOGY_VERSION,
        start=datetime(2026, 9, 1, tzinfo=timezone.utc),
        end=datetime(2026, 10, 1, tzinfo=timezone.utc),
    )

    assert result["candidate_methodology"] == V11_METHODOLOGY_VERSION
    assert result["candidate_family"] == V11_FAMILY
    assert result["engineering_gate"]["passed"] is True
    assert result["execution_authority"] is False
    assert result["broker_orders_possible"] is False
    assert result["promotion_authorized"] is False


def test_velum_dispatches_v12_btc_candidate_without_execution_authority(monkeypatch):
    summary = {
        "trade_count": 30,
        "independent_day_blocks": 15,
        "trades_per_day": 0.5,
        "expectancy_per_trade": 0.001,
        "profit_factor": 1.3,
        "symbol_concentration": {"max_share": 1.0},
        "dependence_adjusted_null": {"p_value": 0.01},
    }

    def fake_v12(*args, **kwargs):
        return {
            "primary": dict(summary),
            "one_bar_delay": {**summary, "expectancy_per_trade": 0.0005},
            "candidate": kwargs["spec"].to_dict(),
        }

    monkeypatch.setattr(velum_graen, "evaluate_v12_candidate", fake_v12)
    result = velum_graen.replay_candidate(
        {},
        candidate_spec=v12_candidate_specs()[0].to_dict(),
        candidate_methodology=V12_METHODOLOGY_VERSION,
        start=datetime(2026, 9, 1, tzinfo=timezone.utc),
        end=datetime(2026, 10, 1, tzinfo=timezone.utc),
    )

    assert result["candidate_methodology"] == V12_METHODOLOGY_VERSION
    assert result["candidate_family"] == V12_FAMILY
    assert result["engineering_gate"]["passed"] is True
    assert result["execution_authority"] is False
    assert result["broker_orders_possible"] is False
    assert result["promotion_authorized"] is False


def test_velum_dispatches_v13_exact_candidate_without_execution_authority(monkeypatch):
    summary = {
        "trade_count": 30,
        "independent_day_blocks": 15,
        "trades_per_day": 0.6,
        "expectancy_per_trade": 0.001,
        "profit_factor": 1.3,
        "symbol_concentration": {"max_share": 1.0},
        "dependence_adjusted_null": {"p_value": 0.01},
    }

    def fake_v13(*args, **kwargs):
        return {
            "primary": dict(summary),
            "one_bar_delay": {**summary, "expectancy_per_trade": 0.0005},
            "candidate": kwargs["spec"].to_dict(),
        }

    monkeypatch.setattr(velum_graen, "evaluate_v13_candidate", fake_v13)
    result = velum_graen.replay_candidate(
        {},
        candidate_spec=v13_candidate_specs()[0].to_dict(),
        candidate_methodology=V13_METHODOLOGY_VERSION,
        start=datetime(2026, 8, 1, tzinfo=timezone.utc),
        end=datetime(2026, 10, 1, tzinfo=timezone.utc),
    )
    assert result["candidate_methodology"] == V13_METHODOLOGY_VERSION
    assert result["candidate_family"] == V13_FAMILY
    assert result["engineering_gate"]["passed"] is True
    assert result["execution_authority"] is False
    assert result["broker_orders_possible"] is False
    assert result["promotion_authorized"] is False


def test_velum_r2h_fetch_contract_includes_slow_signal_warmup():
    start = datetime(2026, 4, 4, tzinfo=timezone.utc)
    end = datetime(2026, 10, 1, tzinfo=timezone.utc)
    symbols, fetch_start, fetch_end, timeframe = velum_graen.replay_fetch_contract(
        V14_R2H_METHODOLOGY_VERSION,
        start=start,
        end=end,
    )
    assert symbols == ("BTC/USD",)
    assert fetch_start < start
    assert (start - fetch_start).days == 260
    assert fetch_end == end
    assert timeframe == "4Hour"


def test_velum_dispatches_r2h_slow_candidate_without_legacy_trade_gate(monkeypatch):
    spec = v14_r2h_candidate_spec()
    replay_payload = {
        "scenarios": {
            "taker_stress_30bp": {
                "bar_count": 1080,
                "total_return": 0.12,
                "sharpe": 0.8,
                "max_drawdown": -0.10,
            },
            "severe_stress_50bp": {
                "bar_count": 1080,
                "total_return": 0.10,
                "sharpe": 0.7,
                "max_drawdown": -0.11,
            },
        },
        "one_bar_execution_delay": {
            "bar_count": 1080,
            "total_return": 0.09,
            "sharpe": 0.6,
            "max_drawdown": -0.12,
        },
        "engineering_gate": {
            "passed": True,
            "reasons": [],
        },
    }

    monkeypatch.setattr(
        velum_graen,
        "evaluate_v14_r2h_replay",
        lambda *args, **kwargs: replay_payload,
    )
    result = velum_graen.replay_candidate(
        {},
        candidate_spec=spec.to_dict(),
        candidate_methodology=V14_R2H_METHODOLOGY_VERSION,
        start=datetime(2026, 4, 4, tzinfo=timezone.utc),
        end=datetime(2026, 10, 1, tzinfo=timezone.utc),
    )

    assert result["candidate_methodology"] == V14_R2H_METHODOLOGY_VERSION
    assert result["candidate_family"] == V14_R2H_FAMILY
    assert result["engineering_gate"]["passed"] is True
    assert result["independent_confirmatory_evidence"] is False
    assert result["execution_authority"] is False
    assert result["broker_orders_possible"] is False
    assert result["promotion_authorized"] is False


def test_velum_legacy_fetch_contract_keeps_service_default_timeframe():
    start = datetime(2026, 9, 1, tzinfo=timezone.utc)
    end = datetime(2026, 10, 1, tzinfo=timezone.utc)
    symbols, fetch_start, fetch_end, timeframe = velum_graen.replay_fetch_contract(
        V13_METHODOLOGY_VERSION,
        start=start,
        end=end,
    )
    assert tuple(symbols) == tuple(velum_graen.GRAEN_CONTEXT_UNIVERSE)
    assert fetch_start == start - timedelta(hours=8)
    assert fetch_end == end + timedelta(hours=3)
    assert timeframe is None


def test_r2h_candidate_fetch_chunks_long_4h_window_and_deduplicates():
    class FakeMarketData:
        def __init__(self):
            self.calls = []

        async def historical_crypto_bars_many(self, symbols, *, start, end):
            self.calls.append((tuple(symbols), start, end))
            index = len(self.calls)
            duplicate = "2026-05-01T00:00:00Z"
            return {
                "BTC/USD": [
                    {"t": duplicate, "c": 100 + index},
                    {"t": f"2026-05-{index + 1:02d}T00:00:00Z", "c": 101 + index},
                ]
            }

    async def scenario():
        market = FakeMarketData()
        start = datetime(2025, 1, 1, tzinfo=timezone.utc)
        end = datetime(2026, 3, 17, tzinfo=timezone.utc)
        bars, chunks = await _fetch_candidate_replay_bars(
            market,
            ("BTC/USD",),
            start=start,
            end=end,
            chunk_days=60,
        )
        assert chunks == 8
        assert len(market.calls) == 8
        assert all(
            call_end < next_start
            for (_, _, call_end), (_, next_start, _) in zip(
                market.calls,
                market.calls[1:],
            )
        )
        stamps = [row["t"] for row in bars["BTC/USD"]]
        assert len(stamps) == len(set(stamps))
        assert stamps == sorted(stamps)

    asyncio.run(scenario())


def test_legacy_candidate_fetch_remains_single_request():
    class FakeMarketData:
        def __init__(self):
            self.calls = []

        async def historical_crypto_bars_many(self, symbols, *, start, end):
            self.calls.append((tuple(symbols), start, end))
            return {"BTC/USD": [{"t": "2026-09-01T00:00:00Z", "c": 100}]}

    async def scenario():
        market = FakeMarketData()
        start = datetime(2026, 9, 1, tzinfo=timezone.utc)
        end = datetime(2026, 10, 1, tzinfo=timezone.utc)
        bars, chunks = await _fetch_candidate_replay_bars(
            market,
            ("BTC/USD",),
            start=start,
            end=end,
            chunk_days=None,
        )
        assert chunks == 1
        assert len(market.calls) == 1
        assert market.calls[0] == (("BTC/USD",), start, end)
        assert len(bars["BTC/USD"]) == 1

    asyncio.run(scenario())
