from __future__ import annotations

import asyncio
import pytest
from fastapi import HTTPException

from app.config import Settings
from graen.crypto.service_v6 import GraenCryptoV6Runtime, require_shadow_token
from graen.crypto.btc_slow_momentum_v14_r2f import (
    METHODOLOGY_VERSION as V14_R2F_METHODOLOGY_VERSION,
    candidate_spec as v14_r2f_candidate_spec,
)
from graen.crypto.btc_consensus_trend_v14_r2g import (
    METHODOLOGY_VERSION as V14_R2G_METHODOLOGY_VERSION,
    candidate_spec as v14_r2g_candidate_spec,
)


def test_legacy_v6_loops_are_disabled_by_default(monkeypatch):
    monkeypatch.delenv("GRAEN_LEGACY_V6_RESEARCH_ENABLED", raising=False)
    monkeypatch.delenv("GRAEN_LEGACY_V6_SHADOW_ENABLED", raising=False)

    runtime = GraenCryptoV6Runtime(Settings())

    assert runtime.legacy_v6_research_enabled is False
    assert runtime.legacy_v6_shadow_enabled is False
    status = runtime.status()
    assert status["legacy_v6"] == {
        "research_enabled": False,
        "shadow_enabled": False,
    }
    assert status["execution_authority"] is False
    assert status["crypto_execution_enabled"] is False



def test_shadow_token_falls_back_to_existing_graen_gateway_token(monkeypatch):
    token = "g" * 40
    monkeypatch.delenv("GRAEN_SHADOW_TOKEN", raising=False)
    monkeypatch.setenv("GRAEN_GATEWAY_TOKEN", token)

    require_shadow_token(token)

    with pytest.raises(HTTPException) as exc:
        require_shadow_token("x" * 40)
    assert exc.value.status_code == 401


def test_dedicated_shadow_token_overrides_gateway_token(monkeypatch):
    shadow_token = "s" * 40
    gateway_token = "g" * 40
    monkeypatch.setenv("GRAEN_SHADOW_TOKEN", shadow_token)
    monkeypatch.setenv("GRAEN_GATEWAY_TOKEN", gateway_token)

    require_shadow_token(shadow_token)

    with pytest.raises(HTTPException) as exc:
        require_shadow_token(gateway_token)
    assert exc.value.status_code == 401



def _activation_request(methodology, spec):
    return {
        "problem_id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
        "graen_run_id": "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
        "campaign_id": "comparison-test",
        "epoch_index": 0,
        "generation": 1,
        "candidate_methodology": methodology,
        "candidate_spec": spec.to_dict(),
        "velum_artifact_id": "",
        "evidence_phase": "FORWARD_SHADOW",
    }


def test_r2g_activation_uses_parallel_shadow_without_replacing_r2f(monkeypatch):
    async def scenario():
        runtime = GraenCryptoV6Runtime(Settings())
        r2f_spec = v14_r2f_candidate_spec()
        r2g_spec = v14_r2g_candidate_spec()

        primary_activation = {
            "schema_version": "graen.candidate_shadow.activation.v1",
            "activation_id": "existing-r2f",
            "problem_id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
            "graen_run_id": "r2f-run",
            "campaign_id": "r2f",
            "epoch_index": 0,
            "generation": 1,
            "candidate_methodology": V14_R2F_METHODOLOGY_VERSION,
            "candidate_id": r2f_spec.candidate_id,
            "candidate_spec": r2f_spec.to_dict(),
            "velum_artifact_id": "",
            "evidence_phase": "FORWARD_SHADOW",
            "activated_at": "2026-10-03T23:21:18+00:00",
            "research_only": True,
            "promotion_authorized": False,
            "execution_authority": False,
            "broker_orders_possible": False,
        }
        runtime.candidate_shadow.activate(primary_activation)

        async def emit(*args, **kwargs):
            return True

        async def sync(*args, **kwargs):
            return True

        async def slack(*args, **kwargs):
            return None

        monkeypatch.setattr(runtime, "_emit_candidate_shadow", emit)
        monkeypatch.setattr(runtime, "_sync_candidate_shadow_checkpoint", sync)
        monkeypatch.setattr(runtime, "_slack", slack)

        result = await runtime.activate_candidate_shadow(
            _activation_request(V14_R2G_METHODOLOGY_VERSION, r2g_spec)
        )

        assert result["duplicate"] is False
        assert (
            runtime.candidate_shadow.status()["candidate_id"]
            == r2f_spec.candidate_id
        )
        assert (
            runtime.candidate_shadow.status()["candidate_methodology"]
            == V14_R2F_METHODOLOGY_VERSION
        )
        assert (
            runtime.comparison_shadow.status()["candidate_id"]
            == r2g_spec.candidate_id
        )
        assert (
            runtime.comparison_shadow.status()["candidate_methodology"]
            == V14_R2G_METHODOLOGY_VERSION
        )
        assert runtime.comparison_shadow.execution_authority is False
        assert runtime.comparison_shadow.broker_orders_possible is False

        duplicate = await runtime.activate_candidate_shadow(
            _activation_request(V14_R2G_METHODOLOGY_VERSION, r2g_spec)
        )
        assert duplicate["duplicate"] is True
        assert duplicate["activation"]["candidate_id"] == r2g_spec.candidate_id
        assert runtime.candidate_shadow.status()["candidate_id"] == r2f_spec.candidate_id

    asyncio.run(scenario())



def test_shadow_checkpoint_sync_falls_back_to_trading_ingest_token(monkeypatch):
    async def scenario():
        token = "i" * 40
        monkeypatch.delenv("GRAEN_GATEWAY_TOKEN", raising=False)
        monkeypatch.setenv(
            "GRAEN_GATEWAY_URL",
            "https://foundation.example/v1/graen-gateway",
        )
        monkeypatch.setenv("TRADING_INGEST_TOKEN", token)
        runtime = GraenCryptoV6Runtime(Settings())
        runtime.candidate_shadow.activation = {
            "problem_id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
        }
        observed = {}

        class FakeResponse:
            def raise_for_status(self):
                return None

            def json(self):
                return {"ok": True}

        class FakeClient:
            def __init__(self, *args, **kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return None

            async def post(self, url, *, headers, json):
                observed["url"] = url
                observed["headers"] = headers
                observed["json"] = json
                return FakeResponse()

        monkeypatch.setattr(
            "graen.crypto.service_v6.httpx.AsyncClient",
            FakeClient,
        )
        checkpoint = {
            "activation_id": "activation-r2f",
            "candidate_id": "V14-R2F-BTC-MOM-180D",
            "status": "COLLECTING",
            "candidate_methodology": V14_R2F_METHODOLOGY_VERSION,
            "evidence_phase": "FORWARD_SHADOW",
        }
        synced = await runtime._sync_candidate_shadow_checkpoint(
            checkpoint,
            runtime.candidate_shadow,
        )

        assert synced is True
        assert observed["headers"] == {
            "x-graen-gateway-token": token,
        }
        assert observed["json"]["action"] == "shadow_checkpoint"
        assert observed["json"]["candidate_id"] == checkpoint["candidate_id"]

    asyncio.run(scenario())
