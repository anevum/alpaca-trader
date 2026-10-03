from __future__ import annotations

from app.config import Settings
from graen.crypto.service_v6 import GraenCryptoV6Runtime


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
