import asyncio
from datetime import datetime, timezone
from decimal import Decimal

from app.config import Settings
from app.replay import ReplayPosition
from app.velum_core import ContinuousReplayEngine, bootstrap_trade_distribution
from app.velum_service import (
    VelumRuntime,
    _canonical_identity,
    _crypto_settings,
    _run_blocking,
    build_counterfactual_from_snapshot,
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


def test_velum_runtime_declares_no_broker_order_authority():
    runtime = VelumRuntime(settings())
    status = runtime.status()
    assert status["system"] == "VELUM"
    assert status["mode"] == "research_replay_only"
    assert status["broker_orders_possible"] is False
    assert status["execution_authority"] is False
    assert status["credential_scope"]["broker_execution_runtime_loaded"] is False
    assert status["credential_scope"]["broker_client_instantiated"] is False
    assert status["credential_scope"]["shared_crypto_strategy_imports_broker_client_module"] is True
    assert status["credential_scope"]["provider_scope_verified"] is False


def test_velum_runtime_exposes_native_provenance(monkeypatch):
    monkeypatch.setenv("RAILWAY_GIT_COMMIT_SHA", "abc123")
    monkeypatch.setenv("RAILWAY_DEPLOYMENT_ID", "deployment-1")
    monkeypatch.setenv("RAILWAY_SERVICE_ID", "service-1")
    monkeypatch.setenv("RAILWAY_SERVICE_NAME", "rhen-velum")
    runtime = VelumRuntime(settings())
    status = runtime.status()
    provenance = status["runtime_provenance"]
    assert provenance["system_version"] == "velum-replay-v2"
    assert provenance["git_commit"] == "abc123"
    assert provenance["deployment_id"] == "deployment-1"
    assert provenance["service_id"] == "service-1"
    assert provenance["service_name"] == "rhen-velum"
    assert provenance["runtime_started_at"] == status["started_at"]
    assert status["last_heartbeat_at"] == status["started_at"]


def test_velum_native_heartbeat_runs_when_replay_is_scheduler_managed():
    async def scenario():
        runtime = VelumRuntime(settings())
        await runtime.start_heartbeat()
        await asyncio.sleep(0)
        assert runtime.status()["worker_alive"] is True
        assert runtime.status()["running"] is False
        await runtime.stop()
        assert runtime.heartbeat_task is None

    asyncio.run(scenario())


def test_counterfactual_snapshot_executes_in_velum_with_parity():
    snapshot = {
        "schema_version": "velum_counterfactual_input.v1",
        "session": "2026-09-30",
        "strategy_version_id": "LIVE-2026-09-25-003",
        "baseline_parameters": {},
        "candidates": [],
        "prior_reports": [],
        "input_identity": "sha256:immutable-input",
    }
    expected = build_counterfactual_from_snapshot(snapshot)
    snapshot["expected_counterfactual_lab"] = expected
    snapshot["expected_output_identity"] = _canonical_identity(expected)
    runtime = VelumRuntime(settings())

    async def emit(*args, **kwargs):
        return True

    runtime._emit = emit
    result = asyncio.run(runtime.run_counterfactual(snapshot))
    assert result["parity_match"] is True
    assert result["persisted"] is True
    assert result["counterfactual_lab"] == expected
    assert result["execution_authority"] is False
    assert result["live_configuration_changed"] is False


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


def test_velum_counterfactual_matches_rhen_inline_lab_identity():
    from datetime import date
    from types import SimpleNamespace

    from app.research_scheduler import ResearchReportScheduler
    from app.velum_service import _canonical_identity, build_counterfactual_from_snapshot

    session = date(2026, 9, 30)
    settings_obj = SimpleNamespace(
        strategy_version_id="LIVE-2026-09-25-003",
        min_momentum_pct=Decimal("0.0020"),
        min_vwap_edge_pct=Decimal("0.0000"),
        min_confirmations=2,
        max_vwap_extension_pct=Decimal("0.0080"),
    )
    scheduler = ResearchReportScheduler(
        settings_obj,
        SimpleNamespace(),
        SimpleNamespace(),
        SimpleNamespace(),
        SimpleNamespace(),
    )
    candidate = {
        "candidate_id": 1,
        "strategy_version_id": "LIVE-2026-09-25-003",
        "session": session.isoformat(),
        "symbol": "TEST",
        "features": {
            "current_close": "100.20",
            "session_vwap": "100.00",
            "momentum_pct": "0.0018",
            "vwap_edge_pct": "0.0020",
            "confirmation_passes": 2,
            "checks": {
                "fast_above_slow": True,
                "rising": True,
                "momentum_ok": False,
                "vwap_ok": True,
                "vwap_extension_ok": True,
                "confirmations_ok": True,
                "regime_ok": True,
            },
        },
        "checks": {
            "strategy": {
                "fast_above_slow": True,
                "rising": True,
                "momentum_ok": False,
                "vwap_ok": True,
                "vwap_extension_ok": True,
                "confirmations_ok": True,
                "regime_ok": True,
            }
        },
        "outcomes": [
            {
                "horizon_minutes": 15,
                "status": "complete",
                "forward_return": "0.003",
                "max_favorable_return": "0.004",
                "max_adverse_return": "-0.001",
            }
        ],
    }

    async def no_history(_session):
        return [], None

    scheduler._counterfactual_history_reports = no_history
    rhen_lab, warning = asyncio.run(
        scheduler._build_counterfactual_lab(session, [candidate])
    )
    assert warning is None

    snapshot = {
        "schema_version": "velum_counterfactual_input.v1",
        "session": session.isoformat(),
        "strategy_version_id": settings_obj.strategy_version_id,
        "baseline_parameters": {
            "min_momentum_pct": str(settings_obj.min_momentum_pct),
            "min_vwap_edge_pct": str(settings_obj.min_vwap_edge_pct),
            "min_confirmations": settings_obj.min_confirmations,
            "max_vwap_extension_pct": str(settings_obj.max_vwap_extension_pct),
        },
        "candidates": [candidate],
        "prior_reports": [],
    }
    velum_lab = build_counterfactual_from_snapshot(snapshot)
    assert _canonical_identity(velum_lab) == _canonical_identity(rhen_lab)


def test_velum_counterfactual_parity_result_is_research_only(monkeypatch):
    runtime = VelumRuntime(settings())
    runtime._emit = lambda *args, **kwargs: asyncio.sleep(0, result=True)
    lab = build_counterfactual_from_snapshot({
        "schema_version": "velum_counterfactual_input.v1",
        "session": "2026-09-30",
        "strategy_version_id": "LIVE-2026-09-25-003",
        "baseline_parameters": {
            "min_momentum_pct": "0.0020",
            "min_vwap_edge_pct": "0",
            "min_confirmations": "2",
            "max_vwap_extension_pct": "0.008",
        },
        "candidates": [],
        "prior_reports": [],
    })
    snapshot = {
        "schema_version": "velum_counterfactual_input.v1",
        "session": "2026-09-30",
        "strategy_version_id": "LIVE-2026-09-25-003",
        "baseline_parameters": lab["baseline_parameters"],
        "candidates": [],
        "prior_reports": [],
        "input_identity": "sha256:test",
        "expected_counterfactual_lab": lab,
        "expected_output_identity": _canonical_identity(lab),
    }
    result = asyncio.run(runtime.run_counterfactual(snapshot))
    assert result["parity_match"] is True
    assert result["persisted"] is True
    assert result["execution_authority"] is False
    assert result["broker_orders_possible"] is False
    assert result["live_configuration_changed"] is False
    assert result["promotion_authorized"] is False
