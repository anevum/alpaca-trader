import pytest

from app.research_agent import service


def test_isolated_runtime_has_no_broker_authority(monkeypatch):
    for name in service.FORBIDDEN_RUNTIME_VARIABLES:
        monkeypatch.delenv(name, raising=False)
    assert service._isolation_violations() == []


def test_broker_credential_presence_is_a_health_violation(monkeypatch):
    monkeypatch.setenv("ALPACA_API_KEY", "synthetic-never-a-real-key")
    assert service._isolation_violations() == ["ALPACA_API_KEY"]
    state = service._health()
    assert state["ok"] is False
    assert state["broker_credentials_present"] is True


def test_health_declares_no_control_plane_authority(monkeypatch):
    for name in service.FORBIDDEN_RUNTIME_VARIABLES:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("RHEN_RESEARCH_AGENT_ENABLED", "true")
    monkeypatch.setenv("RHEN_RESEARCH_GATEWAY_URL", "https://example.invalid/research")
    monkeypatch.setenv("RHEN_RESEARCH_GATEWAY_TOKEN", "x" * 64)
    monkeypatch.setenv("RHEN_RESEARCH_SOURCE_COMMIT", "a" * 40)
    state = service._health()
    assert state["ok"] is True
    assert state["scheduler_configured"] is False
    assert state["autorun"] is False
    assert set(state["authority"].values()) == {False}
