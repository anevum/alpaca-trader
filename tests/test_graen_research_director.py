from __future__ import annotations

import asyncio

from app.graen import research_executor_service as service


PROBLEM_ID = "11111111-1111-1111-1111-111111111111"
RUN_ID = "22222222-2222-2222-2222-222222222222"


class FakeGateway:
    configured = True

    def __init__(self, snapshot=None):
        self.snapshot_value = snapshot or {"problems": [], "runs": []}
        self.queued = []
        self.artifacts = []
        self.completions = []
        self.heartbeats = []

    async def snapshot(self):
        return self.snapshot_value

    async def queue_research_stage(self, **kwargs):
        self.queued.append(kwargs)
        return {"ok": True, "problem": {"problem_id": kwargs["problem_id"]}}

    async def record_artifact(self, **kwargs):
        self.artifacts.append(kwargs)
        return {
            "ok": True,
            "artifact": {"artifact_id": f"artifact-{len(self.artifacts)}"},
        }

    async def complete_research_problem(self, **kwargs):
        self.completions.append(kwargs)
        return {"ok": True}

    async def executor_heartbeat(self, **kwargs):
        self.heartbeats.append(kwargs)
        return {"ok": True}


class FakeDirector:
    configured = True

    def __init__(self):
        self.calls = []

    async def research(self, **kwargs):
        self.calls.append(kwargs)
        return {
            "ok": True,
            "decision": {
                "schema_version": "graen.research-director.v1",
                "action": "REPLICATE_EXTERNAL_METHOD",
                "research_question": "Replicate a credible external method.",
                "rationale": "The existing families are exhausted.",
                "evidence_quality": "HIGH",
                "selected_method": {
                    "name": "External Replication",
                    "category": "machine_learning",
                    "alpha_or_execution": "ALPHA",
                    "source_title": "Paper",
                    "source_url": "https://example.org/paper",
                    "code_repository_url": "",
                    "original_market": "equities",
                    "original_horizon": "daily",
                    "original_data": "published dataset",
                    "original_methodology": "temporal out-of-sample",
                    "btc_transfer_rationale": "testable",
                    "required_data": ["BTC data"],
                    "reproduction_plan": ["original replication"],
                    "btc_transfer_plan": ["BTC transfer"],
                    "cost_model_requirements": ["realistic costs"],
                    "falsification_conditions": ["replication fails"],
                },
                "negative_evidence": [],
                "exhausted_mechanisms": ["V13"],
                "implementation_class": "TRUSTED_COMPILER_EXTENSION_REQUIRED",
                "trusted_compiler_mechanism": "",
                "next_action": "REQUEST_COMPILER_EXTENSION",
                "research_only": True,
                "execution_authority": False,
                "live_execution_authorized": False,
            },
            "retrieved_sources": [{"url": "https://example.org/paper"}],
            "web_search_calls": 3,
            "response_id": "resp-test",
            "usage": {"invoked": True, "total_tokens": 1000},
        }


def _terminal_snapshot():
    problem = {
        "problem_id": PROBLEM_ID,
        "status": "WAITING",
        "domain": service.PROBLEM_DOMAIN,
        "title": "BTC research",
        "statement": "Find a defensible BTC edge.",
        "constraints": {},
        "success_criteria": {},
        "metadata": {"v13_campaign_id": service.V13_CAMPAIGN_ID},
    }
    run = {
        "run_id": RUN_ID,
        "problem_id": PROBLEM_ID,
        "status": "WAITING",
        "methodology_version": service.V13_METHODOLOGY_VERSION,
        "result_summary": {
            "campaign_id": service.V13_CAMPAIGN_ID,
            "state": "V13_NO_DEVELOPMENT_SURVIVOR",
            "decision": "V13_HYPOTHESES_FALSIFIED",
            "next_action": "DESIGN_NEXT_BTC_HYPOTHESIS",
            "candidate_diagnostics": [],
        },
    }
    return {"problems": [problem], "runs": [run]}


def _runtime(snapshot=None):
    runtime = service.GraenResearchExecutor()
    runtime.gateway = FakeGateway(snapshot)
    runtime.research_director = FakeDirector()
    runtime.callback_base_url = ""
    runtime.callback_token = ""
    return runtime


def test_v13_research_director_transition_is_disabled_without_explicit_autorun():
    async def scenario():
        runtime = _runtime(_terminal_snapshot())
        runtime.research_director_autorun = False
        result = await runtime._recover_v13_into_research_director(
            _terminal_snapshot()
        )
        assert result is None
        assert runtime.gateway.queued == []

    asyncio.run(scenario())


def test_v13_exhaustion_queues_director_when_explicitly_enabled():
    async def scenario():
        runtime = _runtime(_terminal_snapshot())
        runtime.research_director_autorun = True
        result = await runtime._recover_v13_into_research_director(
            _terminal_snapshot()
        )
        assert result["next_research_stage"] == service.RESEARCH_DIRECTOR_STAGE
        assert result["execution_authority"] is False
        assert runtime.gateway.queued[-1]["stage"] == service.RESEARCH_DIRECTOR_STAGE

    asyncio.run(scenario())


def test_director_decision_is_persisted_without_execution_authority():
    async def scenario():
        snapshot = _terminal_snapshot()
        runtime = _runtime(snapshot)
        problem = {
            **snapshot["problems"][0],
            "status": "RUNNING",
            "metadata": {
                **snapshot["problems"][0]["metadata"],
                "research_stage": service.RESEARCH_DIRECTOR_STAGE,
            },
        }
        result = await runtime._execute_research_director(
            problem,
            {"run_id": "33333333-3333-3333-3333-333333333333"},
        )
        assert result["state"] == "RESEARCH_DIRECTOR_COMPILER_EXTENSION_REQUIRED"
        assert result["next_action"] == "REQUEST_COMPILER_EXTENSION"
        assert result["execution_authority"] is False
        assert result["broker_orders_possible"] is False
        assert runtime.gateway.artifacts[-1]["artifact_type"] == (
            "CRYPTO_RESEARCH_DIRECTOR_DECISION"
        )
        content = runtime.gateway.artifacts[-1]["content"]
        assert content["live_execution_authorized"] is False
        assert runtime.gateway.completions[-1]["model_usage"]["invoked"] is True

    asyncio.run(scenario())
