import asyncio

from app.graen import research_executor_service as service
from graen.crypto.autonomous_campaign import (
    MAX_GENERATIONS_PER_EPOCH,
    adaptive_candidate_specs,
    epoch_contract,
    prespecification,
)


class CampaignGateway:
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
            "artifact": {
                "artifact_id": f"artifact-{len(self.artifacts)}",
            },
            "content_hash": f"hash-{len(self.artifacts)}",
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


def test_generations_are_materially_distinct_and_multi_family():
    first = adaptive_candidate_specs(1)
    second = adaptive_candidate_specs(2)

    assert [row.candidate_id for row in first] != [
        row.candidate_id for row in second
    ]
    assert [row.to_dict() for row in first] != [
        row.to_dict() for row in second
    ]
    assert len({row.family for row in first}) >= 3
    assert MAX_GENERATIONS_PER_EPOCH >= 4


def test_prespec_freezes_sequential_data_access_contract():
    spec = prespecification(0, 1)
    contract = epoch_contract(0)

    assert spec["stage_order"] == [
        "DEVELOPMENT",
        "VALIDATION",
        "HOLDOUT",
    ]
    assert (
        spec["data_access_contract"]["validation_unseen_until_candidate_frozen"]
        is True
    )
    assert (
        spec["data_access_contract"]["holdout_unseen_until_validation_pass"]
        is True
    )
    assert (
        spec["data_access_contract"]["failed_validation_or_holdout_burns_epoch"]
        is True
    )
    assert contract["development_start"] < contract["validation_start"]
    assert contract["validation_start"] < contract["holdout_start"]
    assert contract["holdout_start"] < contract["holdout_end"]
    assert spec["authority"]["execution_authority"] is False
    assert spec["authority"]["model_execution_enabled"] is False


def test_development_rejection_queues_new_generation_without_future_fetch(
    monkeypatch,
):
    fetches = []

    async def fake_fetch(symbols, *, start, end, warmup_hours=169):
        fetches.append((start, end))
        return {symbol: [] for symbol in symbols}

    def fake_development(*args, **kwargs):
        return {
            "stage": "DEVELOPMENT",
            "opened": True,
            "generation": 1,
            "candidate_count": 5,
            "results": {},
            "gates": [],
            "survivors": [],
            "selected_candidate_id": None,
            "selected_candidate_spec": None,
            "selected_development_result": None,
        }

    async def scenario():
        runtime = service.GraenResearchExecutor()
        runtime.gateway = CampaignGateway()
        runtime._fetch_stage = fake_fetch
        runtime.callback_base_url = ""
        runtime.callback_token = ""
        monkeypatch.setattr(
            service,
            "evaluate_autonomous_development",
            fake_development,
        )

        result = await runtime._execute_autonomous_campaign(
            {
                "problem_id": "11111111-1111-1111-1111-111111111111",
                "linked_iren_job_id": None,
                "metadata": {
                    "research_stage": service.AUTONOMOUS_DEVELOPMENT_STAGE,
                    "campaign_epoch": 0,
                    "campaign_generation": 1,
                },
            },
            {
                "run_id": "22222222-2222-2222-2222-222222222222",
            },
        )

        contract = epoch_contract(0)
        assert len(fetches) == 1
        assert fetches[0] == (
            contract["development_start"],
            contract["validation_start"],
        )
        assert result["state"] == "AUTONOMOUS_GENERATION_REJECTED"
        assert (
            runtime.gateway.queued_stages[-1]["stage"]
            == service.AUTONOMOUS_DEVELOPMENT_STAGE
        )
        assert (
            runtime.gateway.queued_stages[-1]["metadata"][
                "campaign_generation"
            ]
            == 2
        )
        assert runtime.gateway.completions[-1]["status"] == "WAITING"

    asyncio.run(scenario())


def test_validation_failure_burns_epoch_instead_of_reusing_it(monkeypatch):
    fetches = []

    async def fake_fetch(symbols, *, start, end, warmup_hours=169):
        fetches.append((start, end))
        return {symbol: [] for symbol in symbols}

    def fake_validation(*args, **kwargs):
        return {
            "stage": "VALIDATION",
            "opened": True,
            "candidate_id": "A8-G01-LL-BTC",
            "passed": False,
            "reasons": ["validation_expectancy_nonpositive"],
            "result": {},
        }

    candidate = adaptive_candidate_specs(1)[0].to_dict()
    development_result = {
        "primary": {
            "trade_count": 25,
            "independent_day_blocks": 20,
            "trades_per_day": 0.5,
            "expectancy_per_trade": 0.001,
            "profit_factor": 1.2,
        },
        "one_bar_delay": {
            "expectancy_per_trade": 0.0005,
        },
    }

    async def scenario():
        runtime = service.GraenResearchExecutor()
        runtime.gateway = CampaignGateway()
        runtime._fetch_stage = fake_fetch
        runtime.callback_base_url = ""
        runtime.callback_token = ""
        monkeypatch.setattr(
            service,
            "evaluate_autonomous_validation",
            fake_validation,
        )

        result = await runtime._execute_autonomous_campaign(
            {
                "problem_id": "11111111-1111-1111-1111-111111111111",
                "linked_iren_job_id": None,
                "metadata": {
                    "research_stage": service.AUTONOMOUS_VALIDATION_STAGE,
                    "campaign_epoch": 0,
                    "campaign_generation": 1,
                    "candidate_spec": candidate,
                    "development_result": development_result,
                },
            },
            {
                "run_id": "22222222-2222-2222-2222-222222222222",
            },
        )

        contract = epoch_contract(0)
        assert len(fetches) == 1
        assert fetches[0] == (
            contract["validation_start"],
            contract["holdout_start"],
        )
        assert result["state"] == "AUTONOMOUS_CANDIDATE_REJECTED_VALIDATION"
        queued = runtime.gateway.queued_stages[-1]
        assert queued["stage"] == service.AUTONOMOUS_DEVELOPMENT_STAGE
        assert queued["metadata"]["campaign_epoch"] == 1
        assert queued["metadata"]["campaign_generation"] == 1

    asyncio.run(scenario())


def test_holdout_pass_queues_velum_replay(monkeypatch):
    candidate = adaptive_candidate_specs(1)[0].to_dict()
    development_result = {
        "primary": {
            "trade_count": 25,
            "independent_day_blocks": 20,
            "trades_per_day": 0.5,
            "expectancy_per_trade": 0.001,
            "profit_factor": 1.2,
        },
        "one_bar_delay": {"expectancy_per_trade": 0.0005},
    }
    fetches = []

    async def fake_fetch(symbols, *, start, end, warmup_hours=169):
        fetches.append((start, end))
        return {symbol: [] for symbol in symbols}

    def fake_holdout(*args, **kwargs):
        return {
            "stage": "HOLDOUT",
            "opened": True,
            "candidate_id": candidate["candidate_id"],
            "passed": True,
            "reasons": [],
            "scenarios": {},
        }

    async def scenario():
        runtime = service.GraenResearchExecutor()
        runtime.gateway = CampaignGateway()
        runtime._fetch_stage = fake_fetch
        runtime.callback_base_url = ""
        runtime.callback_token = ""
        monkeypatch.setattr(service, "evaluate_autonomous_holdout", fake_holdout)

        result = await runtime._execute_autonomous_campaign(
            {
                "problem_id": "11111111-1111-1111-1111-111111111111",
                "linked_iren_job_id": None,
                "metadata": {
                    "research_stage": service.AUTONOMOUS_HOLDOUT_STAGE,
                    "campaign_epoch": 0,
                    "campaign_generation": 1,
                    "candidate_spec": candidate,
                    "development_result": development_result,
                },
            },
            {"run_id": "22222222-2222-2222-2222-222222222222"},
        )

        assert result["state"] == "CANDIDATE_READY_FOR_VELUM"
        assert runtime.gateway.completions[-1]["status"] == "WAITING"
        queued = runtime.gateway.queued_stages[-1]
        assert queued["stage"] == service.AUTONOMOUS_VELUM_STAGE
        assert queued["metadata"]["candidate_spec"]["candidate_id"] == candidate["candidate_id"]
        assert "holdout_result" in queued["metadata"]

    asyncio.run(scenario())


def test_velum_failure_advances_to_next_untouched_epoch(monkeypatch):
    candidate = adaptive_candidate_specs(1)[0].to_dict()
    development_result = {
        "primary": {
            "trade_count": 25,
            "independent_day_blocks": 20,
            "trades_per_day": 0.5,
            "expectancy_per_trade": 0.001,
            "profit_factor": 1.2,
        },
        "one_bar_delay": {"expectancy_per_trade": 0.0005},
    }

    async def fake_replay(**kwargs):
        return {
            "candidate_id": candidate["candidate_id"],
            "engineering_gate": {
                "passed": False,
                "reasons": ["replay_high_cost_expectancy_nonpositive"],
            },
            "research_only": True,
            "execution_authority": False,
        }

    async def scenario():
        runtime = service.GraenResearchExecutor()
        runtime.gateway = CampaignGateway()
        runtime.callback_base_url = ""
        runtime.callback_token = ""
        runtime.velum_base_url = "https://velum.invalid"
        runtime.velum_token = "x" * 32
        monkeypatch.setattr(runtime, "_replay_in_velum", fake_replay)

        result = await runtime._execute_autonomous_campaign(
            {
                "problem_id": "11111111-1111-1111-1111-111111111111",
                "linked_iren_job_id": None,
                "metadata": {
                    "research_stage": service.AUTONOMOUS_VELUM_STAGE,
                    "campaign_epoch": 0,
                    "campaign_generation": 1,
                    "candidate_spec": candidate,
                    "development_result": development_result,
                    "holdout_result": {"passed": True},
                    "holdout_artifact_id": "artifact-holdout",
                },
            },
            {"run_id": "22222222-2222-2222-2222-222222222222"},
        )

        assert result["state"] == "AUTONOMOUS_CANDIDATE_REJECTED_VELUM"
        queued = runtime.gateway.queued_stages[-1]
        assert queued["stage"] == service.AUTONOMOUS_DEVELOPMENT_STAGE
        assert queued["metadata"]["campaign_epoch"] == 1
        assert queued["metadata"]["campaign_generation"] == 1

    asyncio.run(scenario())
