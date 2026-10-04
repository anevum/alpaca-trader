import asyncio
from copy import deepcopy
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from graen.engineering import (
    SCHEMA,
    IntegrityError,
    ProtectedChange,
    compile_bundle,
    digest,
    stage_bars,
    stage_window,
    validate_spec,
    verify_bundle,
    verify_exposure,
)
from graen.crypto.flow_pressure import flow_signal
from app.graen.research_promotion import ResearchPromotion, engineering_problem_ids

UTC = timezone.utc


def spec():
    return {
        "schema_version": SCHEMA,
        "hypothesis_id": "CRYPTO-FLOW-PRESSURE-002",
        "epoch": "SYNTHETIC-TEST-ONLY",
        "mechanism": "bar_flow_pressure_v1",
        "universe": ["BTC/USD", "ETH/USD", "SOL/USD"],
        "parameters": {
            "lookback_bars": 36,
            "volume_z": 2.0,
            "trade_count_z": 2.0,
            "range_ratio": 1.5,
            "body_strength": 0.6,
            "close_location": 0.8,
            "hold_minutes": 30,
            "cooldown_minutes": 60,
        },
        "corpus": {
            "development": ["2000-01-01T00:00:00Z", "2000-04-01T00:00:00Z"],
            "validation": ["2000-04-01T00:00:00Z", "2000-06-01T00:00:00Z"],
            "holdout": ["2000-06-01T00:00:00Z", "2000-08-01T00:00:00Z"],
        },
        "gates": {
            "policy": "activity_shock_v9_frozen_gates",
            "selection_cost": "high",
            "delay_minutes": 5,
            "confirmatory_candidates": 1,
        },
        "falsification": "Reject on any frozen stage gate failure.",
        "search_history": ["synthetic fixture; no market data read"],
        "exposure_artifact_id": "exposure-fixture",
        "research_only": True,
        "execution_authority": False,
    }


def exposure():
    return {
        "artifact_id": "exposure-fixture",
        "complete": True,
        "inspected_intervals": [],
    }


@pytest.mark.parametrize(
    "stage,previous", [("validation", "development"), ("holdout", "validation")]
)
def test_sealed_stage_requires_exact_frozen_predecessor(stage, previous):
    value = spec()
    good = {
        "stage": previous,
        "passed": True,
        "spec_hash": digest(value),
        "epoch": value["epoch"],
        "artifact_id": "canonical",
    }
    stage_window(value, stage, good)
    for key, bad in (
        ("passed", False),
        ("spec_hash", "other"),
        ("epoch", "other"),
        ("stage", "other"),
        ("artifact_id", ""),
    ):
        with pytest.raises(IntegrityError):
            stage_window(value, stage, {**good, key: bad})
    with pytest.raises(IntegrityError):
        stage_window(value, stage)


def test_sealed_data_and_corrupt_prespec_fail_closed():
    value = spec()
    with pytest.raises(IntegrityError):
        verify_exposure(value, {**exposure(), "complete": False})
    with pytest.raises(IntegrityError):
        verify_exposure(
            value,
            {**exposure(), "inspected_intervals": [value["corpus"]["holdout"]]},
        )
    value["corpus"]["validation"] = value["corpus"]["development"]
    with pytest.raises(IntegrityError):
        validate_spec(value)
    value = spec()
    value["parameters"]["volume_z"] = float("nan")
    with pytest.raises(IntegrityError):
        validate_spec(value)


def test_provider_inclusive_boundary_is_discarded():
    start, end = stage_window(spec(), "development")
    bars = [
        {"t": start.isoformat()},
        {"t": end.isoformat()},
        {"t": "2001-01-01T00:00:00Z"},
    ]
    assert stage_bars(bars, start=start, end=end) == [bars[0]]


@pytest.mark.parametrize(
    "path",
    [
        "app/strategy.py",
        "app/risk.py",
        "app/alpaca_client.py",
        "app/config.py",
        "app/graen/research_promotion.py",
        ".github/workflows/ci.yml",
        "graen/crypto/generated/../../app/risk.py",
        ".env",
        "Dockerfile",
    ],
)
def test_protected_paths_and_controller_self_modification_refused(path):
    value = spec()
    files = compile_bundle(value)
    files[path] = "changed"
    with pytest.raises(ProtectedChange):
        verify_bundle(value, files)


def test_generated_python_cannot_be_replaced_with_broker_import():
    value = spec()
    files = compile_bundle(value)
    path = next(path for path in files if path.startswith("graen/"))
    files[path] += "\nimport app.alpaca_client\n"
    with pytest.raises(ProtectedChange):
        verify_bundle(value, files)


def test_flow_requires_all_five_features_and_strict_history():
    value = spec()
    history = [
        {"o": 100, "h": 101, "l": 99, "c": 100, "v": 100 + i, "n": 50 + i}
        for i in range(36)
    ]
    current = {
        "o": 100,
        "h": 105,
        "l": 99,
        "c": 104.9,
        "v": 100000,
        "n": 10000,
    }
    assert flow_signal(current, history, value["parameters"])
    assert flow_signal(current, history[:-1], value["parameters"]) is None
    for key, invalid in (("n", 1), ("v", 1), ("c", 99), ("h", float("nan"))):
        assert (
            flow_signal({**current, key: invalid}, history, value["parameters"])
            is None
        )


class MemoryStore:
    def __init__(self):
        self.state = {}
        self.saved = []
        self.value = spec()
        self.heartbeat = {}
        self.revision = 0

    async def claim(self, problem_id, owner):
        return {
            "claimed": True,
            "revision": self.revision,
            "state": deepcopy(self.state),
            "prespec": deepcopy(self.value),
            "exposure": exposure(),
            "prespec_artifact_id": "frozen" if self.saved else None,
            "executor_heartbeat": deepcopy(self.heartbeat),
        }

    async def save(self, problem_id, owner, revision, state):
        assert revision == self.revision
        self.revision += 1
        self.state = deepcopy(state)
        self.saved.append(deepcopy(state))


class ForbiddenRepository:
    def __init__(self):
        self.calls = []

    def __getattr__(self, name):
        async def forbidden(*args, **kwargs):
            self.calls.append(name)
            raise AssertionError("runtime repository mutation is forbidden")
        return forbidden


def controller():
    repo = ForbiddenRepository()
    result = ResearchPromotion(None, repo)
    result.store = MemoryStore()
    return result, repo


def test_missing_software_becomes_manual_engineering_requirement_without_git_calls():
    async def run():
        worker, repo = controller()
        state = await worker.tick("problem")
        assert state["phase"] == "ENGINEERING_REQUIRED"
        assert repo.calls == []
        assert state["spec_hash"] == digest(spec())
        requirement = state["engineering_requirement"]
        assert requirement["condition"] == "ENGINEERING_REQUIRED"
        assert requirement["manual_chatgpt_workspace_required"] is True
        assert requirement["runtime_code_mutation_authorized"] is False
        assert requirement["runtime_git_write_authorized"] is False
        assert requirement["runtime_merge_authorized"] is False
        assert requirement["runtime_deploy_authorized"] is False
        assert "Implement ANEVUM engineering requirement" in requirement["handoff_prompt"]

    asyncio.run(run())


def test_engineering_requirement_waits_without_repeated_repo_activity():
    async def run():
        worker, repo = controller()
        await worker.tick("problem")
        state = await worker.tick("problem")
        assert state["phase"] == "ENGINEERING_REQUIRED"
        assert repo.calls == []

    asyncio.run(run())


def test_exact_deployed_manual_implementation_resumes_research(monkeypatch):
    async def run():
        worker, repo = controller()
        await worker.tick("problem")
        worker.store.heartbeat = {
            "source_commit": "manual-commit",
            "deployment_id": "manual-deployment",
            "heartbeat_at": datetime.now(UTC).isoformat(),
        }
        implementation = SimpleNamespace(
            SPEC=spec(),
            SPEC_HASH=digest(spec()),
            evaluate=lambda *args, **kwargs: {},
        )
        import app.graen.research_promotion as promotion

        monkeypatch.setattr(
            promotion,
            "import_module",
            lambda name: implementation,
        )
        state = await worker.tick("problem")
        assert state["phase"] == "COMPLETE"
        assert state["resume_stage"] == "CRYPTO_COMPILED_DEVELOPMENT"
        assert state["manual_resolution"]["source_commit"] == "manual-commit"
        assert state["manual_resolution"]["deployment_id"] == "manual-deployment"
        assert repo.calls == []

    asyncio.run(run())


def test_wrong_or_stale_manual_deployment_cannot_resume(monkeypatch):
    async def run():
        worker, _ = controller()
        await worker.tick("problem")
        worker.store.heartbeat = {
            "source_commit": "manual-commit",
            "deployment_id": "manual-deployment",
            "heartbeat_at": "2020-01-01T00:00:00+00:00",
        }
        implementation = SimpleNamespace(
            SPEC=spec(),
            SPEC_HASH="wrong",
            evaluate=lambda *args, **kwargs: {},
        )
        import app.graen.research_promotion as promotion

        monkeypatch.setattr(promotion, "import_module", lambda name: implementation)
        state = await worker.tick("problem")
        assert state["phase"] == "ENGINEERING_REQUIRED"

    asyncio.run(run())


def test_frozen_prespec_cannot_change_after_handoff():
    async def run():
        worker, _ = controller()
        await worker.tick("problem")
        worker.store.value["parameters"]["volume_z"] = 3
        state = await worker.tick("problem")
        assert state["phase"] == "ENGINEERING_REQUIRED"
        assert state["blocked_reason"] == "frozen_prespec_mutation"

    asyncio.run(run())


def test_exhaustion_routes_inside_existing_control_flow_and_complete_is_terminal():
    snapshot = {
        "problems": [
            {
                "problem_id": "p",
                "status": "WAITING",
                "metadata": {"research_stage": "RESEARCH_IMPLEMENTATION_REQUIRED"},
            }
        ],
        "runs": [
            {
                "problem_id": "p",
                "result_summary": {"decision": "NEEDS_NEW_HYPOTHESIS_ENGINE"},
            }
        ],
    }
    assert list(engineering_problem_ids(snapshot)) == ["p"]
    snapshot["problems"][0]["metadata"]["code_promotion"] = {"phase": "COMPLETE"}
    assert list(engineering_problem_ids(snapshot)) == []


def test_compiled_stage_fetch_is_exclusive_and_rejection_cannot_be_reclaimed(monkeypatch):
    from app.graen import research_executor_service as service
    import importlib

    async def run():
        value = spec()
        result = {
            "stage": "development",
            "passed": False,
            "reasons": ["synthetic_failure"],
            "spec_hash": digest(value),
            "epoch": value["epoch"],
        }
        module = SimpleNamespace(
            SPEC=value,
            SPEC_HASH=digest(value),
            evaluate=lambda *args, **kwargs: result,
        )
        original_import = importlib.import_module
        monkeypatch.setattr(
            importlib,
            "import_module",
            lambda name, *args, **kwargs: (
                module
                if name.startswith("graen.crypto.generated.")
                else original_import(name, *args, **kwargs)
            ),
        )

        class Gateway:
            def __init__(self):
                self.artifacts = []
                self.queued_stages = []

            async def record_artifact(self, **payload):
                self.artifacts.append(payload)
                return {"ok": True, "artifact": {"artifact_id": "artifact"}}

            async def complete_research_problem(self, **payload):
                return {"ok": True}

            async def queue_research_stage(self, **payload):
                self.queued_stages.append(payload)
                return {"ok": True, "problem": {"problem_id": "problem"}}

            async def executor_heartbeat(self, **payload):
                return {"ok": True}

            async def _request(self, method, payload):
                return {"ok": True, "current": None, "predecessor": None}

        requests = []

        async def fetch(symbols, *, start, end):
            requests.append((start, end))
            return {symbol: [] for symbol in symbols}

        runtime = service.GraenResearchExecutor()
        runtime.gateway = Gateway()
        runtime.market_data = SimpleNamespace(historical_crypto_bars_many=fetch)
        runtime.callback_base_url = ""
        runtime.callback_token = ""
        problem = {
            "problem_id": "11111111-1111-1111-1111-111111111111",
            "metadata": {
                "research_stage": "CRYPTO_COMPILED_DEVELOPMENT",
                "compiled_specification_hash": digest(value),
                "code_promotion": {
                    "phase": "COMPLETE",
                    "spec_hash": digest(value),
                    "prespec": value,
                },
            },
        }
        await runtime._execute_compiled_hypothesis(
            problem,
            {"run_id": "22222222-2222-2222-2222-222222222222"},
        )
        _, boundary = stage_window(value, "development")
        assert requests and all(end < boundary for _, end in requests)
        assert (
            runtime.gateway.queued_stages[-1]["stage"]
            == "RESEARCH_IMPLEMENTATION_REQUIRED"
        )
        opened = [
            a
            for a in runtime.gateway.artifacts
            if a["artifact_type"] == "COMPILED_STAGE_OPENED"
        ]
        assert opened[0]["content"]["fetch_end_exclusive"] == boundary.isoformat()

    asyncio.run(run())
