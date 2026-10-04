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


def test_software_build_is_always_manual_chatgpt_codex_handoff(monkeypatch):
    monkeypatch.setenv("IREN_MODEL_EXECUTION_AUTHORIZED", "true")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-should-not-be-used")
    monkeypatch.setenv("IREN_GITHUB_TOKEN", "g" * 40)
    monkeypatch.setenv("IREN_MODEL_DAILY_BUDGET_USD", "999")
    monkeypatch.setenv("IREN_MODEL_JOB_BUDGET_USD", "999")
    runtime = ExecutorRuntime()

    result = runtime.accept(_job())

    assert runtime.model_execution_authorized is False
    assert runtime.software_backend_configured is False
    assert result["status"] == "NEEDS_APPROVAL"
    assert result["reason"] == "manual_chatgpt_codex_handoff_required"
    assert result["required_authority"] == "operator_software_engineering"
    assert result["handoff_ready"] is True
    assert result["model_invoked"] is False
    assert result["runtime_source_mutation_authorized"] is False


def test_protected_software_build_still_requires_human_authority():
    runtime = ExecutorRuntime()
    result = runtime.accept(_job(protected_action=True))
    assert result["status"] == "NEEDS_APPROVAL"
    assert result["reason"] == "protected_action_requires_human_authority"
    assert result["model_invoked"] is False


def test_worker_write_allowlist_remains_bounded_but_runtime_cannot_use_it():
    runtime = ExecutorRuntime()

    assert runtime._write_path_allowed("app/iren/work.py")
    assert runtime._write_path_allowed("foundation/iren_gateway.py")
    assert runtime._write_path_allowed("tests/test_iren_work.py")
    assert runtime._write_path_allowed("db/migrations/0099_example.sql")

    assert not runtime._write_path_allowed("app/main.py")
    assert not runtime._write_path_allowed("app/trading_engine.py")
    assert not runtime._write_path_allowed("app/iren/trading_strategy.py")
    assert not runtime._write_path_allowed("railway.toml")
    assert runtime.software_backend_configured is False


def test_model_cost_helpers_do_not_grant_spending_authority(monkeypatch):
    monkeypatch.setenv("IREN_MODEL_INPUT_USD_PER_MILLION", "10")
    monkeypatch.setenv("IREN_MODEL_OUTPUT_USD_PER_MILLION", "50")
    runtime = ExecutorRuntime()
    usage = ModelUsage(input_tokens=100_000, output_tokens=10_000, calls=2)

    assert runtime.estimate_cost(usage) == 1.5
    assert runtime.health()["spending_authority"] is False


def test_health_never_exposes_credentials_and_reports_manual_mode(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-secret-value")
    monkeypatch.setenv("IREN_GITHUB_TOKEN", "github-secret-value-xxxxxxxx")
    runtime = ExecutorRuntime()

    body = runtime.health()
    rendered = str(body)

    assert "sk-secret-value" not in rendered
    assert "github-secret-value" not in rendered
    assert body["runtime_source_mutation_authorized"] is False
    assert body["manual_software_handoff_required"] is True
    assert body["execution_backends"]["software_build"] == "manual_chatgpt_codex_handoff"
    assert body["software_worker"]["mode"] == "manual_chatgpt_codex"
    assert body["software_worker"]["runtime_write_authority"] is False
    assert body["software_worker"]["runtime_merge_authority"] is False
    assert body["software_worker"]["runtime_deploy_authority"] is False
    assert body["software_worker"]["runtime_spending_authority"] is False


def test_projected_model_call_cost_helper_is_pure(monkeypatch):
    monkeypatch.setenv("IREN_MODEL_INPUT_USD_PER_MILLION", "10")
    monkeypatch.setenv("IREN_MODEL_OUTPUT_USD_PER_MILLION", "50")
    monkeypatch.setenv("IREN_MODEL_MAX_OUTPUT_TOKENS", "1000")
    runtime = ExecutorRuntime()

    projected = runtime.projected_call_cost("x" * 10_000)

    assert round(projected, 6) == 0.15
    assert runtime.model_execution_authorized is False
