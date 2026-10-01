from datetime import datetime, timedelta, timezone

from app.config import Settings
import graen.crypto.candidate_shadow as candidate_shadow
from graen.crypto.activity_shock_v9 import (
    METHODOLOGY_VERSION as V9_METHODOLOGY_VERSION,
    candidate_specs,
)
from graen.crypto.candidate_shadow import CandidateForwardShadow


UTC = timezone.utc


def settings() -> Settings:
    return Settings(
        ALLOWED_SYMBOLS="BTC/USD,ETH/USD,SOL/USD",
        SCAN_SYMBOLS="BTC/USD,ETH/USD,SOL/USD",
        CONFIRMATION_SYMBOLS="BTC/USD,ETH/USD",
        CRYPTO_ALWAYS_INCLUDE="BTC/USD,ETH/USD,SOL/USD",
        CRYPTO_CONFIRMATION_SYMBOLS="BTC/USD,ETH/USD",
        EXECUTION_ENABLED=False,
        LIVE_TRADING=False,
        BOT_ARMED=False,
        CRYPTO_EXECUTION_ENABLED=False,
    )


def activation() -> dict:
    spec = candidate_specs()[0].to_dict()
    return {
        "schema_version": "graen.candidate_shadow.activation.v1",
        "activation_id": "activation-test-001",
        "problem_id": "11111111-1111-1111-1111-111111111111",
        "graen_run_id": "22222222-2222-2222-2222-222222222222",
        "campaign_id": "crypto-activity-shock-v9",
        "epoch_index": 0,
        "generation": 1,
        "candidate_methodology": V9_METHODOLOGY_VERSION,
        "candidate_id": spec["candidate_id"],
        "candidate_spec": spec,
        "velum_artifact_id": "artifact-velum",
        "activated_at": datetime.now(UTC).isoformat(),
        "research_only": True,
        "promotion_authorized": False,
        "execution_authority": False,
        "broker_orders_possible": False,
    }


def test_candidate_shadow_is_broker_proof_and_restart_restorable():
    runtime = CandidateForwardShadow(settings())
    runtime.activate(activation())

    assert runtime.active is True
    assert runtime.execution_authority is False
    assert runtime.broker_orders_possible is False
    assert runtime.status()["crypto_execution_enabled"] is False

    snapshot = runtime.snapshot()
    restored = CandidateForwardShadow(settings())
    restored.restore(snapshot)

    assert restored.status()["activation"]["activation_id"] == "activation-test-001"
    assert restored.execution_authority is False
    assert restored.broker_orders_possible is False


def test_candidate_shadow_negative_forward_sample_rejects_without_promotion(monkeypatch):
    runtime = CandidateForwardShadow(settings())
    runtime.activate(activation())

    monkeypatch.setattr(
        candidate_shadow,
        "_moving_block_null_pvalue",
        lambda values, **kwargs: {"p_value": 1.0},
    )
    start = datetime(2026, 1, 1, tzinfo=UTC)
    symbols = ("BTC/USD", "ETH/USD", "SOL/USD")
    for index in range(60):
        runtime.closed.append({
            "symbol": symbols[index % len(symbols)],
            "exit_at": (start + timedelta(days=index % 30)).isoformat(),
            "stressed_cost_net_return": -0.001,
        })

    checkpoint = runtime._checkpoint()
    assert checkpoint["status"] == "SHADOW_REJECTED"
    assert checkpoint["trade_count"] == 60
    assert checkpoint["promotion_authorized"] is False
    assert checkpoint["execution_authority"] is False
    assert checkpoint["broker_orders_possible"] is False


def test_candidate_shadow_positive_forward_sample_requires_fixed_gate(monkeypatch):
    runtime = CandidateForwardShadow(settings())
    runtime.activate(activation())

    monkeypatch.setattr(
        candidate_shadow,
        "_moving_block_null_pvalue",
        lambda values, **kwargs: {"p_value": 0.01},
    )
    start = datetime(2026, 1, 1, tzinfo=UTC)
    symbols = ("BTC/USD", "ETH/USD", "SOL/USD")
    for index in range(30):
        runtime.closed.append({
            "symbol": symbols[index % len(symbols)],
            "exit_at": (start + timedelta(days=index)).isoformat(),
            "stressed_cost_net_return": 0.002 if index % 5 else -0.001,
        })

    checkpoint = runtime._checkpoint()
    assert checkpoint["status"] == "READY_FOR_HUMAN_REVIEW"
    assert checkpoint["independent_day_blocks"] == 30
    assert checkpoint["promotion_authorized"] is False
