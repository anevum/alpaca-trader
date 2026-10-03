import asyncio

from app.graen import research_executor_service as service
from graen.crypto.btc_hypotheses_v13 import candidate_specs


class FakeGateway:
    configured = True

    def __init__(self):
        self.artifacts = []
        self.completions = []
        self.queued_stages = []
        self.heartbeats = []
        self.blocked_claims = []

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
                "domain": service.PROBLEM_DOMAIN,
                "metadata": {
                    "research_stage": kwargs["stage"],
                    **(kwargs.get("metadata") or {}),
                },
            },
        }

    async def executor_heartbeat(self, **kwargs):
        self.heartbeats.append(kwargs)
        return {"ok": True}

    async def block_research_claim(self, **kwargs):
        self.blocked_claims.append(kwargs)
        return {"ok": True}


PROBLEM_ID = "11111111-1111-1111-1111-111111111111"
RUN_ID = "22222222-2222-2222-2222-222222222222"
PROBLEM = {
    "problem_id": PROBLEM_ID,
    "linked_iren_job_id": None,
    "domain": service.PROBLEM_DOMAIN,
}
RUN = {"run_id": RUN_ID}


def _runtime():
    runtime = service.GraenResearchExecutor()
    runtime.gateway = FakeGateway()
    runtime.callback_base_url = ""
    runtime.callback_token = ""
    return runtime


def _development_result(selected=False):
    candidate = candidate_specs()[0].to_dict()
    summary = {
        "trade_count": 100,
        "independent_day_blocks": 80,
        "expectancy_per_trade": 0.001 if selected else -0.001,
        "profit_factor": 1.3 if selected else 0.8,
        "max_drawdown": 0.1,
        "win_loss_distribution": {},
        "mfe_mae": {"available": True},
    }
    row = {
        "aggregate_high": {
            "candidate": candidate,
            "primary": summary,
            "one_bar_delay": {
                **summary,
                "expectancy_per_trade": 0.0005 if selected else -0.0012,
            },
        },
        "aggregate_base": {
            "candidate": candidate,
            "primary": {
                **summary,
                "expectancy_per_trade": 0.0015 if selected else -0.0005,
            },
        },
        "positive_temporal_folds": 7 if selected else 1,
        "dependence_adjusted_p_value": 0.001 if selected else 0.8,
        "statistical_survival": selected,
        "economic_survival": selected,
        "selection_score": 0.01 if selected else -0.01,
        "reasons": [] if selected else ["development_expectancy_nonpositive"],
    }
    return {
        "stage": "HISTORICAL_DEVELOPMENT",
        "results": {candidate["candidate_id"]: row},
        "selected_candidate_id": candidate["candidate_id"] if selected else None,
        "selected_candidate_spec": candidate if selected else None,
        "survivors": [candidate["candidate_id"]] if selected else [],
    }


def test_v13_no_survivor_stops_without_opening_validation_or_holdout(monkeypatch):
    fetches = []

    async def fake_fetch(symbols, *, start, end, warmup_hours=26):
        fetches.append((tuple(symbols), start, end))
        return {symbol: [] for symbol in symbols}

    monkeypatch.setattr(
        service,
        "verify_v13_development_corpus",
        lambda *args, **kwargs: {"passed": True},
    )
    monkeypatch.setattr(
        service,
        "evaluate_v13_development",
        lambda *args, **kwargs: _development_result(False),
    )

    async def scenario():
        runtime = _runtime()
        runtime._fetch_stage = fake_fetch
        result = await runtime._execute_btc_hypotheses_v13(
            {
                **PROBLEM,
                "metadata": {
                    "research_stage": service.V13_DEVELOPMENT_STAGE,
                    "v13_campaign_id": service.V13_CAMPAIGN_ID,
                },
            },
            RUN,
        )
        assert fetches == [(
            tuple(service.V13_UNIVERSE),
            service.V13_DEVELOPMENT_START,
            service.V13_DEVELOPMENT_END,
        )]
        assert result["state"] == "V13_NO_DEVELOPMENT_SURVIVOR"
        assert result["validation_opened"] is False
        assert result["holdout_opened"] is False
        assert runtime.gateway.queued_stages == []
        assert runtime.gateway.completions[-1]["status"] == "WAITING"
        assert result["execution_authority"] is False
        assert result["broker_orders_possible"] is False

    asyncio.run(scenario())


def test_v13_development_survivor_queues_velum_only(monkeypatch):
    async def fake_fetch(symbols, *, start, end, warmup_hours=26):
        return {symbol: [] for symbol in symbols}

    monkeypatch.setattr(
        service,
        "verify_v13_development_corpus",
        lambda *args, **kwargs: {"passed": True},
    )
    monkeypatch.setattr(
        service,
        "evaluate_v13_development",
        lambda *args, **kwargs: _development_result(True),
    )

    async def scenario():
        runtime = _runtime()
        runtime._fetch_stage = fake_fetch
        result = await runtime._execute_btc_hypotheses_v13(
            {
                **PROBLEM,
                "metadata": {
                    "research_stage": service.V13_DEVELOPMENT_STAGE,
                    "v13_campaign_id": service.V13_CAMPAIGN_ID,
                },
            },
            RUN,
        )
        assert result["state"] == "V13_CANDIDATE_FROZEN_FOR_VELUM"
        queued = runtime.gateway.queued_stages[-1]
        assert queued["stage"] == service.V13_VELUM_STAGE
        assert queued["metadata"]["v13_velum_passed"] is False
        assert queued["metadata"]["v13_validation_passed"] is False
        assert queued["metadata"]["v13_holdout_passed"] is False

    asyncio.run(scenario())


def test_v13_velum_pass_queues_validation_not_holdout():
    candidate = candidate_specs()[0].to_dict()

    async def scenario():
        runtime = _runtime()

        async def fake_replay(**kwargs):
            assert kwargs["candidate_methodology"] == service.V13_METHODOLOGY_VERSION
            return {"engineering_gate": {"passed": True, "reasons": []}}

        runtime._replay_in_velum = fake_replay
        result = await runtime._execute_btc_hypotheses_v13(
            {
                **PROBLEM,
                "metadata": {
                    "research_stage": service.V13_VELUM_STAGE,
                    "v13_campaign_id": service.V13_CAMPAIGN_ID,
                    "v13_candidate_spec": candidate,
                    "v13_development_artifact_id": "dev-artifact",
                },
            },
            RUN,
        )
        assert result["state"] == "V13_VELUM_PASS"
        queued = runtime.gateway.queued_stages[-1]
        assert queued["stage"] == service.V13_VALIDATION_STAGE
        assert queued["metadata"]["v13_velum_passed"] is True
        assert queued["metadata"]["v13_validation_passed"] is False
        assert queued["metadata"]["v13_holdout_passed"] is False

    asyncio.run(scenario())


def test_v13_validation_cannot_open_before_velum_pass():
    candidate = candidate_specs()[0].to_dict()

    async def scenario():
        runtime = _runtime()
        try:
            await runtime._execute_btc_hypotheses_v13(
                {
                    **PROBLEM,
                    "metadata": {
                        "research_stage": service.V13_VALIDATION_STAGE,
                        "v13_candidate_spec": candidate,
                        "v13_velum_passed": False,
                    },
                },
                RUN,
            )
        except RuntimeError as exc:
            assert str(exc) == "v13_validation_opened_before_velum_pass"
        else:
            raise AssertionError("validation must fail closed before VELUM pass")

    asyncio.run(scenario())


def test_v13_holdout_cannot_open_before_validation_pass():
    candidate = candidate_specs()[0].to_dict()

    async def scenario():
        runtime = _runtime()
        try:
            await runtime._execute_btc_hypotheses_v13(
                {
                    **PROBLEM,
                    "metadata": {
                        "research_stage": service.V13_HOLDOUT_STAGE,
                        "v13_candidate_spec": candidate,
                        "v13_velum_passed": True,
                        "v13_validation_passed": False,
                    },
                },
                RUN,
            )
        except RuntimeError as exc:
            assert str(exc) == "v13_holdout_opened_before_predecessor_gates"
        else:
            raise AssertionError("holdout must fail closed before validation pass")

    asyncio.run(scenario())


def test_v13_validation_and_holdout_use_distinct_forward_evidence_phases():
    candidate = candidate_specs()[0].to_dict()

    async def scenario():
        runtime = _runtime()
        phases = []

        async def fake_activate(**kwargs):
            phases.append(kwargs["evidence_phase"])
            return {
                "activation": {
                    "activation_id": f"activation-{kwargs['evidence_phase'].lower()}",
                    "evidence_phase": kwargs["evidence_phase"],
                },
                "duplicate": False,
            }

        runtime._activate_forward_shadow = fake_activate
        validation = await runtime._execute_btc_hypotheses_v13(
            {
                **PROBLEM,
                "metadata": {
                    "research_stage": service.V13_VALIDATION_STAGE,
                    "v13_candidate_spec": candidate,
                    "v13_velum_passed": True,
                    "v13_velum_artifact_id": "velum-artifact",
                },
            },
            RUN,
        )
        holdout = await runtime._execute_btc_hypotheses_v13(
            {
                **PROBLEM,
                "metadata": {
                    "research_stage": service.V13_HOLDOUT_STAGE,
                    "v13_candidate_spec": candidate,
                    "v13_velum_passed": True,
                    "v13_validation_passed": True,
                    "v13_velum_artifact_id": "velum-artifact",
                    "v13_validation_artifact_id": "validation-artifact",
                },
            },
            RUN,
        )
        assert phases == ["VALIDATION", "HOLDOUT"]
        assert validation["validation_opened"] is True
        assert validation["holdout_opened"] is False
        assert holdout["holdout_opened"] is True

    asyncio.run(scenario())


def test_v12_no_survivor_recovers_once_into_v13():
    snapshot = {
        "problems": [{
            "problem_id": PROBLEM_ID,
            "status": "WAITING",
            "domain": service.PROBLEM_DOMAIN,
            "metadata": {"v12_campaign_id": service.V12_CAMPAIGN_ID},
        }],
        "runs": [{
            "run_id": RUN_ID,
            "problem_id": PROBLEM_ID,
            "status": "WAITING",
            "result_summary": {
                "campaign_id": service.V12_CAMPAIGN_ID,
                "state": "V12_NO_DEVELOPMENT_SURVIVOR",
                "decision": "NEEDS_NEW_HYPOTHESIS_ENGINE",
            },
        }],
    }

    async def scenario():
        runtime = _runtime()
        result = await runtime._recover_exhausted_v12_into_v13(snapshot)
        assert result["next_research_stage"] == service.V13_DEVELOPMENT_STAGE
        assert result["execution_authority"] is False
        queued = runtime.gateway.queued_stages[-1]
        assert queued["stage"] == service.V13_DEVELOPMENT_STAGE
        already = {
            **snapshot,
            "problems": [{
                **snapshot["problems"][0],
                "metadata": {
                    "v12_campaign_id": service.V12_CAMPAIGN_ID,
                    "v13_campaign_id": service.V13_CAMPAIGN_ID,
                },
            }],
        }
        assert await runtime._recover_exhausted_v12_into_v13(already) is None

    asyncio.run(scenario())


def test_v13_interrupted_development_requeues_without_opening_sealed_evidence():
    from datetime import datetime, timedelta, timezone

    started = (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()
    snapshot = {
        "runtime_state": {
            "metadata": {
                "research_executor": {"active_problem_id": None},
            },
        },
        "problems": [{
            "problem_id": PROBLEM_ID,
            "status": "RUNNING",
            "domain": service.PROBLEM_DOMAIN,
            "metadata": {
                "research_stage": service.V13_DEVELOPMENT_STAGE,
                "v13_campaign_id": service.V13_CAMPAIGN_ID,
            },
        }],
        "runs": [{
            "run_id": RUN_ID,
            "problem_id": PROBLEM_ID,
            "status": "RUNNING",
            "started_at": started,
        }],
    }

    async def scenario():
        runtime = _runtime()
        result = await runtime._recover_orphaned_v13_nonconfirmatory_claim(snapshot)
        assert result["recovered"] is True
        assert result["requeued_stage"] == service.V13_DEVELOPMENT_STAGE
        assert result["sealed_evidence_opened"] is False
        assert runtime.gateway.blocked_claims[-1]["run_id"] == RUN_ID
        queued = runtime.gateway.queued_stages[-1]
        assert queued["stage"] == service.V13_DEVELOPMENT_STAGE
        assert queued["metadata"]["v13_recovered_interrupted_run_id"] == RUN_ID

    asyncio.run(scenario())


def test_v13_claim_diagnostic_reports_exact_native_stage_predicate():
    runtime = _runtime()
    snapshot = {
        "problems": [{
            "problem_id": PROBLEM_ID,
            "status": "WAITING",
            "priority": 95,
            "domain": service.PROBLEM_DOMAIN,
            "metadata": {
                "research_stage": service.V13_DEVELOPMENT_STAGE,
                "v13_campaign_id": service.V13_CAMPAIGN_ID,
            },
        }],
        "runs": [{
            "run_id": RUN_ID,
            "problem_id": PROBLEM_ID,
            "status": "BLOCKED",
            "methodology_version": service.V13_METHODOLOGY_VERSION,
            "result_summary": {"state": "RESEARCH_EXECUTION_BLOCKED"},
        }],
    }
    diagnostic = runtime._observe_v13_claimability(snapshot)
    assert diagnostic["claim_predicate"] == {
        "status_waiting": True,
        "domain_match": True,
        "stage_recognized": True,
        "code_promotion_allows": True,
        "eligible": True,
    }
    assert diagnostic["execution_authority"] is False
