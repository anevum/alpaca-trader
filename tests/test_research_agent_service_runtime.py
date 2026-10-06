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
            "state": "WAITING",
            "cadence": "daily",
            "gpt_would_run_now": False,
            "blocker_count": 0,
            "blockers": [],
            "limitation_count": 1,
            "limitations": [
                {
                    "scope": "report",
                    "code": "KNOWN_EVIDENCE_LIMITATION",
                    "trigger_reference": "2026-09-25",
                    "reason_codes": ["UNRECONSTRUCTABLE_EVIDENCE"],
                }
            ],
            "monitor_count": 1,
            "monitors": [
                {
                    "scope": "research_question",
                    "code": "NONBLOCKING_MONITOR",
                    "research_question_id": "RQ-INTERNAL",
                    "status": "MONITOR",
                    "category": "OPERATIONAL_DEFECT",
                    "priority_score": 10,
                    "reason_codes": ["CANONICAL_OPERATIONAL_INCIDENTS"],
                }
            ],
            "strategy_question_count": 1,
            "ready_strategy_question_count": 0,
            "waiting_strategy_question_count": 1,
            "strategy_questions": [
                {
                    "research_question_id": "RQ-PRIVATE-STRATEGY",
                    "semantic_readiness": "WAITING",
                    "missing_requirements": ["MULTIPLE_INDEPENDENT_SESSIONS"],
                }
            ],
            "trigger_reference": "2026-09-25",
            "evidence_cutoff": "2026-09-27T02:27:26Z",
        }
    )
    assert payload["state"] == "WAITING"
    assert payload["blocker_count"] == 0
    assert payload["limitation_count"] == 1
    assert payload["monitor_count"] == 1
    assert payload["gpt_would_run_now"] is False
    assert payload["strategy_question_count"] == 1
    assert payload["ready_strategy_question_count"] == 0
    assert payload["waiting_strategy_question_count"] == 1
    assert payload["waiting_requirements"] == ["MULTIPLE_INDEPENDENT_SESSIONS"]
    assert payload["limitations"] == [
        {
            "scope": "report",
            "code": "KNOWN_EVIDENCE_LIMITATION",
            "reason_codes": ["UNRECONSTRUCTABLE_EVIDENCE"],
        }
    ]
    assert payload["monitors"] == [
        {
            "scope": "research_question",
            "code": "NONBLOCKING_MONITOR",
            "reason_codes": ["CANONICAL_OPERATIONAL_INCIDENTS"],
        }
    ]
    assert "strategy_questions" not in payload
    assert "research_question_id" not in payload["monitors"][0]
    assert payload["model_invoked"] is False
    assert payload["persisted"] is False


def test_research_gateway_sends_native_write_auth_alias():
    from app.research_agent.gateway import ResearchGateway

    token = "x" * 64
    headers = ResearchGateway(
        "http://127.0.0.1:8102/v1/research-agent-gateway",
        token,
    )._headers()

    assert headers["x-anevum-ingest-token"] == token
    assert headers["x-rhen-research-gateway-token"] == token
