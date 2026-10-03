from __future__ import annotations

import asyncio
from types import SimpleNamespace

from app import crypto_promotion


def _settings(**overrides):
    values = {
        "foundation_ingest_url": "https://foundation.test/v1/events",
        "foundation_ingest_token": "secret",
        "crypto_strategy_family": "replication-test",
        "crypto_strategy_version_id": "CRYPTO-TEST-001",
        "crypto_model_version": "model-v1",
        "crypto_calibration_version": "cal-v1",
        "crypto_regime_version": "regime-v1",
        "crypto_execution_adapter_version": "alpaca-v1",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


class _Response:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


def test_crypto_promotion_bridge_is_fail_closed_when_unconfigured():
    state = asyncio.run(
        crypto_promotion.fetch_crypto_promotion_status(
            _settings(foundation_ingest_url="", foundation_ingest_token="")
        )
    )
    assert state["promotion_ready"] is False
    assert state["status"] == "GATED"
    assert state["live_execution_authorized"] is False


def test_crypto_promotion_bridge_accepts_only_exact_execution_contract(monkeypatch):
    settings = _settings()
    contract = crypto_promotion.crypto_execution_contract(settings)

    class _Client:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def get(self, url, **kwargs):
            assert url == "https://foundation.test/v1/crypto-promotion-status"
            assert kwargs["params"] == contract
            return _Response({
                "promotion_ready": True,
                "reason": "matching_protected_research_artifact",
                "execution_contract": contract,
                "artifact": {"artifact_id": "artifact-1"},
                "live_execution_authorized": False,
            })

    monkeypatch.setattr(crypto_promotion.httpx, "AsyncClient", _Client)
    state = asyncio.run(crypto_promotion.fetch_crypto_promotion_status(settings))

    assert state["promotion_ready"] is True
    assert state["status"] == "PROMOTION_READY"
    assert state["artifact"]["artifact_id"] == "artifact-1"
    assert state["execution_authority"] is False
    assert state["live_execution_authorized"] is False


def test_crypto_promotion_bridge_rejects_mismatched_strategy(monkeypatch):
    settings = _settings()
    contract = crypto_promotion.crypto_execution_contract(settings)
    wrong = dict(contract)
    wrong["strategy_version_id"] = "OTHER-STRATEGY"

    class _Client:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def get(self, url, **kwargs):
            return _Response({
                "promotion_ready": True,
                "execution_contract": wrong,
            })

    monkeypatch.setattr(crypto_promotion.httpx, "AsyncClient", _Client)
    state = asyncio.run(crypto_promotion.fetch_crypto_promotion_status(settings))

    assert state["promotion_ready"] is False
    assert state["reason"] == "execution_contract_mismatch"
    assert state["live_execution_authorized"] is False
