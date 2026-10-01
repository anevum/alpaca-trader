import asyncio

from app.graen import research_executor_service as service
from graen.crypto.activity_shock_v9 import candidate_specs


class FakeGateway:
    configured = True

    def __init__(self):
        self.artifacts = []
        self.completions = []
        self.queued_stages = []
        self.heartbeats = []

    async def record_artifact(self, **kwargs):
        self.artifacts.append(kwargs)
        return {
            "ok": True,
            "artifact": {"artifact_id": f"artifact-{len(self.artifacts)}"},
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

    async def executor_heartbeat(self, **kwargs):
        self.heartbeats.append(kwargs)
        return {"ok": True}


PROBLEM = {
    "problem_id": "11111111-1111-1111-1111-111111111111",
    "linked_iren_job_id": None,
}
RUN = {"run_id": "22222222-2222-2222-2222-222222222222"}


def _runtime():
    runtime = service.GraenResearchExecutor()
    runtime.gateway = FakeGateway()
    runtime.callback_base_url = ""
    runtime.callback_token = ""
    return runtime


def test_v9_development_failure_never_fetches_future_stages(monkeypatch):
    fetches = []

    async def fake_fetch(symbols, *, start, end, warmup_hours=169):
        fetches.append((start, end))
        return {symbol: [] for symbol in symbols}

    monkeypatch.setattr(
        service,
        "verify_v9_stage_corpus",
        lambda *args, **kwargs: {"passed": True},
    )
    monkeypatch.setattr(
        service,
        "evaluate_v9_development",
        lambda *args, **kwargs: {
            "stage": "DEVELOPMENT",
            "candidate_count": 6,
            "results": {},
            "survivors": [],
            "selected_candidate_id": None,
            "selected_candidate_spec": None,
        },
    )

    async def scenario():
        runtime = _runtime()
        runtime._fetch_stage = fake_fetch
        result = await runtime._execute_activity_shock_v9(
            {
                **PROBLEM,
                "metadata": {
                    "research_stage": service.V9_DEVELOPMENT_STAGE,
                    "v9_epoch_index": 0,
                },
            },
            RUN,
        )
        epoch = service._v9_epoch_contract(0)
        assert fetches == [(epoch["development_start"], epoch["validation_start"])]
        assert result["state"] == "V9_EPOCH_REJECTED_DEVELOPMENT"
        queued = runtime.gateway.queued_stages[-1]
        assert queued["stage"] == service.V9_DEVELOPMENT_STAGE
        assert queued["metadata"]["v9_epoch_index"] == 1

    asyncio.run(scenario())


def test_v9_validation_failure_burns_epoch(monkeypatch):
    fetches = []
    candidate = candidate_specs()[0].to_dict()

    async def fake_fetch(symbols, *, start, end, warmup_hours=169):
        fetches.append((start, end))
        return {symbol: [] for symbol in symbols}

    monkeypatch.setattr(
        service,
        "verify_v9_stage_corpus",
        lambda *args, **kwargs: {"passed": True},
    )
    monkeypatch.setattr(
        service,
        "evaluate_v9_validation",
        lambda *args, **kwargs: {
            "stage": "VALIDATION",
            "candidate_id": candidate["candidate_id"],
            "passed": False,
            "reasons": ["validation_dependence_p_above_0.05"],
            "result": {},
        },
    )

    async def scenario():
        runtime = _runtime()
        runtime._fetch_stage = fake_fetch
        result = await runtime._execute_activity_shock_v9(
            {
                **PROBLEM,
                "metadata": {
                    "research_stage": service.V9_VALIDATION_STAGE,
                    "v9_epoch_index": 0,
                    "v9_candidate_spec": candidate,
                },
            },
            RUN,
        )
        epoch = service._v9_epoch_contract(0)
        assert fetches == [(epoch["validation_start"], epoch["holdout_start"])]
        assert result["state"] == "V9_CANDIDATE_REJECTED_VALIDATION"
        queued = runtime.gateway.queued_stages[-1]
        assert queued["stage"] == service.V9_DEVELOPMENT_STAGE
        assert queued["metadata"]["v9_epoch_index"] == 1

    asyncio.run(scenario())


def test_v9_holdout_pass_queues_velum(monkeypatch):
    candidate = candidate_specs()[0].to_dict()

    async def fake_fetch(symbols, *, start, end, warmup_hours=169):
        return {symbol: [] for symbol in symbols}

    monkeypatch.setattr(
        service,
        "verify_v9_stage_corpus",
        lambda *args, **kwargs: {"passed": True},
    )
    monkeypatch.setattr(
        service,
        "evaluate_v9_holdout",
        lambda *args, **kwargs: {
            "stage": "HOLDOUT",
            "candidate_id": candidate["candidate_id"],
            "passed": True,
            "reasons": [],
            "scenarios": {},
        },
    )

    async def scenario():
        runtime = _runtime()
        runtime._fetch_stage = fake_fetch
        result = await runtime._execute_activity_shock_v9(
            {
                **PROBLEM,
                "metadata": {
                    "research_stage": service.V9_HOLDOUT_STAGE,
                    "v9_epoch_index": 0,
                    "v9_candidate_spec": candidate,
                },
            },
            RUN,
        )
        assert result["state"] == "V9_CANDIDATE_READY_FOR_VELUM"
        queued = runtime.gateway.queued_stages[-1]
        assert queued["stage"] == service.V9_VELUM_STAGE
        assert queued["metadata"]["v9_candidate_spec"]["candidate_id"] == candidate["candidate_id"]
        assert runtime.gateway.completions[-1]["status"] == "WAITING"

    asyncio.run(scenario())
