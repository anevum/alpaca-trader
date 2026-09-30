import asyncio

from app.graen import research_executor_service as service


class FakeGateway:
    configured = True

    def __init__(self):
        self.artifacts = []
        self.completions = []
        self.heartbeats = []

    async def snapshot(self):
        return {"problems": []}

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


def test_leadlag_rejection_never_fetches_validation_or_holdout(monkeypatch):
    class StageGateway(FakeGateway):
        async def complete_research_problem(self, **kwargs):
            self.completions.append(kwargs)
            return {"ok": True}

    fetches = []

    async def fake_fetch(symbols, *, start, end, warmup_hours=169):
        fetches.append((start, end))
        return {symbol: [] for symbol in symbols}

    def fake_evaluate(*args, **kwargs):
        return {
            "primary": {
                "trade_count": 0,
                "expectancy_per_trade": 0.0,
            },
            "one_bar_delay": {
                "expectancy_per_trade": 0.0,
            },
        }

    async def scenario():
        runtime = service.GraenResearchExecutor()
        runtime.gateway = StageGateway()
        runtime._fetch_stage = fake_fetch
        runtime.callback_base_url = ""
        runtime.callback_token = ""
        monkeypatch.setattr(service, "evaluate_leadlag_stage", fake_evaluate)
        monkeypatch.setattr(
            service,
            "leadlag_development_gate",
            lambda result: (False, ["development_trade_count_below_20"]),
        )
        result = await runtime._execute_leadlag_r2(
            {
                "problem_id": "11111111-1111-1111-1111-111111111111",
                "linked_iren_job_id": None,
            },
            {"run_id": "22222222-2222-2222-2222-222222222222"},
        )
        assert result["state"] == "CANDIDATE_REJECTED_DEVELOPMENT"
        assert len(fetches) == 1
        assert fetches[0][0] == service.LEADLAG_DEVELOPMENT_START
        assert fetches[0][1] == service.LEADLAG_VALIDATION_START
        assert runtime.gateway.completions[-1]["status"] == "WAITING"
        artifact_types = [row["artifact_type"] for row in runtime.gateway.artifacts]
        assert "RESEARCH_HYPOTHESIS_SPECIFICATION_REVISION" in artifact_types
        assert "CRYPTO_LEADLAG_DEVELOPMENT_RESULT" in artifact_types
        assert "CRYPTO_LEADLAG_VALIDATION_RESULT" not in artifact_types
        assert "CRYPTO_LEADLAG_HOLDOUT_RESULT" not in artifact_types

    asyncio.run(scenario())
