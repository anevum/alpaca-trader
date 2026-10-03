from __future__ import annotations

import pytest
from fastapi import HTTPException

from app.config import Settings
from graen.crypto.service_v6 import GraenCryptoV6Runtime, require_shadow_token


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
