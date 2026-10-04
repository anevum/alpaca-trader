from __future__ import annotations

import asyncio

import pytest
from fastapi import HTTPException

from app.config import Settings
from app.graen.shadow_host import CandidateShadowHost
from graen.crypto.activity_shock_v9 import (
    METHODOLOGY_VERSION as V9_METHODOLOGY_VERSION,
    candidate_specs,
)


class EventSink:
    def __init__(self):
        self.events = []

    def emit(self, **event):
        self.events.append(event)


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


def activation_payload() -> dict:
    spec = candidate_specs()[0].to_dict()
    return {
        "problem_id": "11111111-1111-1111-1111-111111111111",
        "graen_run_id": "22222222-2222-2222-2222-222222222222",
        "campaign_id": "crypto-activity-shock-v9",
        "epoch_index": 0,
        "generation": 1,
        "candidate_methodology": V9_METHODOLOGY_VERSION,
        "candidate_spec": spec,
        "velum_artifact_id": "artifact-velum",
        "evidence_phase": "FORWARD_SHADOW",
    }


def test_shadow_host_requires_explicit_shared_token(tmp_path):
    host = CandidateShadowHost(
        settings(),
        EventSink(),
        token="",
        state_path=tmp_path / "shadow.json",
    )
    assert host.auth_configured is False
    with pytest.raises(HTTPException) as exc:
        host.require_token("anything")
    assert exc.value.status_code == 503
    assert host.execution_authority is False
    assert host.broker_orders_possible is False


def test_shadow_host_activation_is_idempotent_and_durable(tmp_path):
    async def scenario():
        sink = EventSink()
        path = tmp_path / "shadow.json"
        host = CandidateShadowHost(
            settings(),
            sink,
            token="s" * 40,
            state_path=path,
        )
        first = await host.activate(activation_payload())
        second = await host.activate(activation_payload())

        assert first["duplicate"] is False
        assert second["duplicate"] is True
        assert first["activation"]["activation_id"] == second["activation"]["activation_id"]
        assert first["execution_authority"] is False
        assert first["broker_orders_possible"] is False
        assert path.exists()
        assert len(sink.events) == 1
        assert sink.events[0]["event_type"] == "graen_candidate_shadow_activated"

        restored = CandidateShadowHost(
            settings(),
            EventSink(),
            token="s" * 40,
            state_path=path,
        )
        await restored.restore()
        status = restored.status()
        assert status["active"] is True
        assert status["candidate_id"] == first["activation"]["candidate_id"]
        assert status["execution_authority"] is False
        assert status["broker_orders_possible"] is False

    asyncio.run(scenario())


def test_shadow_host_bad_restart_state_fails_closed_without_rhen_failure(tmp_path):
    async def scenario():
        path = tmp_path / "shadow.json"
        path.write_text("{not-json", encoding="utf-8")
        host = CandidateShadowHost(
            settings(),
            EventSink(),
            token="s" * 40,
            state_path=path,
        )
        await host.restore()
        assert host.runtime.active is False
        assert host.last_error is not None
        assert host.last_error.startswith("restore_failed:")
        assert host.execution_authority is False

    asyncio.run(scenario())


def test_shadow_host_rejects_wrong_token(tmp_path):
    host = CandidateShadowHost(
        settings(),
        EventSink(),
        token="a" * 40,
        state_path=tmp_path / "shadow.json",
    )
    host.require_token("a" * 40)
    with pytest.raises(HTTPException) as exc:
        host.require_token("b" * 40)
    assert exc.value.status_code == 401



def test_shadow_host_queues_candidates_and_retains_exact_terminal_checkpoint(tmp_path):
    async def scenario():
        host = CandidateShadowHost(
            settings(),
            EventSink(),
            token="s" * 40,
            state_path=tmp_path / "shadow.json",
        )
        first_payload = activation_payload()
        second_payload = activation_payload()
        second_payload["problem_id"] = "33333333-3333-3333-3333-333333333333"
        second_payload["graen_run_id"] = "44444444-4444-4444-4444-444444444444"

        first = await host.activate(first_payload)
        second = await host.activate(second_payload)
        first_id = first["activation"]["activation_id"]
        second_id = second["activation"]["activation_id"]

        assert second["queued"] is True
        assert host.status()["queue_count"] == 1
        assert host.checkpoint(second_id)["checkpoint"]["status"] == "QUEUED"

        async def no_events():
            return []

        host.runtime.cycle = no_events
        host._project_checkpoint = lambda _runtime: {
            "status": "SHADOW_REJECTED",
            "candidate_id": first["activation"]["candidate_id"],
            "candidate_methodology": first["activation"]["candidate_methodology"],
            "execution_authority": False,
            "broker_orders_possible": False,
        }

        await host.cycle_once()

        completed = host.checkpoint(first_id)
        assert completed is not None
        assert completed["completed"] is True
        assert completed["checkpoint"]["status"] == "SHADOW_REJECTED"
        assert host.status()["queue_count"] == 0
        assert host.status()["active"] is True
        assert host._active_id() == second_id
        assert host.execution_authority is False
        assert host.broker_orders_possible is False

    asyncio.run(scenario())


def test_shadow_host_strategy_runner_projects_ready_for_paper(tmp_path):
    from app.research_agent.strategy_grammar import build_manifest
    from app.research_agent.strategy_runner import (
        RUNNER_VERSION as STRATEGY_RUNNER_VERSION,
        compile_candidate,
    )

    async def scenario():
        host = CandidateShadowHost(
            settings(),
            EventSink(),
            token="s" * 40,
            state_path=tmp_path / "shadow.json",
        )
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
            falsification_statement=(
                "Reject if stressed-cost forward expectancy is nonpositive."
            ),
        )
        spec = compile_candidate(manifest).to_dict()
        payload = activation_payload()
        payload["campaign_id"] = "strategy-manifest-autonomous-v1"
        payload["candidate_methodology"] = STRATEGY_RUNNER_VERSION
        payload["candidate_spec"] = spec

        activation = await host.activate(payload)
        host.runtime.last_checkpoint = {
            "status": "READY_FOR_HUMAN_REVIEW",
            "trade_count": 30,
        }
        host.runtime.last_checkpoint_status = "READY_FOR_HUMAN_REVIEW"
        checkpoint = host.checkpoint(
            activation["activation"]["activation_id"]
        )["checkpoint"]

        assert checkpoint["raw_status"] == "READY_FOR_HUMAN_REVIEW"
        assert checkpoint["status"] == "READY_FOR_PAPER"
        assert checkpoint["execution_authority"] is False
        assert checkpoint["live_execution_authorized"] is False

    asyncio.run(scenario())



def test_shadow_activation_identity_ignores_transient_run_id():
    first = activation_payload()
    second = activation_payload()
    second["graen_run_id"] = "55555555-5555-5555-5555-555555555555"
    assert CandidateShadowHost.activation_id(first) == CandidateShadowHost.activation_id(second)
