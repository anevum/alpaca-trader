from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timezone
import json
import os
from pathlib import Path
from uuid import uuid4

import httpx
from fastapi import FastAPI, Header, HTTPException

from app import orchestration_scheduler as scheduler
from .core import fresh, identity, reduce_state
from .work import IrenWorkEngine, status_summary
from app.slack_brand import decorate_slack_message
from .topology import bounded_health, topology, project_status

UTC = timezone.utc
POLICY = json.loads(Path(__file__).with_name("policy.json").read_text())


def _runtime_identity_complete(row: dict) -> bool:
    identity = row.get("runtime_identity") if isinstance(row, dict) else {}
    if not isinstance(identity, dict):
        return False
    return all(
        isinstance(identity.get(key), str) and bool(identity[key].strip())
        for key in ("git_commit", "deployment_id")
    )


class IrenController:
    def __init__(self):
        self.state = {}
        self.revision = 0
        self.last_persisted_at = None
        self.last_error = None
        self.stop_event = asyncio.Event()
        self.task = None
        self.lock = asyncio.Lock()
        self.api_verified = False
        self.started_at = datetime.now(UTC).isoformat()
        self.runtime_identity = {
            "version": POLICY["version"],
            "deployment": os.getenv("RAILWAY_DEPLOYMENT_ID"),
            "revision": os.getenv("RAILWAY_GIT_COMMIT_SHA"),
            "started_at": self.started_at,
            "configuration_identity": identity({"policy": POLICY, "registry": scheduler.runtime.registry}),
        }

    async def gateway(self, action: str, **payload):
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.post(scheduler.runtime.ledger.gateway_url,
                headers=scheduler.runtime.ledger.headers, json={"action": action, **payload})
            response.raise_for_status()
            body = response.json()
        if body.get("ok") is not True:
            raise RuntimeError("iren_durable_operation_unconfirmed")
        return body

    async def observe(self):
        services = {}
        provider_inventory = {}
        async with httpx.AsyncClient(timeout=12) as client:
            async def probe(item):
                name = item["id"]
                url = os.getenv(item["url_env"], item["default_url"])
                if name == "RHEN":
                    url = os.getenv(item["url_env"], scheduler.runtime.trader_url.split("/v1/scheduler")[0] + "/health")
                if name == "VELUM":
                    url = os.getenv(item["url_env"], scheduler.runtime.velum_url.split("/v1/scheduler")[0] + "/health")
                last_error: Exception | None = None
                for attempt in range(2):
                    try:
                        response = await client.get(url)
                        response.raise_for_status()
                        body = response.json()
                        services[name] = bounded_health(name, body)
                        return
                    except Exception as exc:
                        last_error = exc
                        if attempt == 0:
                            await asyncio.sleep(0.25)
                services[name] = {
                    "ok": False,
                    "error_type": type(last_error).__name__ if last_error else "UnknownError",
                }
            await asyncio.gather(*(probe(item) for item in POLICY["services"]))
            fallback_targets = ("VELUM", "CRYPTO_EDGE")
            fallback_required = [
                name
                for name in fallback_targets
                if not _runtime_identity_complete(services.get(name, {}))
            ]
            provider_inventory = {
                "schema_version": "iren_provider_inventory.v1",
                "observed_at": datetime.now(UTC).isoformat(),
                "read_only": True,
                "provider_write_authority": False,
                "complete": not fallback_required,
                "services": {},
                "fallback_required": fallback_required,
                "reason": (
                    "runtime_self_report_sufficient"
                    if not fallback_required
                    else "provider_fallback_required"
                ),
            }
            if fallback_required:
                try:
                    executor_url = _executor_root_url(os.getenv("IREN_EXECUTOR_URL", ""))
                    executor_token = os.getenv("IREN_EXECUTOR_TOKEN", "").strip()
                    if not (
                        executor_url.startswith("http")
                        and len(executor_token) >= 32
                    ):
                        raise RuntimeError("provider_fallback_not_configured")
                    response = await client.get(
                        executor_url + "/v1/evidence/runtime-inventory",
                        headers={"x-anevum-scheduler-token": executor_token},
                    )
                    response.raise_for_status()
                    body = response.json()
                    if not (
                        isinstance(body, dict)
                        and body.get("schema_version") == "iren_provider_inventory.v1"
                        and body.get("read_only") is True
                        and body.get("provider_write_authority") is False
                    ):
                        raise RuntimeError("provider_fallback_invalid")
                    provider_inventory = {
                        **body,
                        "fallback_required": fallback_required,
                    }
                except Exception as exc:
                    provider_inventory = {
                        "schema_version": "iren_provider_inventory.v1",
                        "observed_at": datetime.now(UTC).isoformat(),
                        "read_only": True,
                        "provider_write_authority": False,
                        "complete": False,
                        "services": {},
                        "fallback_required": fallback_required,
                        "reason": "provider_fallback_unavailable",
                        "error_type": type(exc).__name__,
                    }
            try:
                response = await client.get(scheduler.runtime.trader_url + "/configuration", headers=scheduler.runtime.scheduler_headers)
                response.raise_for_status()
                config = response.json()
            except Exception:
                config = {}
        recent = await scheduler.runtime.ledger.recent(limit=100)
        rt = scheduler.runtime
        return {"observed_at": datetime.now(UTC).isoformat(), "source_commit": os.getenv("RAILWAY_GIT_COMMIT_SHA"),
            "services": services, "provider_inventory": provider_inventory,
            "configuration": config, "runs": recent,
            "scheduler": {"configured": rt.configured, "version": rt.scheduler_version,
                "started_at": rt.started_at.isoformat(),
                "last_success_at": rt.last_success_at.isoformat() if rt.last_success_at else None,
                "last_error": bool(rt.last_error), "running_job": rt.running_job,
                "next_expected_runs": dict(rt.next_runs)}}

    async def tick(self):
        async with self.lock:
            # Read the authoritative revision each cycle. Restarts preserve hysteresis.
            saved = await self.gateway("iren_read")
            previous = saved.get("state") or {}
            # During rolling deployment, only one observation per minute counts.
            # A replacement process reads the current state instead of accelerating hysteresis.
            if fresh(previous.get("observed_at"), datetime.now(UTC), POLICY["tick_seconds"] - 1):
                self.state = previous
                self.revision = int(saved.get("revision", 0))
                self.last_persisted_at = previous["observed_at"]
                self.last_error = None
                return
            observation = await self.observe()
            state, events = reduce_state(previous, observation, POLICY)
            state["topology"] = topology(observation, state, self.runtime_identity)
            if (
                state["topology"].get("inventory_complete") is not True
                and state.get("state") == "HEALTHY"
            ):
                state["state"] = "DEGRADED"
            written = await self.gateway("iren_commit", expected_revision=saved.get("revision", 0),
                observation_key=identity(observation), state=state, events=events)
            if written.get("committed") is not True:
                raise RuntimeError("iren_revision_conflict")
            self.state = state
            self.revision = written["revision"]
            self.last_persisted_at = datetime.now(UTC).isoformat()
            self.last_error = None
            await self.dispatch()
            if not self.api_verified:
                await self.verify_api()
            open_incident_keys = sorted(
                key
                for key, row in state["incidents"].items()
                if row.get("status") == "OPEN"
            )
            topology_state = state.get("topology") if isinstance(state.get("topology"), dict) else {}
            inventory_gaps = topology_state.get("inventory_gaps") if isinstance(topology_state.get("inventory_gaps"), dict) else {}
            print(json.dumps({"event": "iren_observation_committed", "revision": self.revision,
                "state": state["state"], "open_incidents": len(open_incident_keys),
                "open_incident_keys": open_incident_keys,
                "runtime_inventory_complete": topology_state.get("inventory_complete") is True,
                "runtime_inventory_gap_count": len(inventory_gaps),
                "runtime_inventory_gaps": inventory_gaps}, sort_keys=True), flush=True)

    async def dispatch(self):
        owner = str(uuid4())
        batch = await self.gateway("iren_notifications_claim", owner=owner)
        for event in batch.get("events", []):
            transition = str(event.get("transition") or "").upper()
            severity = str(event.get("severity") or "").lower()
            iren_state = (
                "HEALTHY"
                if transition == "RECOVERED"
                else "INCIDENT"
                if transition == "ESCALATED" or severity == "critical"
                else "DEGRADED"
            )
            message = decorate_slack_message(
                f"*IREN // {event['transition']} // {event['key']}*\n{event['reason']}\nDeterministic supervision; no trading configuration changed.",
                system="IREN",
                iren_state=iren_state,
            )
            result = await scheduler.runtime.slack.send(event["route"], message)
            await self.gateway("iren_notification_complete", event_key=event["event_key"], owner=owner, delivery_status=result)

    async def verify_api(self):
        async with httpx.AsyncClient(timeout=10) as client:
            root = "http://127.0.0.1:" + os.getenv("PORT", "8080")
            unauthorized = await client.get(root + "/v1/iren/status")
            if unauthorized.status_code != 401:
                raise RuntimeError("iren_api_authentication_failed")
            response = await client.get(root + "/v1/iren/status", headers=scheduler.runtime.scheduler_headers)
            response.raise_for_status()
            body = response.json()
            if body.get("stale") is not False or int(body.get("revision", 0)) < self.revision:
                raise RuntimeError("iren_api_durable_state_not_current")
            registry = await client.get(root + "/v1/registry")
            registry.raise_for_status()
            if registry.json().get("scheduler_version") != scheduler.runtime.scheduler_version:
                raise RuntimeError("iren_scheduler_api_not_preserved")
        self.api_verified = True
        print(json.dumps({"event": "iren_api_verified", "unauthenticated_status": 401,
            "authenticated_status": 200, "durable_revision": body["revision"], "scheduler_registry_preserved": True}), flush=True)

    async def run(self):
        while not self.stop_event.is_set():
            try:
                await self.tick()
            except Exception as exc:
                self.last_error = type(exc).__name__
                print(json.dumps({"event": "iren_cycle_failed", "classification": self.last_error}), flush=True)
            try:
                await asyncio.wait_for(self.stop_event.wait(), POLICY["tick_seconds"])
            except asyncio.TimeoutError:
                pass

    async def start(self):
        if self.task is None:
            self.task = asyncio.create_task(self.run(), name="iren-supervisor")

    async def stop(self):
        self.stop_event.set()
        if self.task:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
            self.task = None


controller = IrenController()


async def _work_notify(message: str, context: dict[str, object]) -> None:
    response_url = str(context.get("response_url") or "").strip()
    if response_url.startswith("https://hooks.slack.com/commands/"):
        async with httpx.AsyncClient(timeout=8) as client:
            response = await client.post(
                response_url,
                json={"response_type": "ephemeral", "text": message},
            )
            response.raise_for_status()
        return
    body = decorate_slack_message(
        "*IREN // WORK*\n" + message,
        system="IREN",
        iren_state="HEALTHY",
    )
    await scheduler.runtime.slack.send("iren-control", body)


work_engine = IrenWorkEngine(
    controller.gateway,
    lambda: controller.state,
    notify=_work_notify,
    interval_seconds=float(os.getenv("IREN_WORK_TICK_SECONDS", "5")),
)


@asynccontextmanager
async def lifespan(_: FastAPI):
    await scheduler.runtime.start()
    await controller.start()
    await work_engine.start()
    try:
        yield
    finally:
        await work_engine.stop()
        await controller.stop()
        await scheduler.runtime.stop()


app = FastAPI(title="IREN Deterministic Control Plane", version=POLICY["version"], lifespan=lifespan)


def _executor_root_url(value: str) -> str:
    value = str(value or "").strip().rstrip("/")
    return value.removesuffix("/v1/jobs/accept").rstrip("/")


def _runtime_evidence(state: dict) -> dict:
    topology_state = state.get("topology") if isinstance(state, dict) else {}
    topology_state = topology_state if isinstance(topology_state, dict) else {}
    gaps = topology_state.get("inventory_gaps")
    gaps = gaps if isinstance(gaps, dict) else {}
    required = topology_state.get("required_inventory")
    required = required if isinstance(required, list) else []
    return {
        "schema_version": "iren_runtime_evidence.v1",
        "complete_deployment_inventory": topology_state.get("inventory_complete") is True,
        "required_inventory": list(required),
        "inventory_gaps": {str(key): list(value) for key, value in gaps.items() if isinstance(value, list)},
        "inventory_verified_at": topology_state.get("inventory_verified_at"),
        "observation_source": topology_state.get("inventory_source"),
    }


@app.get("/health")
async def health():
    # Process readiness differs from the health of the systems being supervised.
    alive = controller.task is not None and not controller.task.done()
    work_alive = work_engine.task is not None and not work_engine.task.done()
    body = {"ok": alive and work_alive and scheduler.runtime.configured, "system": "IREN", "version": POLICY["version"],
        "durable_state_current": fresh(controller.last_persisted_at, datetime.now(UTC), POLICY["stale_after_seconds"]),
        "control_state": controller.state.get("state", "STARTING"), "last_error": controller.last_error,
        "scheduler_version": scheduler.runtime.scheduler_version, "model_invoked": False,
        "runtime_identity": controller.runtime_identity,
        "runtime_evidence": _runtime_evidence(controller.state),
        "last_heartbeat_at": controller.last_persisted_at,
        "codex_handoff": {"version": "v1", "paid_execution": False, "auto_merge": False},
        "work_engine": {"running": work_alive, "last_error": work_engine.last_error,
            "last_command_at": work_engine.last_command_at, "last_job_at": work_engine.last_job_at,
            "last_autopilot_at": work_engine.last_autopilot_at,
            "last_autopilot_reason": work_engine.last_autopilot_reason,
            "execution_router_configured": bool(work_engine.executor_url and len(work_engine.executor_token) >= 32)}}
    if not body["ok"]:
        raise HTTPException(status_code=503, detail=body)
    return body


@app.get("/ready")
async def ready():
    current = await health()
    if not current["durable_state_current"]:
        raise HTTPException(status_code=503, detail="durable_observation_stale")
    return current


@app.get("/v1/iren/status")
async def status(x_anevum_scheduler_token: str | None = Header(default=None)):
    scheduler._require_scheduler_token(x_anevum_scheduler_token)
    saved = await controller.gateway("iren_read")
    state = saved.get("state") or {}
    return {**project_status(saved, datetime.now(UTC), POLICY["stale_after_seconds"]),
        "local_last_error": controller.last_error}


@app.get("/v1/iren/policy")
async def policy(x_anevum_scheduler_token: str | None = Header(default=None)):
    scheduler._require_scheduler_token(x_anevum_scheduler_token)
    return POLICY


@app.get("/v1/iren/work")
async def work_status(x_anevum_scheduler_token: str | None = Header(default=None)):
    scheduler._require_scheduler_token(x_anevum_scheduler_token)
    snapshot = await work_engine.snapshot()
    return {
        "schema_version": "iren_work.v1",
        "summary": status_summary(snapshot, controller.state),
        **snapshot,
    }


@app.post("/v1/iren/commands")
async def create_command(
    body: dict,
    x_anevum_scheduler_token: str | None = Header(default=None),
):
    scheduler._require_scheduler_token(x_anevum_scheduler_token)
    command = str(body.get("command") or "").strip()
    source = str(body.get("source") or "api").strip()
    requested_by = str(body.get("requested_by") or "operator").strip()
    if not command:
        raise HTTPException(status_code=400, detail="command_required")
    created = await work_engine.enqueue_command(
        command,
        source=source,
        requested_by=requested_by,
    )
    return {"ok": True, **created}


@app.post("/v1/iren/jobs/{job_id}/callback")
async def job_callback(
    job_id: str,
    body: dict,
    x_anevum_scheduler_token: str | None = Header(default=None),
):
    scheduler._require_scheduler_token(x_anevum_scheduler_token)
    status = str(body.get("status") or "").strip().upper()
    if status not in {"WAITING", "BLOCKED", "NEEDS_APPROVAL", "SUCCEEDED", "FAILED"}:
        raise HTTPException(status_code=400, detail="invalid_job_status")
    snapshot = await work_engine.snapshot()
    job = next((row for row in snapshot.get("jobs") or [] if str(row.get("job_id")) == job_id), None)
    if job is None:
        raise HTTPException(status_code=404, detail="job_not_found")
    if job.get("job_type") == "CODEX_HANDOFF":
        raise HTTPException(status_code=409, detail="handoff_requires_independent_verifier")
    if bool(job.get("protected_action")) and status in {"SUCCEEDED"}:
        raise HTTPException(status_code=409, detail="protected_job_requires_human_authority")
    result = body.get("result") if isinstance(body.get("result"), dict) else {}
    error = body.get("error") if isinstance(body.get("error"), dict) else {}
    updated = await controller.gateway(
        "iren_job_update",
        job_id=job_id,
        status=status,
        result=result,
        error=error,
    )

    requirement = result.get("engineering_requirement")
    if (
        result.get("condition") == "ENGINEERING_REQUIRED"
        and isinstance(requirement, dict)
    ):
        requirement_id = str(requirement.get("requirement_id") or "").strip()
        if requirement_id:
            safe_id = "".join(
                ch.lower() if ch.isalnum() else "-"
                for ch in requirement_id
            ).strip("-")[:180]
            suggested_paths = [
                str(path)
                for path in requirement.get("suggested_paths") or []
                if isinstance(path, str) and path.strip()
            ]
            objective = {
                "objective_key": "engineering." + safe_id,
                "title": str(
                    requirement.get("title")
                    or "Implement GRAEN research capability"
                )[:240],
                "description": str(
                    requirement.get("handoff_prompt")
                    or requirement.get("reason")
                    or "Manual research software update required."
                )[:12000],
                "status": "READY",
                "owner_system": "IREN",
                "priority": 85,
                "dependencies": [],
                "success_criteria": {
                    "manual_software_handoff": True,
                    "protected_authority": False,
                },
                "protected_action": False,
                "metadata": {
                    "classification": "ENGINEERING_REQUIRED",
                    "source": "GRAEN",
                    "job_type": "SOFTWARE_BUILD",
                    "manual_software_required": True,
                    "engineering_requirement": requirement,
                    "graen_problem_id": result.get("graen_problem_id"),
                    "codex_scope": {
                        "allowed_paths": suggested_paths,
                        "expected_services": ["GRAEN", "GRAEN_EXECUTOR"],
                        "verification_checks": {},
                    },
                },
            }
            await controller.gateway(
                "iren_objective_create",
                objective=objective,
            )

    if (
        result.get("engineering_requirement_resolved") is True
        and result.get("graen_problem_id")
    ):
        # Resolution is still independently verified by the Codex handoff
        # verifier when a handoff exists. This callback only updates the linked
        # research job; it never bypasses that verifier.
        pass

    return {"ok": True, **updated}


# Preserve all existing scheduler URLs and its single durable scheduling authority.
app.include_router(scheduler.app.router)
