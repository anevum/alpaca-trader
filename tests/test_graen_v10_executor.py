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
        self.created_problems = []

    async def create_problem(self, payload):
        self.created_problems.append(payload)
        return {
            "ok": True,
            "problem": {
                "problem_id": "55555555-5555-5555-5555-555555555555",
                "status": "QUEUED",
                "domain": payload.get("domain"),
                "metadata": payload.get("metadata") or {},
            },
        }

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


def test_fetch_stage_never_requests_beyond_stage_end():
    calls = []

    class MarketData:
        async def historical_crypto_bars_many(self, symbols, *, start, end):
            calls.append((start, end))
            return {symbol: [] for symbol in symbols}

    async def scenario():
        runtime = _runtime()
        runtime.market_data = MarketData()
        stage_start = service.datetime(2023, 7, 1, tzinfo=service.UTC)
        stage_end = service.datetime(2023, 10, 1, tzinfo=service.UTC)
        await runtime._fetch_stage(
            service.V10_UNIVERSE,
            start=stage_start,
            end=stage_end,
            warmup_hours=169,
        )
        assert calls
        assert all(request_end < stage_end for _, request_end in calls)
        assert max(request_end for _, request_end in calls) == stage_end - service.timedelta(microseconds=1)

    asyncio.run(scenario())


def test_process_once_blocks_claim_when_research_execution_raises(monkeypatch):
    problem_id = "11111111-1111-1111-1111-111111111111"
    run_id = "22222222-2222-2222-2222-222222222222"

    class ClaimGateway(FakeGateway):
        def __init__(self):
            super().__init__()
            self.blocked = []

        async def snapshot(self):
            return {
                "problems": [{
                    "problem_id": problem_id,
                    "status": "WAITING",
                    "domain": service.PROBLEM_DOMAIN,
                    "metadata": {"research_stage": service.V10_VALIDATION_STAGE},
                }],
                "runs": [],
            }

        async def claim_research_problem(self, **kwargs):
            return {
                "problem": {
                    "problem_id": problem_id,
                    "linked_iren_job_id": None,
                    "metadata": {
                        "research_stage": service.V10_VALIDATION_STAGE,
                        "v10_epoch_index": 1,
                        "v10_candidate_spec": candidate_specs()[0].to_dict(),
                    },
                },
                "run": {"run_id": run_id},
            }

        async def block_research_claim(self, **kwargs):
            self.blocked.append(kwargs)
            return {"ok": True, "status": "BLOCKED"}

    async def fail(*args, **kwargs):
        raise RuntimeError("synthetic-stage-failure")

    async def scenario():
        runtime = _runtime()
        runtime.gateway = ClaimGateway()
        monkeypatch.setattr(runtime, "_execute_trend_pullback_v10", fail)
        try:
            await runtime.process_once()
            assert False, "expected research execution failure"
        except RuntimeError as exc:
            assert str(exc) == "synthetic-stage-failure"
        assert runtime.gateway.blocked == [{
            "problem_id": problem_id,
            "run_id": run_id,
            "worker_id": runtime.worker_id,
            "error": "RuntimeError: synthetic-stage-failure",
        }]
        assert runtime.active_problem_id is None
        assert runtime.last_error == "RuntimeError: synthetic-stage-failure"

    asyncio.run(scenario())


def test_recover_exhausted_v9_into_v10_is_fail_closed_and_idempotent():
    problem_id = "33333333-3333-3333-3333-333333333333"
    terminal_run_id = "44444444-4444-4444-4444-444444444444"
    snapshot = {
        "problems": [{
            "problem_id": problem_id,
            "status": "WAITING",
            "domain": service.PROBLEM_DOMAIN,
            "metadata": {},
        }],
        "runs": [{
            "run_id": terminal_run_id,
            "problem_id": problem_id,
            "result_summary": {
                "campaign_id": service.V9_CAMPAIGN_ID,
                "state": "V9_CAMPAIGN_EXHAUSTED",
                "decision": "NEEDS_NEW_HYPOTHESIS_ENGINE",
                "next_action": "MODEL_HYPOTHESIS_GENERATION_REQUIRED",
            },
        }],
    }

    async def scenario():
        runtime = _runtime()
        recovered = await runtime._recover_exhausted_v9_into_v10(snapshot)
        assert recovered == {
            "recovered": True,
            "problem_id": problem_id,
            "next_research_stage": service.V10_DEVELOPMENT_STAGE,
            "source": "V9_CAMPAIGN_EXHAUSTED",
        }
        assert runtime.gateway.queued_stages == [{
            "problem_id": problem_id,
            "stage": service.V10_DEVELOPMENT_STAGE,
            "metadata": {
                "v10_campaign_id": service.V10_CAMPAIGN_ID,
                "v10_epoch_index": 0,
                "v10_generation": 1,
                "v10_transition_source": "v9_campaign_exhausted",
                "v9_terminal_run_id": terminal_run_id,
            },
        }]

        already_recovered = {
            **snapshot,
            "problems": [{
                **snapshot["problems"][0],
                "metadata": {"v10_campaign_id": service.V10_CAMPAIGN_ID},
            }],
        }
        assert await runtime._recover_exhausted_v9_into_v10(already_recovered) is None
        assert len(runtime.gateway.queued_stages) == 1

        wrong_terminal = {
            **snapshot,
            "runs": [{
                **snapshot["runs"][0],
                "result_summary": {
                    **snapshot["runs"][0]["result_summary"],
                    "state": "V9_CANDIDATE_REJECTED_VALIDATION",
                },
            }],
        }
        assert await runtime._recover_exhausted_v9_into_v10(wrong_terminal) is None
        assert len(runtime.gateway.queued_stages) == 1

    asyncio.run(scenario())


def test_ensure_v10_campaign_seed_creates_one_research_only_problem():
    async def scenario():
        runtime = _runtime()
        result = await runtime._ensure_v10_campaign_seed({"problems": [], "runs": []})
        assert result == {
            "seeded": True,
            "problem_id": "55555555-5555-5555-5555-555555555555",
            "next_research_stage": service.V10_DEVELOPMENT_STAGE,
            "campaign_id": service.V10_CAMPAIGN_ID,
        }
        assert len(runtime.gateway.created_problems) == 1
        created = runtime.gateway.created_problems[0]
        assert created["domain"] == service.PROBLEM_DOMAIN
        assert created["metadata"]["v10_campaign_id"] == service.V10_CAMPAIGN_ID
        assert created["constraints"]["execution_authority"] is False
        assert created["constraints"]["broker_orders_possible"] is False
        assert created["constraints"]["production_promotion_authority"] is False
        assert runtime.gateway.queued_stages[-1]["stage"] == service.V10_DEVELOPMENT_STAGE

        existing = {
            "problems": [{
                "problem_id": "existing-v10",
                "status": "WAITING",
                "domain": service.PROBLEM_DOMAIN,
                "metadata": {"v10_campaign_id": service.V10_CAMPAIGN_ID},
            }],
            "runs": [],
        }
        assert await runtime._ensure_v10_campaign_seed(existing) is None
        assert len(runtime.gateway.created_problems) == 1
        assert len(runtime.gateway.queued_stages) == 1

    asyncio.run(scenario())


def test_observe_blocked_v10_surfaces_stored_error_once():
    runtime = _runtime()
    candidate = candidate_specs()[0].to_dict()
    snapshot = {
        "problems": [{
            "problem_id": "11111111-1111-1111-1111-111111111111",
            "status": "BLOCKED",
            "domain": service.PROBLEM_DOMAIN,
            "metadata": {
                "research_stage": service.V10_VALIDATION_STAGE,
                "v10_epoch_index": 1,
                "v10_candidate_spec": candidate,
            },
        }],
        "runs": [{
            "run_id": "22222222-2222-2222-2222-222222222222",
            "problem_id": "11111111-1111-1111-1111-111111111111",
            "status": "BLOCKED",
            "result_summary": {
                "state": "RESEARCH_EXECUTION_BLOCKED",
                "next_action": "RESUME_FROZEN_STAGE_AFTER_REPAIR",
                "error": "RuntimeError: synthetic-validation-failure",
            },
        }],
    }
    observed = runtime._observe_blocked_v10(snapshot)
    assert observed == {
        "problem_id": "11111111-1111-1111-1111-111111111111",
        "run_id": "22222222-2222-2222-2222-222222222222",
        "research_stage": service.V10_VALIDATION_STAGE,
        "epoch_index": 1,
        "candidate_id": candidate["candidate_id"],
        "error": "RuntimeError: synthetic-validation-failure",
        "next_action": "RESUME_FROZEN_STAGE_AFTER_REPAIR",
        "execution_authority": False,
    }
    assert runtime._observe_blocked_v10(snapshot) is None



def test_terminal_v10_corpus_failure_routes_to_v11_without_execution_authority():
    problem_id = "77777777-7777-7777-7777-777777777777"
    run_id = "88888888-8888-8888-8888-888888888888"
    candidate = candidate_specs()[0].to_dict()
    snapshot = {
        "problems": [{
            "problem_id": problem_id,
            "status": "BLOCKED",
            "domain": service.PROBLEM_DOMAIN,
            "metadata": {
                "research_stage": service.V10_VALIDATION_STAGE,
                "v10_campaign_id": service.V10_CAMPAIGN_ID,
                "v10_epoch_index": 1,
                "v10_candidate_spec": candidate,
            },
        }],
        "runs": [{
            "run_id": run_id,
            "problem_id": problem_id,
            "status": "BLOCKED",
            "result_summary": {
                "state": "RESEARCH_EXECUTION_BLOCKED",
                "decision": "REPAIR_REQUIRED",
                "next_action": "RESUME_FROZEN_STAGE_AFTER_REPAIR",
                "error": "ValueError: v9_stage_corpus_incomplete:SOL/USD",
            },
        }],
    }

    async def scenario():
        runtime = _runtime()
        recovered = await runtime._recover_blocked_v10_corpus_into_v11(snapshot)

        assert recovered["recovered"] is True
        assert recovered["state"] == "V10_CAMPAIGN_EXHAUSTED_CORPUS"
        assert recovered["next_research_stage"] == service.V11_DEVELOPMENT_STAGE
        assert recovered["execution_authority"] is False

        artifact = runtime.gateway.artifacts[-1]
        assert artifact["artifact_type"] == "CRYPTO_TREND_PULLBACK_V10_CORPUS_EXHAUSTION"
        assert artifact["content"]["validation_strategy_evaluation_performed"] is False
        assert artifact["content"]["validation_window_burned"] is True
        assert artifact["content"]["historical_promotion_eligible"] is False
        assert artifact["content"]["execution_authority"] is False
        assert artifact["content"]["broker_orders_possible"] is False
        assert artifact["content"]["production_promotion_authority"] is False

        queued = runtime.gateway.queued_stages[-1]
        assert queued["stage"] == service.V11_DEVELOPMENT_STAGE
        assert queued["metadata"]["v11_campaign_id"] == service.V11_CAMPAIGN_ID
        assert queued["metadata"]["v11_origin"] == "v10_terminal_corpus_exhaustion"

        already_v11 = {
            **snapshot,
            "problems": [{
                **snapshot["problems"][0],
                "metadata": {
                    **snapshot["problems"][0]["metadata"],
                    "v11_campaign_id": service.V11_CAMPAIGN_ID,
                },
            }],
        }
        assert await runtime._recover_blocked_v10_corpus_into_v11(already_v11) is None
        assert len(runtime.gateway.queued_stages) == 1

    asyncio.run(scenario())


def test_v11_no_survivor_routes_to_v12_once_without_execution_authority():
    problem_id = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
    run_id = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
    snapshot = {
        "problems": [{
            "problem_id": problem_id,
            "status": "WAITING",
            "domain": service.PROBLEM_DOMAIN,
            "metadata": {
                "v11_campaign_id": service.V11_CAMPAIGN_ID,
                "v11_generation": 1,
            },
        }],
        "runs": [{
            "run_id": run_id,
            "problem_id": problem_id,
            "status": "WAITING",
            "result_summary": {
                "state": "V11_NO_DEVELOPMENT_SURVIVOR",
                "decision": "NEEDS_NEW_HYPOTHESIS_ENGINE",
                "campaign_id": service.V11_CAMPAIGN_ID,
                "execution_authority": False,
                "broker_orders_possible": False,
            },
        }],
    }

    async def scenario():
        runtime = _runtime()
        recovered = await runtime._recover_exhausted_v11_into_v12(snapshot)
        assert recovered == {
            "recovered": True,
            "problem_id": problem_id,
            "state": "V11_CAMPAIGN_EXHAUSTED",
            "next_research_stage": service.V12_DEVELOPMENT_STAGE,
            "v12_campaign_id": service.V12_CAMPAIGN_ID,
            "execution_authority": False,
        }

        artifact = runtime.gateway.artifacts[-1]
        assert artifact["artifact_type"] == "CRYPTO_BTC_V11_EXHAUSTION"
        assert artifact["content"]["historical_promotion_eligible"] is False
        assert artifact["content"]["execution_authority"] is False
        assert artifact["content"]["broker_orders_possible"] is False
        assert artifact["content"]["production_promotion_authority"] is False

        queued = runtime.gateway.queued_stages[-1]
        assert queued["stage"] == service.V12_DEVELOPMENT_STAGE
        assert queued["metadata"]["v12_campaign_id"] == service.V12_CAMPAIGN_ID
        assert queued["metadata"]["v12_origin"] == "v11_no_development_survivor"

        already_v12 = {
            **snapshot,
            "problems": [{
                **snapshot["problems"][0],
                "metadata": {
                    **snapshot["problems"][0]["metadata"],
                    "v12_campaign_id": service.V12_CAMPAIGN_ID,
                },
            }],
        }
        assert await runtime._recover_exhausted_v11_into_v12(already_v12) is None
        assert len(runtime.gateway.queued_stages) == 1

    asyncio.run(scenario())


def test_v12_claim_diagnostic_exposes_claim_predicate_without_mutation():
    runtime = _runtime()
    snapshot = {
        "problems": [{
            "problem_id": "cccccccc-cccc-cccc-cccc-cccccccccccc",
            "status": "WAITING",
            "domain": service.PROBLEM_DOMAIN,
            "priority": 95,
            "metadata": {
                "research_stage": service.V12_DEVELOPMENT_STAGE,
                "v12_campaign_id": service.V12_CAMPAIGN_ID,
                "code_promotion": {"phase": "PENDING"},
            },
        }],
        "runs": [{
            "run_id": "dddddddd-dddd-dddd-dddd-dddddddddddd",
            "problem_id": "cccccccc-cccc-cccc-cccc-cccccccccccc",
            "status": "WAITING",
            "methodology_version": service.V11_METHODOLOGY_VERSION,
            "result_summary": {"state": "V11_NO_DEVELOPMENT_SURVIVOR"},
        }],
    }
    result = runtime._observe_v12_claimability(snapshot)
    assert result["research_stage"] == service.V12_DEVELOPMENT_STAGE
    assert result["code_promotion_phase"] == "PENDING"
    assert result["claim_predicate"] == {
        "status_waiting": True,
        "domain_match": True,
        "stage_recognized": True,
        "code_promotion_allows": False,
        "eligible": False,
    }
    assert runtime.gateway.queued_stages == []
    assert runtime.gateway.completions == []
