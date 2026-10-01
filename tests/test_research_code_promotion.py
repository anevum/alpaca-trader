import asyncio
from copy import deepcopy
from datetime import datetime, timezone
import pytest

from graen.engineering import (
    SCHEMA, IntegrityError, ProtectedChange, compile_bundle, digest,
    stage_bars, stage_window, validate_spec, verify_bundle, verify_ci, verify_exposure,
)
from graen.crypto.flow_pressure import flow_signal
from app.graen.research_repository import GitHubRepository, TransportUnavailable, AuthorizationDenied
from app.graen.research_promotion import ResearchPromotion, engineering_problem_ids

UTC = timezone.utc


def spec():
    return {
        "schema_version": SCHEMA, "hypothesis_id": "CRYPTO-FLOW-PRESSURE-002",
        "epoch": "SYNTHETIC-TEST-ONLY", "mechanism": "bar_flow_pressure_v1",
        "universe": ["BTC/USD", "ETH/USD", "SOL/USD"],
        "parameters": {"lookback_bars": 36, "volume_z": 2.0, "trade_count_z": 2.0,
            "range_ratio": 1.5, "body_strength": 0.6, "close_location": 0.8,
            "hold_minutes": 30, "cooldown_minutes": 60},
        "corpus": {
            "development": ["2000-01-01T00:00:00Z", "2000-04-01T00:00:00Z"],
            "validation": ["2000-04-01T00:00:00Z", "2000-06-01T00:00:00Z"],
            "holdout": ["2000-06-01T00:00:00Z", "2000-08-01T00:00:00Z"],
        },
        "gates": {"policy": "activity_shock_v9_frozen_gates", "selection_cost": "high",
            "delay_minutes": 5, "confirmatory_candidates": 1},
        "falsification": "Reject on any frozen stage gate failure.",
        "search_history": ["synthetic fixture; no market data read"],
        "exposure_artifact_id": "exposure-fixture",
        "research_only": True, "execution_authority": False,
    }


def exposure():
    return {"artifact_id": "exposure-fixture", "complete": True, "inspected_intervals": []}


def ci(head="head", passed=True):
    return [{
        "id": 1, "head_sha": head, "event": "pull_request",
        "path": ".github/workflows/ci.yml", "pull_requests": [{"number": 1}],
        "run_number": 1, "status": "completed", "conclusion": "success" if passed else "failure",
        "jobs": [{"name": name, "status": "completed", "conclusion": "success"} for name in
            ("test", "iren-command", "graen-autonomy", "research-promotion")],
    }]


@pytest.mark.parametrize("stage,previous", [("validation", "development"), ("holdout", "validation")])
def test_sealed_stage_requires_exact_frozen_predecessor(stage, previous):
    value = spec()
    good = {"stage": previous, "passed": True, "spec_hash": digest(value),
            "epoch": value["epoch"], "artifact_id": "canonical"}
    stage_window(value, stage, good)
    for key, bad in (("passed", False), ("spec_hash", "other"), ("epoch", "other"), ("stage", "other"), ("artifact_id", "")):
        with pytest.raises(IntegrityError):
            stage_window(value, stage, {**good, key: bad})
    with pytest.raises(IntegrityError):
        stage_window(value, stage)


def test_sealed_data_and_corrupt_prespec_fail_closed():
    value = spec()
    with pytest.raises(IntegrityError):
        verify_exposure(value, {**exposure(), "complete": False})
    with pytest.raises(IntegrityError):
        verify_exposure(value, {**exposure(), "inspected_intervals": [value["corpus"]["holdout"]]})
    value["corpus"]["validation"] = value["corpus"]["development"]
    with pytest.raises(IntegrityError):
        validate_spec(value)
    value = spec()
    value["parameters"]["volume_z"] = float("nan")
    with pytest.raises(IntegrityError):
        validate_spec(value)


def test_provider_inclusive_boundary_is_discarded():
    start, end = stage_window(spec(), "development")
    bars = [{"t": start.isoformat()}, {"t": end.isoformat()}, {"t": "2001-01-01T00:00:00Z"}]
    assert stage_bars(bars, start=start, end=end) == [bars[0]]


@pytest.mark.parametrize("path", [
    "app/strategy.py", "app/risk.py", "app/alpaca_client.py", "app/config.py",
    "app/graen/research_promotion.py", ".github/workflows/ci.yml",
    "graen/crypto/generated/../../app/risk.py", ".env", "Dockerfile",
])
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


def test_ci_requires_all_jobs_exact_head_and_latest_attempt():
    runs = ci()
    assert verify_ci(runs, head="head", pr_number=1)
    assert not verify_ci(runs, head="other", pr_number=1)
    assert not verify_ci(runs, head="head", pr_number=2)
    runs[0]["jobs"].pop()
    assert not verify_ci(runs, head="head", pr_number=1)
    runs = ci()
    failed = {**runs[0], "run_attempt": 2, "conclusion": "failure"}
    assert not verify_ci(runs + [failed], head="head", pr_number=1)


def test_flow_requires_all_five_features_and_strict_history():
    value = spec()
    history = [{"o": 100, "h": 101, "l": 99, "c": 100, "v": 100 + i, "n": 50 + i} for i in range(36)]
    current = {"o": 100, "h": 105, "l": 99, "c": 104.9, "v": 100000, "n": 10000}
    assert flow_signal(current, history, value["parameters"])
    assert flow_signal(current, history[:-1], value["parameters"]) is None
    for key, invalid in (("n", 1), ("v", 1), ("c", 99), ("h", float("nan"))):
        assert flow_signal({**current, key: invalid}, history, value["parameters"]) is None


class MemoryStore:
    def __init__(self):
        self.state = {}
        self.saved = []
        self.value = spec()
        self.heartbeat = {}
        self.revision = 0

    async def claim(self, problem_id, owner):
        return {"claimed": True, "revision": self.revision, "state": deepcopy(self.state),
            "prespec": deepcopy(self.value), "exposure": exposure(),
            "prespec_artifact_id": "frozen" if self.saved else None,
            "executor_heartbeat": self.heartbeat}

    async def save(self, problem_id, owner, revision, state):
        assert revision == self.revision
        self.revision += 1
        self.state = deepcopy(state)
        self.saved.append(deepcopy(state))


class Repository(GitHubRepository):
    def __init__(self):
        super().__init__(token="test-only")
        self.base = "base"
        self.branch_head = "base"
        self.files = None
        self.calls = []
        self.merge_count = 0
        self.primary_error = TransportUnavailable("simulated_primary_outage")
        self.checks = ci()
        self.protected_diff = False

    async def head(self, branch=None):
        return self.base if branch is None else self.branch_head

    async def create_branch(self, branch, base):
        assert base == self.base
        self.calls.append("branch")
        self.branch_head = base

    async def write_primary(self, branch, parent, files, message):
        self.calls.append("primary")
        raise self.primary_error

    async def write_fallback(self, branch, parent, files, message):
        self.calls.append("fallback")
        self.files = files
        self.branch_head = "head"
        return "head"

    async def bundle_matches(self, branch, files):
        return files == self.files

    async def open_pr(self, branch, title, body):
        self.calls.append("pr")
        return 1

    async def pr(self, number):
        return {"head": {"sha": "head"}, "base": {"ref": "iren-runtime-boundary-20260930"},
            "state": "open", "merged": False}

    async def diff(self, number):
        rows = [{"filename": path, "status": "added"} for path in self.files]
        if self.protected_diff:
            rows.append({"filename": "app/risk.py", "status": "modified"})
        return rows

    async def ci(self, head):
        return self.checks

    async def merge(self, number, head):
        assert head == "head"
        self.merge_count += 1
        return {"merged": True, "sha": "merged"}


def controller():
    repo = Repository()
    result = ResearchPromotion(None, repo)
    result.store = MemoryStore()
    return result, repo


def test_primary_outage_fallback_completes_branch_ci_deployment_resume_without_human():
    async def run():
        worker, repo = controller()
        await worker.tick("problem")
        assert not repo.calls  # Prespec persisted before first repository access.
        for _ in range(4):
            await worker.tick("problem")
        assert repo.calls == ["branch", "primary", "fallback", "pr"]
        assert repo.merge_count == 1
        assert worker.store.state["phase"] == "DEPLOY"
        await worker.tick("problem")
        assert worker.store.state["phase"] == "DEPLOY"  # No heartbeat, no resume.
        worker.store.heartbeat = {"source_commit": "merged", "deployment_id": "deployment",
            "heartbeat_at": datetime.now(UTC).isoformat(), "last_error": None}
        await worker.tick("problem")
        await worker.tick("problem")
        state = worker.store.state
        assert state["phase"] == "COMPLETE"
        assert state["resume_stage"] == "CRYPTO_COMPILED_DEVELOPMENT"
        assert state["write_transport"] == "github_git_data_api"
        assert state["spec_hash"] == digest(spec())
        assert all(state[key] for key in ("branch", "base_sha", "head_sha", "pr", "ci", "merge_sha", "deployment_id", "epoch"))
    asyncio.run(run())


def test_authorization_denial_never_uses_fallback():
    async def run():
        worker, repo = controller()
        repo.primary_error = AuthorizationDenied("denied")
        for _ in range(3):
            await worker.tick("problem")
        assert "fallback" not in repo.calls
        assert repo.merge_count == 0
        assert worker.store.state["blocked_reason"] == "denied"
    asyncio.run(run())


@pytest.mark.parametrize("failure", ["ci", "protected"])
def test_failed_ci_or_protected_diff_never_merges(failure):
    async def run():
        worker, repo = controller()
        repo.checks = ci(passed=failure != "ci")
        repo.protected_diff = failure == "protected"
        for _ in range(6):
            await worker.tick("problem")
        assert repo.merge_count == 0
        assert worker.store.state["phase"] == "CI"
    asyncio.run(run())


def test_stale_hypothesis_branch_is_not_used_and_prespec_cannot_change():
    async def run():
        worker, repo = controller()
        await worker.tick("problem")
        await worker.tick("problem")
        repo.base = "new-canonical"
        await worker.tick("problem")
        assert worker.store.state["phase"] == "BRANCH"
        assert "primary" not in repo.calls
        worker.store.value["parameters"]["volume_z"] = 3
        await worker.tick("problem")
        assert worker.store.state["blocked_reason"] == "frozen_prespec_mutation"
        assert "primary" not in repo.calls
    asyncio.run(run())


def test_exhaustion_routes_inside_existing_control_flow():
    snapshot = {"problems": [{"problem_id": "p", "status": "WAITING", "metadata": {}}],
        "runs": [{"problem_id": "p", "result_summary": {"decision": "NEEDS_NEW_HYPOTHESIS_ENGINE"}}]}
    assert list(engineering_problem_ids(snapshot)) == ["p"]
    snapshot["problems"][0]["status"] = "RUNNING"
    assert list(engineering_problem_ids(snapshot)) == []


def test_actual_http_primary_unavailable_uses_git_data_api():
    import httpx
    calls = []

    def respond(request):
        calls.append((request.method, request.url.path))
        path = request.url.path
        if path == "/graphql":
            return httpx.Response(503, json={"message": "temporary outage"})
        if "/git/ref/heads/" in path:
            return httpx.Response(200, json={"object": {"sha": "base"}})
        if path.endswith("/git/commits/base"):
            return httpx.Response(200, json={"tree": {"sha": "tree"}})
        if path.endswith("/git/trees"):
            return httpx.Response(201, json={"sha": "newtree"})
        if path.endswith("/git/commits"):
            return httpx.Response(201, json={"sha": "newhead"})
        if "/git/refs/heads/" in path:
            import json
            assert json.loads(request.content)["force"] is False
            return httpx.Response(200, json={"object": {"sha": "newhead"}})
        raise AssertionError(path)

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            repository = GitHubRepository(token="test-only", client=client)
            head, transport = await repository.write("isolated", "base", compile_bundle(spec()), "test")
            assert head == "newhead"
            assert transport == "github_git_data_api"
        assert ("POST", "/graphql") in calls
        assert any(method == "PATCH" and "/git/refs/" in path for method, path in calls)
    asyncio.run(run())


def test_infrastructure_retry_is_bounded_and_never_changes_frozen_code():
    async def run():
        worker, repo = controller()
        retries = []
        async def retry(run_id):
            retries.append(run_id)
        repo.retry_ci_infrastructure = retry
        repo.checks = ci()
        repo.checks[0]["conclusion"] = "timed_out"
        for _ in range(5):
            await worker.tick("problem")
        assert retries == [1]
        await worker.tick("problem")
        assert retries == [1]  # Same attempt cannot trigger repeated retries.
        repo.checks[0]["run_attempt"] = 2
        await worker.tick("problem")
        repo.checks[0]["run_attempt"] = 3
        await worker.tick("problem")
        assert retries == [1, 1]
        assert worker.store.state["blocked_reason"] == "bounded_ci_infrastructure_retries_exhausted"
        assert repo.merge_count == 0
        assert repo.files == compile_bundle(spec())
    asyncio.run(run())


def test_compiled_stage_fetch_is_exclusive_and_rejection_cannot_be_reclaimed(monkeypatch):
    from types import SimpleNamespace
    from app.graen import research_executor_service as service
    from tests.test_graen_research_executor import FakeGateway

    async def run():
        value = spec()
        result = {"stage": "development", "passed": False, "reasons": ["synthetic_failure"],
            "spec_hash": digest(value), "epoch": value["epoch"]}
        module = SimpleNamespace(SPEC=value, SPEC_HASH=digest(value),
            evaluate=lambda *args, **kwargs: result)
        monkeypatch.setattr("importlib.import_module", lambda *args, **kwargs: module)

        class Gateway(FakeGateway):
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
        problem = {"problem_id": "11111111-1111-1111-1111-111111111111",
            "metadata": {"research_stage": "CRYPTO_COMPILED_DEVELOPMENT",
                "compiled_specification_hash": digest(value),
                "code_promotion": {"phase": "COMPLETE", "spec_hash": digest(value), "prespec": value}}}
        await runtime._execute_compiled_hypothesis(problem, {"run_id": "22222222-2222-2222-2222-222222222222"})
        _, boundary = stage_window(value, "development")
        assert requests and all(end < boundary for _, end in requests)
        assert runtime.gateway.queued_stages[-1]["stage"] == "RESEARCH_IMPLEMENTATION_REQUIRED"
        opened = [a for a in runtime.gateway.artifacts if a["artifact_type"] == "COMPILED_STAGE_OPENED"]
        assert opened[0]["content"]["fetch_end_exclusive"] == boundary.isoformat()
    asyncio.run(run())
