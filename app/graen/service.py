from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from typing import Any, Mapping

import httpx
from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel, Field


UTC = timezone.utc
RUNTIME_VERSION = "graen-runtime-v1.0.0"


def _truthy(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _source_commit() -> str | None:
    return os.getenv("RAILWAY_GIT_COMMIT_SHA") or os.getenv("GRAEN_SOURCE_COMMIT")


def _deployment_id() -> str | None:
    return os.getenv("RAILWAY_DEPLOYMENT_ID")


class ProblemRequest(BaseModel):
    title: str = Field(min_length=1, max_length=240)
    statement: str = Field(min_length=1, max_length=12000)
    domain: str = Field(default="GENERAL_RESEARCH", max_length=80)
    priority: int = Field(default=50, ge=0, le=100)
    source: str = Field(default="IREN", max_length=80)
    requested_by: str | None = Field(default=None, max_length=160)
    linked_iren_job_id: str | None = None
    constraints: dict[str, Any] = Field(default_factory=dict)
    success_criteria: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)


class GraenGateway:
    def __init__(self, url: str, token: str, *, timeout_seconds: float = 15.0):
        self.url = url.strip()
        self.token = token.strip()
        self.timeout_seconds = timeout_seconds

    @property
    def configured(self) -> bool:
        return bool(self.url and len(self.token) >= 32)

    def _headers(self) -> dict[str, str]:
        return {
            "accept": "application/json",
            "content-type": "application/json",
            "x-graen-gateway-token": self.token,
        }

    async def _request(self, method: str, payload: dict[str, Any] | None = None) -> Mapping[str, Any]:
        if not self.configured:
            raise RuntimeError("GRAEN gateway is not configured")
        try:
            async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                if method == "GET":
                    response = await client.get(self.url, headers=self._headers())
                else:
                    response = await client.post(self.url, headers=self._headers(), json=payload or {})
                response.raise_for_status()
                body = response.json()
        except Exception as exc:
            raise RuntimeError(f"GRAEN gateway request failed: {type(exc).__name__}") from exc
        if not isinstance(body, Mapping) or body.get("ok") is not True:
            raise RuntimeError("GRAEN gateway returned an invalid response")
        return body

    async def snapshot(self) -> Mapping[str, Any]:
        return await self._request("GET")

    async def create_problem(self, payload: dict[str, Any]) -> Mapping[str, Any]:
        return await self._request("POST", {"action": "create_problem", **payload})

    async def claim_problem(self, worker_id: str) -> Mapping[str, Any]:
        return await self._request(
            "POST",
            {
                "action": "claim_problem",
                "worker_id": worker_id,
                "runtime_version": RUNTIME_VERSION,
                "source_commit": _source_commit(),
                "deployment_id": _deployment_id(),
            },
        )

    async def queue_research_stage(
        self,
        *,
        problem_id: str,
        stage: str,
        metadata: dict[str, Any] | None = None,
    ) -> Mapping[str, Any]:
        return await self._request(
            "POST",
            {
                "action": "queue_research_stage",
                "problem_id": problem_id,
                "stage": stage,
                "metadata": metadata or {},
            },
        )

    async def claim_research_problem(
        self,
        *,
        worker_id: str,
        runtime_version: str,
        methodology_version: str,
        domain: str,
    ) -> Mapping[str, Any]:
        return await self._request(
            "POST",
            {
                "action": "claim_research_problem",
                "worker_id": worker_id,
                "runtime_version": runtime_version,
                "methodology_version": methodology_version,
                "domain": domain,
                "source_commit": _source_commit(),
                "deployment_id": _deployment_id(),
            },
        )

    async def executor_heartbeat(
        self,
        *,
        worker_id: str,
        runtime_version: str,
        methodology_version: str,
        active_problem_id: str | None,
        last_error: str | None,
    ) -> Mapping[str, Any]:
        return await self._request(
            "POST",
            {
                "action": "executor_heartbeat",
                "worker_id": worker_id,
                "runtime_version": runtime_version,
                "methodology_version": methodology_version,
                "source_commit": _source_commit(),
                "deployment_id": _deployment_id(),
                "active_problem_id": active_problem_id,
                "last_error": last_error,
            },
        )

    async def block_research_claim(
        self,
        *,
        problem_id: str,
        run_id: str,
        worker_id: str,
        error: str,
    ) -> Mapping[str, Any]:
        return await self._request(
            "POST",
            {
                "action": "block_research_claim",
                "problem_id": problem_id,
                "run_id": run_id,
                "worker_id": worker_id,
                "error": error,
            },
        )

    async def complete_research_problem(
        self,
        *,
        problem_id: str,
        run_id: str,
        worker_id: str,
        status: str,
        result_summary: dict[str, Any],
        model_usage: dict[str, Any],
    ) -> Mapping[str, Any]:
        return await self._request(
            "POST",
            {
                "action": "complete_research_problem",
                "problem_id": problem_id,
                "run_id": run_id,
                "worker_id": worker_id,
                "status": status,
                "result_summary": result_summary,
                "model_usage": model_usage,
            },
        )

    async def heartbeat(
        self,
        worker_id: str,
        *,
        active_problem_id: str | None,
        last_error: str | None,
    ) -> Mapping[str, Any]:
        return await self._request(
            "POST",
            {
                "action": "heartbeat",
                "worker_id": worker_id,
                "runtime_version": RUNTIME_VERSION,
                "source_commit": _source_commit(),
                "deployment_id": _deployment_id(),
                "active_problem_id": active_problem_id,
                "last_error": last_error,
            },
        )

    async def record_artifact(
        self,
        *,
        problem_id: str,
        run_id: str | None,
        artifact_type: str,
        content: dict[str, Any],
        methodology_version: str | None = None,
    ) -> Mapping[str, Any]:
        return await self._request(
            "POST",
            {
                "action": "record_artifact",
                "problem_id": problem_id,
                "run_id": run_id,
                "artifact_type": artifact_type,
                "methodology_version": methodology_version,
                "source_commit": _source_commit(),
                "content": content,
            },
        )

    async def complete_problem(
        self,
        *,
        problem_id: str,
        run_id: str,
        status: str,
        result_summary: dict[str, Any],
        model_usage: dict[str, Any],
    ) -> Mapping[str, Any]:
        return await self._request(
            "POST",
            {
                "action": "complete_problem",
                "problem_id": problem_id,
                "run_id": run_id,
                "status": status,
                "result_summary": result_summary,
                "model_usage": model_usage,
            },
        )


class GraenRuntime:
    def __init__(self) -> None:
        self.worker_id = os.getenv("GRAEN_WORKER_ID", "graen-primary").strip() or "graen-primary"
        self.interval_seconds = max(5, int(os.getenv("GRAEN_TICK_SECONDS", "15")))
        self.autorun = _truthy("GRAEN_AUTORUN", True)
        self.gateway = GraenGateway(
            os.getenv("GRAEN_GATEWAY_URL", ""),
            os.getenv("GRAEN_GATEWAY_TOKEN", ""),
        )
        self.stop_event = asyncio.Event()
        self.task: asyncio.Task | None = None
        self.started_at = datetime.now(UTC)
        self.last_heartbeat_at: datetime | None = None
        self.last_claim_at: datetime | None = None
        self.last_completion_at: datetime | None = None
        self.active_problem_id: str | None = None
        self.last_error: str | None = None
        self.last_result: dict[str, Any] | None = None

    def health(self) -> dict[str, Any]:
        running = self.task is not None and not self.task.done()
        ready = self.gateway.configured and running and self.last_error is None
        return {
            "ok": ready,
            "system": "GRAEN",
            "service": "graen",
            "program": "ANEVUM GRAEN",
            "runtime_version": RUNTIME_VERSION,
            "running": running,
            "worker_alive": running,
            "execution_authority": False,
            "broker_orders_possible": False,
            "risk_or_sizing_authority": False,
            "production_promotion_authority": False,
            "model_execution_enabled": False,
            "autorun": self.autorun,
            "gateway_configured": self.gateway.configured,
            "last_error": self.last_error,
            "active_problem_id": self.active_problem_id,
            "last_heartbeat_at": self.last_heartbeat_at.isoformat() if self.last_heartbeat_at else None,
            "last_claim_at": self.last_claim_at.isoformat() if self.last_claim_at else None,
            "last_completion_at": self.last_completion_at.isoformat() if self.last_completion_at else None,
            "last_result": self.last_result,
            "runtime_provenance": {
                "system_version": RUNTIME_VERSION,
                "git_commit": _source_commit(),
                "deployment_id": _deployment_id(),
                "runtime_started_at": self.started_at.isoformat(),
            },
        }

    async def start(self) -> None:
        if self.task is None:
            self.task = asyncio.create_task(self.run(), name="graen-runtime")

    async def stop(self) -> None:
        self.stop_event.set()
        if self.task is not None:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
            self.task = None

    async def run(self) -> None:
        while not self.stop_event.is_set():
            try:
                if self.autorun:
                    await self.process_once()
                else:
                    await self.gateway.heartbeat(
                        self.worker_id,
                        active_problem_id=self.active_problem_id,
                        last_error=self.last_error,
                    )
                    self.last_heartbeat_at = datetime.now(UTC)
                if self.last_error and self.active_problem_id is None:
                    self.last_error = None
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.last_error = f"{type(exc).__name__}: {exc}"[:1000]
                try:
                    await self.gateway.heartbeat(
                        self.worker_id,
                        active_problem_id=self.active_problem_id,
                        last_error=self.last_error,
                    )
                    self.last_heartbeat_at = datetime.now(UTC)
                except Exception:
                    pass
            try:
                await asyncio.wait_for(self.stop_event.wait(), timeout=self.interval_seconds)
            except asyncio.TimeoutError:
                pass

    async def process_once(self) -> dict[str, Any]:
        # Durable engineering is part of GRAEN's existing worker, not a session.
        from .research_promotion import ResearchPromotion, engineering_problem_ids
        snapshot = await self.gateway.snapshot()
        for problem_id in engineering_problem_ids(snapshot):
            await ResearchPromotion(self.gateway).tick(problem_id)
            break  # One bounded external engineering step per runtime tick.
        claimed = await self.gateway.claim_problem(self.worker_id)
        self.last_heartbeat_at = datetime.now(UTC)
        problem = claimed.get("problem")
        run = claimed.get("run")
        if not isinstance(problem, Mapping) or not isinstance(run, Mapping):
            self.active_problem_id = None
            await self.gateway.heartbeat(
                self.worker_id,
                active_problem_id=None,
                last_error=None,
            )
            self.last_heartbeat_at = datetime.now(UTC)
            return {"status": "IDLE", "claimed": False}

        problem_id = str(problem.get("problem_id"))
        run_id = str(run.get("run_id"))
        self.active_problem_id = problem_id
        self.last_claim_at = datetime.now(UTC)

        specification = {
            "schema_version": "graen.problem_specification.v1",
            "problem_id": problem_id,
            "problem_key": problem.get("problem_key"),
            "title": problem.get("title"),
            "statement": problem.get("statement"),
            "domain": problem.get("domain"),
            "priority": problem.get("priority"),
            "constraints": problem.get("constraints") or {},
            "success_criteria": problem.get("success_criteria") or {},
            "source": problem.get("source"),
            "requested_by": problem.get("requested_by"),
            "linked_iren_job_id": problem.get("linked_iren_job_id"),
            "methodology": {
                "research_stages": ["DEVELOPMENT", "VALIDATION", "HOLDOUT"],
                "evidence_first": True,
                "preserve_search_history": True,
                "falsification_required": True,
                "production_authority": False,
                "broker_authority": False,
            },
            "created_at": datetime.now(UTC).isoformat(),
            "source_commit": _source_commit(),
        }
        canonical = json.dumps(specification, sort_keys=True, separators=(",", ":"))
        specification["specification_hash"] = hashlib.sha256(canonical.encode()).hexdigest()

        artifact = await self.gateway.record_artifact(
            problem_id=problem_id,
            run_id=run_id,
            artifact_type="RESEARCH_PROBLEM_SPECIFICATION",
            content=specification,
            methodology_version="graen-intake-v1",
        )

        result = {
            "state": "READY_FOR_RESEARCH_EXECUTOR",
            "problem_specification_artifact": (
                (artifact.get("artifact") or {}).get("artifact_id")
                if isinstance(artifact.get("artifact"), Mapping)
                else None
            ),
            "content_hash": artifact.get("content_hash"),
            "model_invoked": False,
            "execution_authority": False,
            "next_stage": "RESEARCH_EXECUTOR",
        }
        await self.gateway.complete_problem(
            problem_id=problem_id,
            run_id=run_id,
            status="WAITING",
            result_summary=result,
            model_usage={"invoked": False},
        )
        self.active_problem_id = None
        self.last_completion_at = datetime.now(UTC)
        self.last_result = result
        self.last_error = None
        return {"status": "WAITING", "claimed": True, "problem_id": problem_id, **result}


runtime = GraenRuntime()


def _require_admin(value: str | None) -> None:
    expected = os.getenv("GRAEN_ADMIN_TOKEN", "").strip()
    if len(expected) < 32 or value != expected:
        raise HTTPException(status_code=401, detail="unauthorized")


@asynccontextmanager
async def lifespan(_: FastAPI):
    if not runtime.gateway.configured:
        raise RuntimeError("GRAEN gateway is not configured")
    await runtime.start()
    try:
        yield
    finally:
        await runtime.stop()


app = FastAPI(title="ANEVUM GRAEN", version="1.0.0", lifespan=lifespan)


@app.get("/health")
async def health():
    state = runtime.health()
    if not state["ok"]:
        raise HTTPException(status_code=503, detail=state)
    return state


@app.get("/v1/status")
async def status(x_graen_admin_token: str | None = Header(default=None)):
    _require_admin(x_graen_admin_token)
    state = runtime.health()
    try:
        snapshot = await runtime.gateway.snapshot()
    except Exception as exc:
        raise HTTPException(status_code=502, detail=type(exc).__name__) from exc
    return {
        "service": state,
        "queue": {
            "problems": snapshot.get("problems") or [],
            "runs": snapshot.get("runs") or [],
            "artifacts": snapshot.get("artifacts") or [],
            "runtime_state": snapshot.get("runtime_state"),
        },
    }


@app.post("/v1/problems")
async def create_problem(
    request: ProblemRequest,
    x_graen_admin_token: str | None = Header(default=None),
):
    _require_admin(x_graen_admin_token)
    payload = request.model_dump()
    created = await runtime.gateway.create_problem(payload)
    return {"ok": True, **created}


@app.post("/v1/run-once")
async def run_once(x_graen_admin_token: str | None = Header(default=None)):
    _require_admin(x_graen_admin_token)
    return await runtime.process_once()
