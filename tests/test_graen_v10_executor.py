import asyncio

from app.graen import research_executor_service as service
from graen.crypto.trend_pullback_v10 import candidate_specs


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


def test_v10_development_failure_only_reads_development_and_advances_epoch(monkeypatch):
    fetches = []

    async def fake_fetch(symbols, *, start, end, warmup_hours=169):
        fetches.append((start, end))
        return {symbol: [] for symbol in symbols}

    monkeypatch.setattr(
        service,
        "verify_v10_stage_corpus",
        lambda *args, **kwargs: {"passed": True},
    )
    monkeypatch.setattr(
        service,
        "evaluate_v10_development",
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
        result = await runtime._execute_trend_pullback_v10(
            {
                **PROBLEM,
                "metadata": {
                    "research_stage": service.V10_DEVELOPMENT_STAGE,
                    "v10_epoch_index": 0,
                },
            },
            RUN,
        )
        epoch = service._v10_epoch_contract(0)
        assert fetches == [(epoch["development_start"], epoch["validation_start"])]
        assert result["state"] == "V10_EPOCH_REJECTED_DEVELOPMENT"
        queued = runtime.gateway.queued_stages[-1]
        assert queued["stage"] == service.V10_DEVELOPMENT_STAGE
        assert queued["metadata"]["v10_epoch_index"] == 1
        prespec = runtime.gateway.artifacts[0]["content"]
        assert prespec["development_previously_inspected"] is True
        assert prespec["validation_previously_inspected"] is False
        assert prespec["holdout_previously_inspected"] is False

    asyncio.run(scenario())


def test_v10_validation_failure_burns_epoch(monkeypatch):
    fetches = []
    candidate = candidate_specs()[0].to_dict()

    async def fake_fetch(symbols, *, start, end, warmup_hours=169):
        fetches.append((start, end))
        return {symbol: [] for symbol in symbols}

    monkeypatch.setattr(
        service,
        "verify_v10_stage_corpus",
        lambda *args, **kwargs: {"passed": True},
    )
    monkeypatch.setattr(
        service,
        "evaluate_v10_validation",
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
        result = await runtime._execute_trend_pullback_v10(
            {
                **PROBLEM,
                "metadata": {
                    "research_stage": service.V10_VALIDATION_STAGE,
                    "v10_epoch_index": 0,
                    "v10_candidate_spec": candidate,
                },
            },
            RUN,
        )
        epoch = service._v10_epoch_contract(0)
        assert fetches == [(epoch["validation_start"], epoch["holdout_start"])]
        assert result["state"] == "V10_CANDIDATE_REJECTED_VALIDATION"
        queued = runtime.gateway.queued_stages[-1]
        assert queued["stage"] == service.V10_DEVELOPMENT_STAGE
        assert queued["metadata"]["v10_epoch_index"] == 1

    asyncio.run(scenario())


def test_v10_holdout_pass_queues_velum(monkeypatch):
    candidate = candidate_specs()[0].to_dict()

    async def fake_fetch(symbols, *, start, end, warmup_hours=169):
        return {symbol: [] for symbol in symbols}

    monkeypatch.setattr(
        service,
        "verify_v10_stage_corpus",
        lambda *args, **kwargs: {"passed": True},
    )
    monkeypatch.setattr(
        service,
        "evaluate_v10_holdout",
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
        result = await runtime._execute_trend_pullback_v10(
            {
                **PROBLEM,
                "metadata": {
                    "research_stage": service.V10_HOLDOUT_STAGE,
                    "v10_epoch_index": 0,
                    "v10_candidate_spec": candidate,
                },
            },
            RUN,
        )
        assert result["state"] == "V10_CANDIDATE_READY_FOR_VELUM"
        queued = runtime.gateway.queued_stages[-1]
        assert queued["stage"] == service.V10_VELUM_STAGE
        assert queued["metadata"]["v10_candidate_spec"]["candidate_id"] == candidate["candidate_id"]
        assert runtime.gateway.completions[-1]["status"] == "WAITING"

    asyncio.run(scenario())


def test_v10_velum_pass_activates_forward_shadow_and_stays_waiting(monkeypatch):
    candidate = candidate_specs()[0].to_dict()
    activations = []

    async def fake_replay(**kwargs):
        return {
            "candidate_id": candidate["candidate_id"],
            "engineering_gate": {"passed": True, "reasons": []},
            "research_only": True,
            "execution_authority": False,
        }

    async def fake_activate(**kwargs):
        activations.append(kwargs)
        return {
            "activation": {
                "activation_id": "shadow-v10-001",
                "candidate_id": candidate["candidate_id"],
            },
            "duplicate": False,
            "execution_authority": False,
            "broker_orders_possible": False,
            "promotion_authorized": False,
        }

    async def scenario():
        runtime = _runtime()
        monkeypatch.setattr(runtime, "_replay_in_velum", fake_replay)
        monkeypatch.setattr(runtime, "_activate_forward_shadow", fake_activate)

        result = await runtime._execute_trend_pullback_v10(
            {
                **PROBLEM,
                "metadata": {
                    "research_stage": service.V10_VELUM_STAGE,
                    "v10_epoch_index": 0,
                    "v10_candidate_spec": candidate,
                    "v10_holdout_result": {"passed": True},
                    "v10_holdout_artifact_id": "holdout-v10",
                },
            },
            RUN,
        )

        assert result["state"] == "FORWARD_SHADOW_RUNNING"
        assert result["status"] == "SHADOW_ACTIVE"
        assert result["decision"] == "COLLECT_FORWARD_EVIDENCE"
        assert runtime.gateway.completions[-1]["status"] == "WAITING"
        assert activations[0]["candidate_methodology"] == service.V10_METHODOLOGY_VERSION
        assert result["execution_authority"] is False
        assert result["broker_orders_possible"] is False
        assert result["promotion_authorized"] is False

    asyncio.run(scenario())
