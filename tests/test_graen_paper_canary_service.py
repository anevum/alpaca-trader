from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.config import Settings
from app.graen.paper_canary_service import PaperCanaryService
from app.research_agent.strategy_grammar import build_manifest
from app.research_agent.strategy_runner import (
    RUNNER_VERSION as STRATEGY_RUNNER_VERSION,
    compile_candidate,
)


UTC = timezone.utc


def settings() -> Settings:
    return Settings(
        ALPACA_API_KEY="paper-key",
        ALPACA_API_SECRET="paper-secret",
        TRADING_MODE="paper",
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


def activation_payload() -> dict:
    manifest = build_manifest(
        hypothesis_id="AUTO-PAPER-01",
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
        "problem_id": "problem-paper-1",
        "graen_run_id": "run-paper-1",
        "candidate_methodology": STRATEGY_RUNNER_VERSION,
        "candidate_spec": spec,
        "shadow_activation_id": "shadow-123",
        "shadow_checkpoint": {
            "status": "READY_FOR_PAPER",
            "candidate_id": spec["candidate_id"],
            "execution_authority": False,
            "live_promotion_authorized": False,
        },
    }


def test_paper_service_is_hard_gated_to_paper_and_idempotent(tmp_path):
    service = PaperCanaryService(
        settings(),
        state_path=tmp_path / "paper.json",
        token="p" * 32,
    )
    first = service.activate(activation_payload())
    second = service.activate(activation_payload())

    assert first["duplicate"] is False
    assert second["duplicate"] is True
    assert service.settings.trading_mode == "paper"
    assert service.settings.live_trading is False
    assert service.execution_authority == "PAPER_ONLY"
    assert service.live_execution_authority is False
    assert service.active_id == first["activation"]["paper_activation_id"]
    assert (tmp_path / "paper.json").exists()

    restored = PaperCanaryService(
        settings(),
        state_path=tmp_path / "paper.json",
        token="p" * 32,
    )
    restored.restore()
    assert restored.active_id == first["activation"]["paper_activation_id"]
    assert restored.live_execution_authority is False


def test_paper_activation_rejects_unproven_shadow(tmp_path):
    service = PaperCanaryService(
        settings(),
        state_path=tmp_path / "paper.json",
        token="p" * 32,
    )
    payload = activation_payload()
    payload["shadow_checkpoint"]["status"] = "COLLECTING"
    try:
        service.activate(payload)
    except ValueError as exc:
        assert "ready_for_paper" in str(exc)
    else:
        raise AssertionError("unproven shadow candidate must not enter paper")


def test_paper_checkpoint_pass_never_authorizes_live(tmp_path):
    service = PaperCanaryService(
        settings(),
        state_path=tmp_path / "paper.json",
        token="p" * 32,
    )
    activation = service.activate(activation_payload())["activation"]
    row = service.candidates[activation["paper_activation_id"]]
    started = datetime.now(UTC) - timedelta(days=10)
    row["started_at"] = started.isoformat()
    row["state"] = "ACTIVE"
    row["trades"] = [
        {
            "exit_at": (started + timedelta(days=index)).isoformat(),
            "net_return": 0.002 if index % 4 else -0.0005,
            "entry_slippage_pct": "0.0002",
        }
        for index in range(8)
    ]

    checkpoint = service._checkpoint(row)
    assert checkpoint["status"] == "PAPER_PASSED"
    assert checkpoint["next_condition"] == "HUMAN_DECISION_REQUIRED"
    assert checkpoint["paper_execution_authorized"] is True
    assert checkpoint["live_execution_authorized"] is False
    assert checkpoint["live_promotion_authorized"] is False


def test_paper_checkpoint_terminal_failure_returns_to_research(tmp_path):
    service = PaperCanaryService(
        settings(),
        state_path=tmp_path / "paper.json",
        token="p" * 32,
    )
    activation = service.activate(activation_payload())["activation"]
    row = service.candidates[activation["paper_activation_id"]]
    started = datetime.now(UTC) - timedelta(days=50)
    row["started_at"] = started.isoformat()
    row["state"] = "ACTIVE"
    row["trades"] = [
        {
            "exit_at": (started + timedelta(days=index)).isoformat(),
            "net_return": -0.001,
            "entry_slippage_pct": "0.0002",
        }
        for index in range(8)
    ]

    checkpoint = service._checkpoint(row)
    assert checkpoint["status"] == "PAPER_REJECTED"
    assert checkpoint["next_condition"] == "RESEARCHING"
    assert checkpoint["live_execution_authorized"] is False
