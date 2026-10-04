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
