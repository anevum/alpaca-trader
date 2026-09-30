import asyncio

from app.graen import research_executor_service as service


class FakeGateway:
    configured = True

    def __init__(self):
        self.artifacts = []
        self.completions = []
        self.heartbeats = []

    async def claim_research_problem(self, **kwargs):
        return {
            "problem": {
                "problem_id": "11111111-1111-1111-1111-111111111111",
                "problem_key": "crypto-objective",
                "domain": "CRYPTO_STRATEGY_RESEARCH",
                "linked_iren_job_id": None,
            },
            "run": {
                "run_id": "22222222-2222-2222-2222-222222222222",
            },
        }

    async def executor_heartbeat(self, **kwargs):
        self.heartbeats.append(kwargs)
        return {"ok": True}

    async def record_artifact(self, **kwargs):
        self.artifacts.append(kwargs)
        return {
            "ok": True,
            "artifact": {"artifact_id": "33333333-3333-3333-3333-333333333333"},
            "content_hash": "abc123",
        }

    async def complete_research_problem(self, **kwargs):
        self.completions.append(kwargs)
        return {"ok": True}


def test_executor_persists_batch_and_waits_when_no_survivor(monkeypatch):
    async def fake_fetch():
        return {}

    def fake_research(**kwargs):
        return {
            "methodology_version": "graen-crypto-native-v7",
            "research_batch_id": "test-batch",
            "status": "NO_VALIDATION_SURVIVOR",
            "decision": "CONTINUE_RESEARCH",
            "candidate_count": 13,
            "candidate_family_count": 3,
            "validation_survivors": [],
            "selected_candidate": None,
            "holdout": {"opened": False},
            "next_action": "DESIGN_NEXT_FROZEN_RESEARCH_BATCH",
            "model_invoked": False,
            "execution_authority": False,
            "production_state_changed": False,
        }

    async def scenario():
        runtime = service.GraenResearchExecutor()
        runtime.gateway = FakeGateway()
        runtime._fetch_corpus = fake_fetch
        runtime.callback_base_url = ""
        runtime.callback_token = ""
        monkeypatch.setattr(service, "run_crypto_research_v7", fake_research)
        result = await runtime.process_once()
        assert result["state"] == "RESEARCH_BATCH_COMPLETE"
        assert result["decision"] == "CONTINUE_RESEARCH"
        assert result["model_invoked"] is False
        assert runtime.gateway.artifacts[0]["artifact_type"] == "CRYPTO_RESEARCH_BATCH_RESULT"
        assert runtime.gateway.completions[0]["status"] == "WAITING"
        assert runtime.active_problem_id is None

    asyncio.run(scenario())


def test_executor_health_never_has_trading_authority():
    runtime = service.GraenResearchExecutor()
    runtime.task = type("Task", (), {"done": lambda self: False})()
    runtime.gateway = type("Gateway", (), {"configured": True})()
    state = runtime.health()
    assert state["system"] == "GRAEN"
    assert state["execution_authority"] is False
    assert state["broker_orders_possible"] is False
    assert state["risk_or_sizing_authority"] is False
    assert state["production_promotion_authority"] is False
    assert state["crypto_execution_enabled"] is False
    assert state["model_execution_enabled"] is False
