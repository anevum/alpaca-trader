import asyncio

from app.graen.service import GraenRuntime


class FakeGateway:
    configured = True

    def __init__(self):
        self.heartbeats = []
        self.artifacts = []
        self.completions = []

    async def claim_problem(self, worker_id):
        return {
            "problem": {
                "problem_id": "11111111-1111-1111-1111-111111111111",
                "problem_key": "p1",
                "title": "Test problem",
                "statement": "Find a falsifiable solution.",
                "domain": "TEST",
                "priority": 90,
                "constraints": {"production_authority": False},
                "success_criteria": {"artifact": True},
                "source": "IREN",
                "requested_by": "test",
                "linked_iren_job_id": None,
            },
            "run": {
                "run_id": "22222222-2222-2222-2222-222222222222",
            },
        }

    async def heartbeat(self, worker_id, *, active_problem_id, last_error):
        self.heartbeats.append((worker_id, active_problem_id, last_error))
        return {"ok": True}

    async def record_artifact(self, **payload):
        self.artifacts.append(payload)
        return {
            "ok": True,
            "artifact": {"artifact_id": "33333333-3333-3333-3333-333333333333"},
            "content_hash": "abc",
        }

    async def complete_problem(self, **payload):
        self.completions.append(payload)
        return {"ok": True}


def test_graen_claim_normalizes_problem_into_canonical_artifact():
    async def scenario():
        runtime = GraenRuntime()
        runtime.gateway = FakeGateway()
        result = await runtime.process_once()
        assert result["status"] == "WAITING"
        assert result["state"] == "READY_FOR_RESEARCH_EXECUTOR"
        assert result["model_invoked"] is False
        assert result["execution_authority"] is False
        assert runtime.gateway.artifacts[0]["artifact_type"] == "RESEARCH_PROBLEM_SPECIFICATION"
        spec = runtime.gateway.artifacts[0]["content"]
        assert spec["schema_version"] == "graen.problem_specification.v1"
        assert spec["methodology"]["research_stages"] == ["DEVELOPMENT", "VALIDATION", "HOLDOUT"]
        assert spec["methodology"]["production_authority"] is False
        assert runtime.gateway.completions[0]["status"] == "WAITING"
        assert runtime.active_problem_id is None

    asyncio.run(scenario())


def test_graen_health_has_no_execution_authority():
    runtime = GraenRuntime()
    runtime.gateway = FakeGateway()
    runtime.task = type("Task", (), {"done": lambda self: False})()
    state = runtime.health()
    assert state["system"] == "GRAEN"
    assert state["execution_authority"] is False
    assert state["broker_orders_possible"] is False
    assert state["risk_or_sizing_authority"] is False
    assert state["production_promotion_authority"] is False
    assert state["model_execution_enabled"] is False
