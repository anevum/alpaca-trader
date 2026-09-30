from __future__ import annotations

import asyncio

import httpx

from app.iren.executor_service import JobEnvelope, app, runtime
from app.iren.work import IrenWorkEngine


def software_job(**overrides):
    value = {
        "job_id": "job-1",
        "objective_key": "IREN-COMMAND-SURFACE",
        "title": "Build Command",
        "instructions": "Implement the requested software change.",
        "owner_system": "IREN",
        "job_type": "SOFTWARE_BUILD",
        "protected_action": False,
        "requires_human": False,
        "metadata": {},
    }
    value.update(overrides)
    return value


def test_software_build_cannot_spend_without_authority(monkeypatch):
    monkeypatch.setenv("IREN_EXECUTOR_ENABLED", "true")
    monkeypatch.delenv("IREN_MODEL_EXECUTION_AUTHORIZED", raising=False)
    result = runtime.accept(JobEnvelope(**software_job()))
    assert result["status"] == "NEEDS_APPROVAL"
    assert result["model_invoked"] is False
    assert result["required_authority"] == "model_api_spending_and_software_execution"


def test_protected_job_never_executes(monkeypatch):
    monkeypatch.setenv("IREN_MODEL_EXECUTION_AUTHORIZED", "true")
    result = runtime.accept(JobEnvelope(**software_job(protected_action=True)))
    assert result["status"] == "NEEDS_APPROVAL"
    assert result["reason"] == "protected_action_requires_human_authority"


def test_unknown_job_type_is_blocked():
    result = runtime.accept(JobEnvelope(**software_job(job_type="CAPITAL_TRANSFER")))
    assert result["status"] == "BLOCKED"
    assert result["model_invoked"] is False


def test_executor_api_requires_scheduler_token(monkeypatch):
    monkeypatch.setenv("IREN_EXECUTOR_TOKEN", "a" * 40)

    async def scenario():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://executor",
        ) as client:
            assert (await client.get("/health")).status_code == 200
            assert (await client.get("/v1/capabilities")).status_code == 401
            ok = await client.get(
                "/v1/capabilities",
                headers={"x-anevum-scheduler-token": "a" * 40},
            )
            assert ok.status_code == 200
            assert ok.json()["model_invoked"] is False
    asyncio.run(scenario())


def test_work_engine_routes_job_and_persists_executor_state(monkeypatch):
    updates = []

    async def gateway(action, **payload):
        if action == "iren_job_update":
            updates.append(payload)
            return {"job": payload}
        return {}

    factory = httpx.AsyncClient

    def handler(request):
        assert request.headers["x-anevum-scheduler-token"] == "b" * 40
        return httpx.Response(
            200,
            json={
                "accepted": True,
                "job_id": "job-1",
                "status": "NEEDS_APPROVAL",
                "reason": "model_execution_not_authorized",
                "model_invoked": False,
            },
        )

    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kwargs: factory(
            transport=httpx.MockTransport(handler),
            **kwargs,
        ),
    )

    async def scenario():
        engine = IrenWorkEngine(gateway, lambda: {"state": "HEALTHY"})
        engine.executor_url = "http://executor/v1/jobs/accept"
        engine.executor_token = "b" * 40
        await engine._route_job(software_job())
    asyncio.run(scenario())

    assert updates[-1]["status"] == "NEEDS_APPROVAL"
    routed = updates[-1]["result"]["execution_router"]
    assert routed["reason"] == "model_execution_not_authorized"
