from __future__ import annotations

import os
from typing import Any

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
            "execution_backends": {
                "software_build": "authorization_required",
                "graen_research_problem": "waiting_for_graen_runtime",
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
                "accepted": True,
                "job_id": job.job_id,
                "status": "WAITING",
                "reason": "graen_independent_runtime_required",
                "model_invoked": False,
            }
        return {
            "accepted": True,
            "job_id": job.job_id,
            "status": "WAITING",
            "reason": "external_execution_backend_not_configured",
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
    return runtime.accept(job)
