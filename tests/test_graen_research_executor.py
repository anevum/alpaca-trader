import asyncio

from app.graen import research_executor_service as service


class FakeGateway:
    configured = True

    def __init__(self):
        self.artifacts = []
        self.completions = []
        self.heartbeats = []
        self.queued_stages = []

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

    async def queue_research_stage(self, **kwargs):
        self.queued_stages.append(kwargs)
        return {
            "ok": True,
            "problem": {
                "problem_id": kwargs["problem_id"],
                "status": "WAITING",
                "metadata": {
                    "research_stage": kwargs["stage"],
                    **(kwargs.get("metadata") or {}),
                },
            },
        }


def test_v7_development_rejection_never_fetches_validation_or_holdout(monkeypatch):
    fetches = []

    async def fake_fetch(symbols, *, start, end, warmup_hours=169):
        fetches.append((start, end, warmup_hours))
        return {symbol: [] for symbol in symbols}

    def fake_development(**kwargs):
        return {
            "stage": "DEVELOPMENT",
            "opened": True,
            "results": {},
            "gates": [],
            "survivors": [],
        }

    async def scenario():
        runtime = service.GraenResearchExecutor()
        runtime.gateway = FakeGateway()
        runtime._fetch_stage = fake_fetch
        runtime.callback_base_url = ""
        runtime.callback_token = ""
        monkeypatch.setattr(service, "evaluate_v7_development", fake_development)
        result = await runtime._execute_v7_staged(
            {
                "problem_id": "11111111-1111-1111-1111-111111111111",
                "linked_iren_job_id": None,
            },
            {"run_id": "22222222-2222-2222-2222-222222222222"},
        )
        assert result["state"] == "RESEARCH_BATCH_COMPLETE"
        assert result["status"] == "NO_DEVELOPMENT_SURVIVOR"
        assert result["decision"] == "CONTINUE_RESEARCH"
        assert len(fetches) == 1
        assert fetches[0][0] == service.DEVELOPMENT_START
        assert fetches[0][1] == service.VALIDATION_START
        assert runtime.gateway.completions[-1]["status"] == "WAITING"
        assert runtime.gateway.queued_stages[-1]["stage"] == service.AUTONOMOUS_DEVELOPMENT_STAGE
        assert runtime.gateway.queued_stages[-1]["metadata"]["campaign_epoch"] == 0
        assert runtime.gateway.queued_stages[-1]["metadata"]["campaign_generation"] == 1
        artifact_types = [row["artifact_type"] for row in runtime.gateway.artifacts]
        assert "CRYPTO_V7_BATCH_SPECIFICATION" in artifact_types
        assert "CRYPTO_V7_DEVELOPMENT_RESULT" in artifact_types
        assert "CRYPTO_V7_VALIDATION_RESULT" not in artifact_types
        assert "CRYPTO_V7_HOLDOUT_RESULT" not in artifact_types

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
        assert runtime.gateway.queued_stages[-1]["stage"] == service.AUTONOMOUS_DEVELOPMENT_STAGE
        assert runtime.gateway.queued_stages[-1]["metadata"]["campaign_epoch"] == 0
        assert runtime.gateway.queued_stages[-1]["metadata"]["campaign_generation"] == 1
        artifact_types = [row["artifact_type"] for row in runtime.gateway.artifacts]
        assert "RESEARCH_HYPOTHESIS_SPECIFICATION_REVISION" in artifact_types
        assert "CRYPTO_LEADLAG_DEVELOPMENT_RESULT" in artifact_types
        assert "CRYPTO_LEADLAG_VALIDATION_RESULT" not in artifact_types
        assert "CRYPTO_LEADLAG_HOLDOUT_RESULT" not in artifact_types

    asyncio.run(scenario())


def test_process_once_runs_bounded_research_promotion_without_claiming_old_stage():
    problem_id = "44444444-4444-4444-4444-444444444444"

    class PromotionGateway(FakeGateway):
        async def snapshot(self):
            return {
                "problems": [{
                    "problem_id": problem_id,
                    "status": "WAITING",
                    "domain": service.PROBLEM_DOMAIN,
                    "metadata": {
                        "research_stage": "RESEARCH_IMPLEMENTATION_REQUIRED",
                    },
                }],
                "runs": [],
            }

        async def claim_research_problem(self, **kwargs):
            return {"problem": None, "run": None}

    class Promotion:
        def __init__(self):
            self.calls = []

        async def tick(self, candidate_problem_id):
            self.calls.append(candidate_problem_id)
            return {
                "phase": "FREEZE",
                "blocked_reason": "complete_frozen_prespec_required",
            }

    async def scenario():
        runtime = service.GraenResearchExecutor()
        runtime.gateway = PromotionGateway()
        promotion = Promotion()
        runtime.research_promotion = promotion
        result = await runtime.process_once()
        assert promotion.calls == [problem_id]
        assert result["status"] == "IDLE"
        assert result["claimed"] is False
        assert result["research_promotion"] == [{
            "problem_id": problem_id,
            "phase": "FREEZE",
            "blocked_reason": "complete_frozen_prespec_required",
        }]

    asyncio.run(scenario())
