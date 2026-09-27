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


def test_disabled_credentials_and_false_execution_flags_are_inert(monkeypatch):
    monkeypatch.setenv("ALPACA_API_KEY", "DISABLED")
    monkeypatch.setenv("ALPACA_API_SECRET", "DISABLED")
    monkeypatch.setenv("EXECUTION_ENABLED", "false")
    monkeypatch.setenv("BOT_ARMED", "false")
    monkeypatch.setenv("LIVE_TRADING", "false")
    monkeypatch.setenv("I_ACKNOWLEDGE_LIVE_TRADING", "false")
    assert service._isolation_violations() == []


def test_public_readiness_sanitizes_internal_question_identity():
    payload = service._sanitized_readiness(
        {
            "state": "BLOCKED",
            "cadence": "daily",
            "gpt_would_run_now": False,
            "blocker_count": 2,
            "blockers": [
                {
                    "scope": "research_question",
                    "code": "QUEUE_EVIDENCE_INTEGRITY",
                    "research_question_id": "RQ-INTERNAL",
                    "status": "MONITOR",
                    "category": "OPERATIONAL_DEFECT",
                    "priority_score": 10,
                    "reason_codes": ["CANONICAL_OPERATIONAL_INCIDENTS"],
                }
            ],
            "strategy_question_count": 1,
            "strategy_questions": [
                {"research_question_id": "RQ-PRIVATE-STRATEGY"}
            ],
            "trigger_reference": "2026-09-25",
            "evidence_cutoff": "2026-09-27T02:27:26Z",
        }
    )
    assert payload["state"] == "BLOCKED"
    assert payload["blocker_count"] == 2
    assert payload["gpt_would_run_now"] is False
    assert payload["strategy_question_count"] == 1
    assert payload["blockers"] == [
        {
            "scope": "research_question",
            "code": "QUEUE_EVIDENCE_INTEGRITY",
            "reason_codes": ["CANONICAL_OPERATIONAL_INCIDENTS"],
        }
    ]
    assert "strategy_questions" not in payload
    assert "research_question_id" not in payload["blockers"][0]
    assert payload["model_invoked"] is False
    assert payload["persisted"] is False
