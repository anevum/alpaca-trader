from __future__ import annotations

from app.iren.executor_service import ExecutorRuntime, JobEnvelope, ModelUsage


def _job(**overrides):
    payload = {
        "job_id": "job-1",
        "objective_key": "iren.model-worker.v1",
        "title": "Improve IREN worker",
        "instructions": "Make a bounded IREN-only change.",
        "owner_system": "IREN",
        "job_type": "SOFTWARE_BUILD",
        "protected_action": False,
        "requires_human": False,
        "metadata": {},
    }
    payload.update(overrides)
    return JobEnvelope(**payload)


def test_software_build_requires_explicit_model_authorization(monkeypatch):
    monkeypatch.delenv("IREN_MODEL_EXECUTION_AUTHORIZED", raising=False)
    runtime = ExecutorRuntime()

    result = runtime.accept(_job())

    assert result["status"] == "NEEDS_APPROVAL"
    assert result["reason"] == "model_execution_not_authorized"
    assert result["model_invoked"] is False


def test_software_build_fails_closed_when_runtime_configuration_missing(monkeypatch):
    monkeypatch.setenv("IREN_MODEL_EXECUTION_AUTHORIZED", "true")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("IREN_GITHUB_TOKEN", raising=False)
    monkeypatch.setenv("IREN_MODEL_DAILY_BUDGET_USD", "0")
    monkeypatch.setenv("IREN_MODEL_JOB_BUDGET_USD", "0")
    runtime = ExecutorRuntime()

    result = runtime.accept(_job())

    assert result["status"] == "WAITING"
    assert result["reason"] == "software_worker_configuration_required"
    assert "OPENAI_API_KEY" in result["missing_configuration"]
    assert "IREN_GITHUB_TOKEN" in result["missing_configuration"]
    assert result["model_invoked"] is False


def test_software_build_rejects_job_budget_above_daily_budget(monkeypatch):
    monkeypatch.setenv("IREN_MODEL_EXECUTION_AUTHORIZED", "true")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-" + "x" * 32)
    monkeypatch.setenv("IREN_GITHUB_TOKEN", "g" * 40)
    monkeypatch.setenv("IREN_MODEL_DAILY_BUDGET_USD", "2")
    monkeypatch.setenv("IREN_MODEL_JOB_BUDGET_USD", "3")
    runtime = ExecutorRuntime()

    result = runtime.accept(_job())

    assert result["status"] == "WAITING"
    assert "IREN_MODEL_JOB_BUDGET_USD<=IREN_MODEL_DAILY_BUDGET_USD" in result["missing_configuration"]
    assert runtime.software_backend_configured is False


def test_protected_software_build_never_invokes_model(monkeypatch):
    monkeypatch.setenv("IREN_MODEL_EXECUTION_AUTHORIZED", "true")
    runtime = ExecutorRuntime()

    result = runtime.accept(_job(protected_action=True))

    assert result["status"] == "NEEDS_APPROVAL"
    assert result["reason"] == "protected_action_requires_human_authority"
    assert result["model_invoked"] is False


def test_worker_write_allowlist_is_iren_scoped():
    runtime = ExecutorRuntime()

    assert runtime._write_path_allowed("app/iren/work.py")
    assert runtime._write_path_allowed("foundation/iren_gateway.py")
    assert runtime._write_path_allowed("tests/test_iren_work.py")
    assert runtime._write_path_allowed("db/migrations/0099_example.sql")

    assert not runtime._write_path_allowed("app/main.py")
    assert not runtime._write_path_allowed("app/trading_engine.py")
    assert not runtime._write_path_allowed("app/iren/trading_strategy.py")
    assert not runtime._write_path_allowed("railway.toml")


def test_model_cost_estimate_uses_configured_rates(monkeypatch):
    monkeypatch.setenv("IREN_MODEL_INPUT_USD_PER_MILLION", "10")
    monkeypatch.setenv("IREN_MODEL_OUTPUT_USD_PER_MILLION", "50")
    runtime = ExecutorRuntime()
    usage = ModelUsage(input_tokens=100_000, output_tokens=10_000, calls=2)

    assert runtime.estimate_cost(usage) == 1.5


def test_health_never_exposes_credentials(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-secret-value")
    monkeypatch.setenv("IREN_GITHUB_TOKEN", "github-secret-value-xxxxxxxx")
    runtime = ExecutorRuntime()

    body = runtime.health()
    rendered = str(body)

    assert "sk-secret-value" not in rendered
    assert "github-secret-value" not in rendered
    assert body["software_worker"]["draft_pr_only"] is True
    assert body["software_worker"]["auto_merge"] is False


def test_projected_model_call_cost_is_preventive(monkeypatch):
    monkeypatch.setenv("IREN_MODEL_INPUT_USD_PER_MILLION", "10")
    monkeypatch.setenv("IREN_MODEL_OUTPUT_USD_PER_MILLION", "50")
    monkeypatch.setenv("IREN_MODEL_MAX_OUTPUT_TOKENS", "1000")
    runtime = ExecutorRuntime()

    projected = runtime.projected_call_cost("x" * 10_000)

    assert projected == 0.15
