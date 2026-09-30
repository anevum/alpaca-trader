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
from .topology import bounded_health, topology, project_status

UTC = timezone.utc
POLICY = json.loads(Path(__file__).with_name("policy.json").read_text())


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
        async with httpx.AsyncClient(timeout=12) as client:
            async def probe(item):
                name = item["id"]
                url = os.getenv(item["url_env"], item["default_url"])
                if name == "RHEN":
                    url = os.getenv(item["url_env"], scheduler.runtime.trader_url.split("/v1/scheduler")[0] + "/health")
                if name == "VELUM":
                    url = os.getenv(item["url_env"], scheduler.runtime.velum_url.split("/v1/scheduler")[0] + "/health")
                try:
                    response = await client.get(url)
                    response.raise_for_status()
                    body = response.json()
                    bounded = bounded_health(name, body)
                    services[name] = bounded
                except Exception as exc:
                    services[name] = {"ok": False, "error_type": type(exc).__name__}
            await asyncio.gather(*(probe(item) for item in POLICY["services"]))
            try:
                response = await client.get(scheduler.runtime.trader_url + "/configuration", headers=scheduler.runtime.scheduler_headers)
                response.raise_for_status()
                config = response.json()
            except Exception:
                config = {}
        recent = await scheduler.runtime.ledger.recent(limit=100)
        rt = scheduler.runtime
        return {"observed_at": datetime.now(UTC).isoformat(), "source_commit": os.getenv("RAILWAY_GIT_COMMIT_SHA"),
            "services": services, "configuration": config, "runs": recent,
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
            print(json.dumps({"event": "iren_observation_committed", "revision": self.revision,
                "state": state["state"], "open_incidents": sum(x["status"] == "OPEN" for x in state["incidents"].values())}), flush=True)

    async def dispatch(self):
        owner = str(uuid4())
        batch = await self.gateway("iren_notifications_claim", owner=owner)
        for event in batch.get("events", []):
            result = await scheduler.runtime.slack.send(event["route"],
                f"*IREN // {event['transition']} // {event['key']}*\n{event['reason']}\nDeterministic supervision; no trading configuration changed.")
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


@asynccontextmanager
async def lifespan(_: FastAPI):
    await scheduler.runtime.start()
    await controller.start()
    try:
        yield
    finally:
        await controller.stop()
        await scheduler.runtime.stop()


app = FastAPI(title="IREN Deterministic Control Plane", version=POLICY["version"], lifespan=lifespan)


@app.get("/health")
async def health():
    # Process readiness differs from the health of the systems being supervised.
    alive = controller.task is not None and not controller.task.done()
    body = {"ok": alive and scheduler.runtime.configured, "system": "IREN", "version": POLICY["version"],
        "durable_state_current": fresh(controller.last_persisted_at, datetime.now(UTC), POLICY["stale_after_seconds"]),
        "control_state": controller.state.get("state", "STARTING"), "last_error": controller.last_error,
        "scheduler_version": scheduler.runtime.scheduler_version, "model_invoked": False,
        "runtime_identity": controller.runtime_identity,
        "last_heartbeat_at": controller.last_persisted_at}
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


# Preserve all existing scheduler URLs and its single durable scheduling authority.
app.include_router(scheduler.app.router)
