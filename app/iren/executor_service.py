from __future__ import annotations

import os
from typing import Any

import httpx
from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel, Field


def _enabled(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


class JobEnvelope(BaseModel):
    job_id: str = Field(min_length=1, max_length=128)
    objective_key: str | None = Field(default=None, max_length=240)
    title: str = Field(min_length=1, max_length=240)
    instructions: str = Field(default="", max_length=12000)
    owner_system: str = Field(default="IREN", max_length=80)
    job_type: str = Field(default="AGENT_WORK", max_length=80)
    protected_action: bool = False
    requires_human: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)


class ExecutorRuntime:
    supported_job_types = {"SOFTWARE_BUILD", "GRAEN_RESEARCH_PROBLEM", "AGENT_WORK"}

    @property
    def service_enabled(self) -> bool:
        return _enabled("IREN_EXECUTOR_ENABLED", True)

    @property
    def model_execution_authorized(self) -> bool:
        return _enabled("IREN_MODEL_EXECUTION_AUTHORIZED", False)

    @property
    def token(self) -> str:
        return os.getenv("IREN_EXECUTOR_TOKEN", "").strip()

    @property
    def graen_url(self) -> str:
        return os.getenv("GRAEN_SERVICE_URL", "").strip().rstrip("/")

    @property
    def graen_token(self) -> str:
        return os.getenv("GRAEN_ADMIN_TOKEN", "").strip()

    @property
    def graen_configured(self) -> bool:
        return self.graen_url.startswith("http") and len(self.graen_token) >= 32

    def health(self) -> dict[str, Any]:
        return {
            "ok": True,
            "system": "IREN",
            "service": "iren-executor",
            "version": "iren-executor-v1.0.0",
            "service_enabled": self.service_enabled,
            "model_execution_authorized": self.model_execution_authorized,
            "model_invoked": False,
            "spending_authority": self.model_execution_authorized,
            "supported_job_types": sorted(self.supported_job_types),
            "graen_configured": self.graen_configured,
            "execution_backends": {
                "software_build": "authorization_required",
                "graen_research_problem": "graen_problem_api" if self.graen_configured else "not_configured",
                "agent_work": "external_worker_required",
            },
        }

    def accept(self, job: JobEnvelope) -> dict[str, Any]:
        job_type = job.job_type.strip().upper()
        if job.protected_action or job.requires_human:
            return {
                "accepted": False,
                "job_id": job.job_id,
                "status": "NEEDS_APPROVAL",
                "reason": "protected_action_requires_human_authority",
                "model_invoked": False,
            }
        if job_type not in self.supported_job_types:
            return {
                "accepted": False,
                "job_id": job.job_id,
                "status": "BLOCKED",
                "reason": "unsupported_job_type",
                "job_type": job_type,
                "model_invoked": False,
            }
        if not self.service_enabled:
            return {
                "accepted": False,
                "job_id": job.job_id,
                "status": "WAITING",
                "reason": "iren_executor_disabled",
                "model_invoked": False,
            }
        if job_type == "SOFTWARE_BUILD" and not self.model_execution_authorized:
            return {
                "accepted": True,
                "job_id": job.job_id,
                "status": "NEEDS_APPROVAL",
                "reason": "model_execution_not_authorized",
                "required_authority": "model_api_spending_and_software_execution",
                "model_invoked": False,
            }
        if job_type == "GRAEN_RESEARCH_PROBLEM":
            return {
                "accepted": self.graen_configured,
                "job_id": job.job_id,
                "status": "WAITING" if self.graen_configured else "BLOCKED",
                "reason": "graen_submission_ready" if self.graen_configured else "graen_problem_api_not_configured",
                "model_invoked": False,
            }
        return {
            "accepted": True,
            "job_id": job.job_id,
            "status": "WAITING",
            "reason": "external_execution_backend_not_configured",
            "model_invoked": False,
        }


    async def submit_graen(self, job: JobEnvelope) -> dict[str, Any]:
        if not self.graen_configured:
            return {
                "accepted": False,
                "job_id": job.job_id,
                "status": "BLOCKED",
                "reason": "graen_problem_api_not_configured",
                "model_invoked": False,
            }
        success_criteria = job.metadata.get("success_criteria")
        if not isinstance(success_criteria, dict):
            success_criteria = {}
        payload = {
            "title": job.title,
            "statement": job.instructions,
            "domain": "CRYPTO_STRATEGY_RESEARCH",
            "priority": 100,
            "source": "IREN",
            "requested_by": "iren-executor",
            "linked_iren_job_id": job.job_id,
            "constraints": {
                "research_only": True,
                "production_authority": False,
                "broker_authority": False,
                "risk_or_sizing_authority": False,
                "crypto_execution_enabled": False,
                "falsification_required": True,
                "preserve_search_history": True,
            },
            "success_criteria": success_criteria,
            "metadata": {
                "objective_key": job.objective_key,
                "requested_via": "iren-executor",
            },
        }
        try:
            async with httpx.AsyncClient(timeout=20.0) as client:
                response = await client.post(
                    f"{self.graen_url}/v1/problems",
                    headers={"x-graen-admin-token": self.graen_token},
                    json=payload,
                )
                response.raise_for_status()
                body = response.json()
        except Exception as exc:
            return {
                "accepted": True,
                "job_id": job.job_id,
                "status": "WAITING",
                "reason": "graen_problem_submission_failed",
                "error_type": type(exc).__name__,
                "model_invoked": False,
            }
        problem = body.get("problem") if isinstance(body, dict) else None
        problem = problem if isinstance(problem, dict) else {}
        return {
            "accepted": True,
            "job_id": job.job_id,
            "status": "WAITING",
            "reason": "graen_problem_submitted",
            "graen_problem_id": problem.get("problem_id"),
            "graen_problem_key": problem.get("problem_key"),
            "model_invoked": False,
        }


runtime = ExecutorRuntime()
app = FastAPI(title="IREN Executor Boundary", version="1.0.0")


def _require_token(value: str | None) -> None:
    expected = runtime.token
    if len(expected) < 32 or value != expected:
        raise HTTPException(status_code=401, detail="unauthorized")


@app.get("/health")
async def health():
    return runtime.health()


@app.get("/v1/capabilities")
async def capabilities(x_anevum_scheduler_token: str | None = Header(default=None)):
    _require_token(x_anevum_scheduler_token)
    return runtime.health()


@app.post("/v1/jobs/accept")
async def accept_job(
    job: JobEnvelope,
    x_anevum_scheduler_token: str | None = Header(default=None),
):
    _require_token(x_anevum_scheduler_token)
    accepted = runtime.accept(job)
    if job.job_type.strip().upper() == "GRAEN_RESEARCH_PROBLEM" and accepted.get("accepted"):
        return await runtime.submit_graen(job)
    return accepted
