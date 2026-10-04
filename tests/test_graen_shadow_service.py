from __future__ import annotations

from datetime import datetime, timezone

from app.config import Settings
from app.graen.shadow_service import ForwardShadowService
from app.research_agent.strategy_grammar import build_manifest
from app.research_agent.strategy_runner import (
    RUNNER_VERSION as STRATEGY_RUNNER_VERSION,
    compile_candidate,
)


UTC = timezone.utc


def settings() -> Settings:
    return Settings(
        ALPACA_API_KEY="test-key",
        ALPACA_API_SECRET="test-secret",
        EXECUTION_ENABLED=False,
        LIVE_TRADING=False,
        BOT_ARMED=False,
        CRYPTO_EXECUTION_ENABLED=False,
        FOUNDATION_SHADOW_ENABLED=False,
        ALLOWED_SYMBOLS="BTC/USD,ETH/USD,SOL/USD",
        SCAN_SYMBOLS="BTC/USD,ETH/USD,SOL/USD",
        CONFIRMATION_SYMBOLS="BTC/USD,ETH/USD",
        CRYPTO_ALWAYS_INCLUDE="BTC/USD,ETH/USD,SOL/USD",
        CRYPTO_CONFIRMATION_SYMBOLS="BTC/USD,ETH/USD",
    )


def candidate_payload() -> dict:
    manifest = build_manifest(
        hypothesis_id="AUTO-SHADOW-HOST-01",
        family="cross_asset_diffusion",
        mechanism="BTC information diffusion into liquid crypto followers.",
        information_source="cross_asset_returns",
        feature="lead_lag_gap",
        transformation="residualize_btc",
        regime="dispersion_bucket",
        trigger="threshold",
        entry="market_next_bar",
        exit="time_60m",
        parameters={
            "hold_minutes": 60,
            "scan_minutes": 10,
            "lookback_minutes": 30,
            "concentration_limit": 0.70,
            "leader_symbol": "BTC/USD",
            "leader_threshold": 0.0035,
            "lag_gap_threshold": 0.0015,
            "min_target_return": -0.005,
            "max_target_return": 0.0035,
            "min_breadth_positive": 3,
            "require_btc_nonnegative": False,
        },
        symbols=("BTC/USD", "ETH/USD", "SOL/USD"),
        timeframe="5m",
        falsification_statement="Reject if stressed-cost forward expectancy is nonpositive.",
    )
    spec = compile_candidate(manifest).to_dict()
    return {
        "problem_id": "problem-1",
        "graen_run_id": "run-1",
        "campaign_id": "strategy-manifest-autonomous-v1",
        "epoch_index": 1,
        "generation": 1,
        "candidate_methodology": STRATEGY_RUNNER_VERSION,
        "candidate_spec": spec,
        "velum_artifact_id": "velum-1",
        "evidence_phase": "FORWARD_SHADOW",
    }


def test_shadow_service_is_broker_proof_restart_durable_and_idempotent(tmp_path):
    path = tmp_path / "shadow.json"
    service = ForwardShadowService(
        settings(),
        state_path=path,
        token="x" * 32,
        max_active=4,
    )
    first = service.activate(candidate_payload())
    second = service.activate(candidate_payload())

    assert first["duplicate"] is False
    assert second["duplicate"] is True
    assert first["activation"]["activation_id"] == second["activation"]["activation_id"]
    assert service.execution_authority is False
    assert service.broker_orders_possible is False
    assert service.status()["crypto_execution_enabled"] is False
    assert path.exists()

    restored = ForwardShadowService(
        settings(),
        state_path=path,
        token="x" * 32,
        max_active=4,
    )
    restored.restore()
    assert list(restored.runtimes) == [first["activation"]["activation_id"]]
    assert restored.status()["active_count"] == 1
    assert restored.status()["candidates"][0]["candidate_methodology"] == STRATEGY_RUNNER_VERSION


def test_strategy_runner_shadow_success_projects_ready_for_paper(tmp_path, monkeypatch):
    service = ForwardShadowService(
        settings(),
        state_path=tmp_path / "shadow.json",
        token="y" * 32,
    )
    activation = service.activate(candidate_payload())["activation"]
    runtime = service.runtimes[activation["activation_id"]]

    monkeypatch.setattr(
        "graen.crypto.candidate_shadow._moving_block_null_pvalue",
        lambda values, **kwargs: {"p_value": 0.01},
    )
    start = datetime(2026, 1, 1, tzinfo=UTC)
    symbols = ("BTC/USD", "ETH/USD", "SOL/USD")
    for index in range(30):
        runtime.closed.append({
            "symbol": symbols[index % len(symbols)],
            "exit_at": (start.replace(day=1) if index == 0 else start).isoformat(),
            "stressed_cost_net_return": 0.002,
        })
    # Ensure 30 independent UTC dates for the fixed shadow gate.
    runtime.closed = [
        {
            "symbol": symbols[index % len(symbols)],
            "exit_at": f"2026-01-{index + 1:02d}T00:00:00+00:00",
            "stressed_cost_net_return": 0.002,
        }
        for index in range(30)
    ]
    checkpoint = service._project_checkpoint(runtime)
    assert checkpoint["raw_status"] == "READY_FOR_HUMAN_REVIEW"
    assert checkpoint["status"] == "READY_FOR_PAPER"
    assert checkpoint["live_promotion_authorized"] is False
    assert checkpoint["execution_authority"] is False
