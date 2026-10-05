import asyncio

from app.graen import research_executor_service as service
from app.research_agent.strategy_grammar import build_manifest, manifest_hash


class FakeGateway:
    configured = True

    def __init__(self):
        self.artifacts = []
        self.completions = []
        self.heartbeats = []
        self.queued_stages = []
        self.created_problems = []

    async def snapshot(self):
        return {"problems": []}

    async def create_problem(self, payload):
        self.created_problems.append(payload)
        return {
            "ok": True,
            "problem": {
                "problem_id": "99999999-9999-9999-9999-999999999999",
                "status": "QUEUED",
                "domain": payload.get("domain"),
                "metadata": payload.get("metadata") or {},
            },
        }

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


def test_stale_orphaned_validation_is_invalidated_before_research_continues():
    problem_id = "55555555-5555-5555-5555-555555555555"
    run_id = "66666666-6666-6666-6666-666666666666"

    class RecoveryGateway(FakeGateway):
        def __init__(self):
            super().__init__()
            self.blocked = []

        async def block_research_claim(self, **kwargs):
            self.blocked.append(kwargs)
            return {"ok": True, "status": "BLOCKED"}

    async def scenario():
        runtime = service.GraenResearchExecutor()
        gateway = RecoveryGateway()
        runtime.gateway = gateway
        result = await runtime._reconcile_orphaned_confirmatory_claim({
            "problems": [{
                "problem_id": problem_id,
                "status": "RUNNING",
                "metadata": {
                    "research_stage": service.V10_VALIDATION_STAGE,
                },
            }],
            "runs": [{
                "run_id": run_id,
                "problem_id": problem_id,
                "status": "RUNNING",
                "started_at": "2026-01-01T00:00:00+00:00",
            }],
            "runtime_state": {
                "metadata": {
                    "research_executor": {
                        "active_problem_id": None,
                    },
                },
            },
        })
        assert result == {
            "problem_id": problem_id,
            "run_id": run_id,
            "invalidated_stage": service.V10_VALIDATION_STAGE,
            "next_stage": "RESEARCH_IMPLEMENTATION_REQUIRED",
        }
        assert gateway.blocked == [{
            "problem_id": problem_id,
            "run_id": run_id,
            "worker_id": runtime.worker_id,
            "error": "sealed_confirmatory_stage_orphaned:" + service.V10_VALIDATION_STAGE,
        }]
        assert gateway.queued_stages[-1]["stage"] == "RESEARCH_IMPLEMENTATION_REQUIRED"
        assert gateway.queued_stages[-1]["metadata"]["sealed_stage_invalidated"] is True
        assert gateway.queued_stages[-1]["metadata"]["next_action"] == "MODEL_HYPOTHESIS_GENERATION_REQUIRED"

    asyncio.run(scenario())


def test_active_confirmatory_claim_is_not_reconciled():
    problem_id = "77777777-7777-7777-7777-777777777777"

    async def scenario():
        runtime = service.GraenResearchExecutor()
        gateway = FakeGateway()
        runtime.gateway = gateway
        result = await runtime._reconcile_orphaned_confirmatory_claim({
            "problems": [{
                "problem_id": problem_id,
                "status": "RUNNING",
                "metadata": {"research_stage": service.V10_VALIDATION_STAGE},
            }],
            "runs": [{
                "run_id": "88888888-8888-8888-8888-888888888888",
                "problem_id": problem_id,
                "status": "RUNNING",
                "started_at": "2026-01-01T00:00:00+00:00",
            }],
            "runtime_state": {
                "metadata": {
                    "research_executor": {
                        "active_problem_id": problem_id,
                    },
                },
            },
        })
        assert result is None
        assert gateway.queued_stages == []

    asyncio.run(scenario())



def test_autonomous_loop_bootstrap_is_exactly_once_and_research_only():
    class BootstrapGateway(FakeGateway):
        def __init__(self):
            super().__init__()
            self.problem = None

        async def snapshot(self):
            return {
                "problems": [self.problem] if self.problem else [],
                "runs": [],
            }

        async def create_problem(self, payload):
            self.created_problems.append(payload)
            self.problem = {
                "problem_id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
                "status": "QUEUED",
                "domain": payload["domain"],
                "metadata": dict(payload.get("metadata") or {}),
            }
            return {"ok": True, "problem": dict(self.problem)}

        async def queue_research_stage(self, **kwargs):
            self.queued_stages.append(kwargs)
            self.problem = {
                **self.problem,
                "status": "WAITING",
                "metadata": {
                    **dict(self.problem.get("metadata") or {}),
                    "research_stage": kwargs["stage"],
                    **dict(kwargs.get("metadata") or {}),
                },
            }
            return {"ok": True, "problem": dict(self.problem)}

    async def scenario():
        runtime = service.GraenResearchExecutor()
        gateway = BootstrapGateway()
        runtime.gateway = gateway

        first = await runtime._ensure_autonomous_loop_seed(
            await gateway.snapshot()
        )
        second = await runtime._ensure_autonomous_loop_seed(
            await gateway.snapshot()
        )

        assert first["seeded"] is True
        assert first["autonomous_loop_id"] == service.AUTONOMOUS_LOOP_ID
        assert first["next_research_stage"] == service.HYPOTHESIS_PLANNER_STAGE
        assert first["execution_authority"] is False
        assert first["runtime_source_mutation_authorized"] is False
        assert first["live_execution_authorized"] is False
        assert second is None

        assert len(gateway.created_problems) == 1
        created = gateway.created_problems[0]
        assert created["priority"] == 100
        assert created["constraints"]["execution_authority"] is False
        assert created["constraints"]["runtime_source_mutation_authorized"] is False
        assert created["constraints"]["spending_authority"] is False
        assert created["constraints"]["production_risk_increase_authority"] is False
        assert created["constraints"]["unrestricted_live_promotion_authority"] is False
        assert len(gateway.queued_stages) == 1
        assert gateway.queued_stages[0]["stage"] == service.HYPOTHESIS_PLANNER_STAGE
        assert (
            gateway.queued_stages[0]["metadata"]["autonomous_loop_id"]
            == service.AUTONOMOUS_LOOP_ID
        )

    asyncio.run(scenario())


def test_legacy_campaign_bootstrap_is_disabled_by_default(monkeypatch):
    monkeypatch.delenv("GRAEN_LEGACY_CAMPAIGN_BOOTSTRAP", raising=False)
    runtime = service.GraenResearchExecutor()
    assert runtime.autonomous_loop_bootstrap is True
    assert runtime.legacy_campaign_bootstrap is False



def test_paper_pass_forks_human_decision_and_continues_planner():
    async def scenario():
        runtime = service.GraenResearchExecutor()
        gateway = FakeGateway()
        runtime.gateway = gateway
        runtime.callback_base_url = ""
        runtime.callback_token = ""
        runtime.paper_base_url = "http://paper.internal"
        runtime.paper_token = "p" * 40

        manifest = build_manifest(
            hypothesis_id="AUTO-PAPER-CONTINUE-01",
            family="cross_asset_diffusion",
            mechanism="BTC information diffusion into liquid crypto followers.",
            information_source="cross_asset_returns",
            feature="lead_lag_gap",
            transformation="residualize_btc",
            regime="dispersion_bucket",
            trigger="threshold",
            entry="market_next_bar",
            exit="time_60m",
            parameters={
                "hold_minutes": 60,
                "scan_minutes": 10,
                "lookback_minutes": 30,
                "concentration_limit": 0.70,
                "leader_symbol": "BTC/USD",
                "leader_threshold": 0.0035,
                "lag_gap_threshold": 0.0015,
                "min_target_return": -0.005,
                "max_target_return": 0.0035,
                "min_breadth_positive": 3,
                "require_btc_nonnegative": False,
            },
            symbols=("BTC/USD", "ETH/USD", "SOL/USD"),
            timeframe="5m",
            falsification_statement=(
                "Reject if stressed-cost forward expectancy is nonpositive."
            ),
        )

        async def paper_status(_activation_id):
            return {
                "checkpoint": {
                    "status": "PAPER_PASSED",
                    "live_execution_authorized": False,
                    "promotion_authorized": False,
                }
            }

        runtime._paper_canary_status = paper_status
        problem = {
            "problem_id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
            "linked_iren_job_id": None,
            "metadata": {
                "research_stage": service.STRATEGY_PAPER_STAGE,
                "autonomous_loop_id": service.AUTONOMOUS_LOOP_ID,
                "autonomous_continuation": True,
                "strategy_manifest": manifest.as_dict(),
                "strategy_manifest_hash": manifest_hash(manifest),
                "strategy_corpus": {
                    "development": [
                        "2026-01-01T00:00:00+00:00",
                        "2026-03-01T00:00:00+00:00",
                    ],
                    "validation": [
                        "2026-03-01T00:00:00+00:00",
                        "2026-04-01T00:00:00+00:00",
                    ],
                    "holdout": [
                        "2026-04-01T00:00:00+00:00",
                        "2026-05-01T00:00:00+00:00",
                    ],
                },
                "strategy_search_generation": 1,
                "strategy_validation_alpha": 0.01,
                "strategy_shadow_activation": {
                    "activation_id": "shadow-validated-001",
                },
                "strategy_shadow_checkpoint": {
                    "status": "READY_FOR_PAPER",
                },
                "strategy_paper_activation": {
                    "activation_id": "paper-validated-001",
                },
            },
        }
        result = await runtime._execute_strategy_manifest(
            problem,
            {"run_id": "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"},
        )

        assert result["condition"] == "HUMAN_DECISION_REQUIRED"
        assert result["state"] == "STRATEGY_PAPER_VALIDATED"
        assert result["live_execution_authority"] is False
        assert result["live_promotion_authorized"] is False
        assert result["continuation_queued"] is True
        assert result["next_research_stage"] == service.HYPOTHESIS_PLANNER_STAGE
        assert gateway.completions[-1]["status"] == "WAITING"
        assert gateway.queued_stages[-1]["stage"] == service.HYPOTHESIS_PLANNER_STAGE
        assert (
            gateway.queued_stages[-1]["metadata"][
                "protected_live_risk_decision_pending"
            ]
            is True
        )
        assert (
            gateway.queued_stages[-1]["metadata"][
                "last_paper_validated_candidate_id"
            ]
            == manifest.hypothesis_id
        )

    asyncio.run(scenario())



def test_interrupted_autonomous_bootstrap_recovers_without_duplicate_problem():
    class InterruptedGateway(FakeGateway):
        def __init__(self):
            super().__init__()
            self.problem = {
                "problem_id": "cccccccc-cccc-cccc-cccc-cccccccccccc",
                "status": "QUEUED",
                "domain": service.PROBLEM_DOMAIN,
                "metadata": {
                    "autonomous_loop_id": service.AUTONOMOUS_LOOP_ID,
                    "autonomous_loop_bootstrap_version": (
                        service.AUTONOMOUS_LOOP_BOOTSTRAP_VERSION
                    ),
                    "autonomous_continuation": True,
                },
            }

        async def create_problem(self, payload):
            raise AssertionError("interrupted bootstrap must not create a duplicate")

        async def queue_research_stage(self, **kwargs):
            self.queued_stages.append(kwargs)
            self.problem = {
                **self.problem,
                "status": "WAITING",
                "metadata": {
                    **self.problem["metadata"],
                    "research_stage": kwargs["stage"],
                    **dict(kwargs.get("metadata") or {}),
                },
            }
            return {"ok": True, "problem": dict(self.problem)}

    async def scenario():
        runtime = service.GraenResearchExecutor()
        gateway = InterruptedGateway()
        runtime.gateway = gateway

        result = await runtime._ensure_autonomous_loop_seed(
            {"problems": [dict(gateway.problem)], "runs": []}
        )

        assert result["seeded"] is False
        assert result["recovered"] is True
        assert result["next_research_stage"] == service.HYPOTHESIS_PLANNER_STAGE
        assert len(gateway.queued_stages) == 1
        assert gateway.queued_stages[0]["stage"] == service.HYPOTHESIS_PLANNER_STAGE
        assert gateway.created_problems == []

    asyncio.run(scenario())


def test_engineering_wait_releases_after_new_source_deployment(monkeypatch):
    problem_id = "dddddddd-dddd-dddd-dddd-dddddddddddd"

    class EngineeringGateway(FakeGateway):
        def __init__(self):
            super().__init__()
            self.problem = {
                "problem_id": problem_id,
                "status": "WAITING",
                "domain": service.PROBLEM_DOMAIN,
                "metadata": {
                    "autonomous_loop_id": service.AUTONOMOUS_LOOP_ID,
                    "autonomous_continuation": True,
                    "research_stage": service.ENGINEERING_REQUIRED_STAGE,
                    "resume_stage": service.HYPOTHESIS_PLANNER_STAGE,
                    "engineering_required_source_commit": "old-source-commit",
                },
            }

        async def snapshot(self):
            return {
                "problems": [dict(self.problem)],
                "runs": [],
                "artifacts": [],
                "runtime_state": {},
            }

        async def queue_research_stage(self, **kwargs):
            self.queued_stages.append(kwargs)
            self.problem = {
                **self.problem,
                "status": "WAITING",
                "metadata": {
                    **self.problem["metadata"],
                    "research_stage": kwargs["stage"],
                    **dict(kwargs.get("metadata") or {}),
                },
            }
            return {"ok": True, "problem": dict(self.problem)}

        async def claim_research_problem(self, **kwargs):
            return {"problem": None, "run": None}

    async def scenario():
        monkeypatch.setenv("RAILWAY_GIT_COMMIT_SHA", "new-source-commit")
        runtime = service.GraenResearchExecutor()
        gateway = EngineeringGateway()
        runtime.gateway = gateway

        result = await runtime.process_once()

        assert result["status"] == "IDLE"
        assert gateway.queued_stages
        release = gateway.queued_stages[0]
        assert release["problem_id"] == problem_id
        assert release["stage"] == service.HYPOTHESIS_PLANNER_STAGE
        assert (
            release["metadata"]["engineering_resumed_on_source_commit"]
            == "new-source-commit"
        )
        assert release["metadata"]["autonomous_continuation"] is True
        assert runtime.engineering_required_count == 0

    asyncio.run(scenario())



def test_process_once_dispatches_forward_shadow_and_paper_to_strategy_lifecycle():
    async def run_stage(stage):
        problem_id = "eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee"
        run_id = "ffffffff-ffff-ffff-ffff-ffffffffffff"

        class StageGateway(FakeGateway):
            async def snapshot(self):
                return {
                    "problems": [{
                        "problem_id": problem_id,
                        "status": "WAITING",
                        "domain": service.PROBLEM_DOMAIN,
                        "priority": 100,
                        "metadata": {
                            "autonomous_loop_id": service.AUTONOMOUS_LOOP_ID,
                            "autonomous_continuation": True,
                            "research_stage": stage,
                        },
                    }],
                    "runs": [],
                    "artifacts": [],
                    "runtime_state": {},
                }

            async def claim_research_problem(self, **kwargs):
                return {
                    "problem": {
                        "problem_id": problem_id,
                        "status": "RUNNING",
                        "domain": service.PROBLEM_DOMAIN,
                        "linked_iren_job_id": None,
                        "metadata": {
                            "autonomous_loop_id": service.AUTONOMOUS_LOOP_ID,
                            "autonomous_continuation": True,
                            "research_stage": stage,
                        },
                    },
                    "run": {"run_id": run_id},
                }

        runtime = service.GraenResearchExecutor()
        runtime.gateway = StageGateway()
        called = []

        async def execute(problem, run):
            called.append((problem["metadata"]["research_stage"], run["run_id"]))
            return {"claimed": True, "stage": stage}

        runtime._execute_strategy_manifest = execute
        result = await runtime.process_once()

        assert result == {"claimed": True, "stage": stage}
        assert called == [(stage, run_id)]

    asyncio.run(run_stage(service.STRATEGY_SHADOW_STAGE))
    asyncio.run(run_stage(service.STRATEGY_PAPER_STAGE))



def test_orphaned_autonomous_validation_burns_evidence_and_returns_to_planner():
    problem_id = "12121212-1212-1212-1212-121212121212"
    run_id = "34343434-3434-3434-3434-343434343434"

    class RecoveryGateway(FakeGateway):
        def __init__(self):
            super().__init__()
            self.blocked = []

        async def block_research_claim(self, **kwargs):
            self.blocked.append(kwargs)
            return {"ok": True, "status": "BLOCKED"}

    async def scenario():
        runtime = service.GraenResearchExecutor()
        gateway = RecoveryGateway()
        runtime.gateway = gateway
        result = await runtime._reconcile_orphaned_confirmatory_claim({
            "problems": [{
                "problem_id": problem_id,
                "status": "RUNNING",
                "metadata": {
                    "autonomous_loop_id": service.AUTONOMOUS_LOOP_ID,
                    "research_stage": service.STRATEGY_VALIDATION_STAGE,
                    "strategy_manifest": {
                        "hypothesis_id": "AUTO-LL-09",
                    },
                },
            }],
            "runs": [{
                "run_id": run_id,
                "problem_id": problem_id,
                "status": "RUNNING",
                "started_at": "2026-01-01T00:00:00+00:00",
            }],
            "runtime_state": {
                "metadata": {
                    "research_executor": {
                        "active_problem_id": None,
                    },
                },
            },
        })

        assert result["invalidated_stage"] == service.STRATEGY_VALIDATION_STAGE
        assert result["candidate_id"] == "AUTO-LL-09"
        assert result["opened_evidence_burned"] is True
        assert result["next_stage"] == service.HYPOTHESIS_PLANNER_STAGE
        assert gateway.blocked[-1]["run_id"] == run_id
        assert gateway.queued_stages[-1]["stage"] == service.HYPOTHESIS_PLANNER_STAGE
        assert gateway.queued_stages[-1]["metadata"]["sealed_stage_invalidated"] is True
        assert gateway.queued_stages[-1]["metadata"]["opened_evidence_burned"] is True
        assert gateway.queued_stages[-1]["metadata"]["falsified_candidate_id"] == "AUTO-LL-09"
        assert gateway.artifacts[-1]["artifact_type"] == (
            "CRYPTO_STRATEGY_CONFIRMATORY_ORPHAN_INVALIDATION"
        )

    asyncio.run(scenario())
