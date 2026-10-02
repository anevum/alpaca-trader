from __future__ import annotations

import asyncio
import json
import os
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from typing import Any
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from fastapi import FastAPI, HTTPException
from app.slack_notifier import SlackNotifier

from .catalog import source_catalog
from .config import get_preopen_settings
from .engine import CHECKPOINTS, OUTCOME_DUE, PreOpenStateEngine, checkpoint_time
from .persistence import PreOpenEventSink

NY = ZoneInfo("America/New_York")
settings = get_preopen_settings()
engine = PreOpenStateEngine(settings)
sink = PreOpenEventSink(settings)
notifications = SlackNotifier(SimpleNamespace(slack_webhook_url=os.environ.get("SLACK_WEBHOOK_URL", "")))
stop_event = asyncio.Event()
worker_task: asyncio.Task | None = None
runtime: dict[str, Any] = {
    "started_at": None,
    "last_snapshot": None,
    "last_outcome": None,
    "last_error": None,
    "processed": [],
    "last_tick_at": None,
}


def _operation(action: str, message: str) -> None:
    event = {"kind": "preopen_state", "action": action, "message": message,
             "at": datetime.now(NY).isoformat()}
    print(json.dumps({"event": "rhen_preopen_operation", **event}), flush=True)
    notifications.record_event(event)


def _confirmed(result: dict) -> None:
    if result.get("ok") is not True:
        raise RuntimeError("preopen_persistence_unconfirmed")
    if runtime["last_error"]:
        _operation("recovered", "Pre-open canonical persistence recovered.")
    runtime["last_error"] = None


def _due(now: datetime, label: str, grace_minutes: int) -> bool:
    scheduled = checkpoint_time(now.date(), label)
    return scheduled <= now <= scheduled + timedelta(minutes=grace_minutes)


async def _capture_snapshot(label: str, now: datetime) -> None:
    payload = await engine.snapshot(checkpoint=label, observed_at=now)
    result = await sink.emit(
        event_type="preopen_state_snapshot",
        event_key=payload["snapshot_key"],
        payload=payload,
        occurred_at=now.isoformat(),
    )
    _confirmed(result)
    runtime["last_snapshot"] = {
        "snapshot_key": payload["snapshot_key"],
        "checkpoint": label,
        "data_quality_state": payload["data_quality_state"],
        "persistence": result,
    }


async def _capture_outcome(horizon: int, now: datetime) -> None:
    payload = await engine.outcome(horizon_minutes=horizon, observed_at=now)
    result = await sink.emit(
        event_type="preopen_state_outcome",
        event_key=payload["outcome_key"],
        payload=payload,
        occurred_at=now.isoformat(),
    )
    _confirmed(result)
    runtime["last_outcome"] = {
        "outcome_key": payload["outcome_key"],
        "horizon_minutes": horizon,
        "persistence": result,
    }


async def _worker() -> None:
    processed: set[str] = set()
    runtime["started_at"] = datetime.now(NY).isoformat()
    while not stop_event.is_set():
        now = datetime.now(NY)
        runtime["last_tick_at"] = now.isoformat()
        processed = {key for key in processed if key.startswith(now.date().isoformat())}
        try:
            if settings.preopen_state_enabled and now.weekday() < 5:
                grace = max(1, settings.preopen_capture_grace_minutes)
                for label in CHECKPOINTS:
                    key = f"{now.date().isoformat()}:snapshot:{label}"
                    if key not in processed and _due(now, label, grace):
                        await _capture_snapshot(label, now)
                        processed.add(key)

                for horizon, label in OUTCOME_DUE.items():
                    key = f"{now.date().isoformat()}:outcome:{horizon}"
                    if key not in processed and _due(now, label, grace):
                        await _capture_outcome(horizon, now)
                        processed.add(key)

                runtime["processed"] = sorted(processed)[-20:]
        except Exception as exc:
            runtime["last_error"] = type(exc).__name__
            _operation("error", "Pre-open capture or persistence failed; checkpoint remains retryable.")

        try:
            await asyncio.wait_for(
                stop_event.wait(),
                timeout=max(5, settings.preopen_poll_seconds),
            )
        except asyncio.TimeoutError:
            pass


@asynccontextmanager
async def lifespan(_: FastAPI):
    global worker_task
    stop_event.clear()
    await notifications.start()
    worker_task = asyncio.create_task(_worker())
    try:
        yield
    finally:
        stop_event.set()
        if worker_task is not None:
            try:
                await asyncio.wait_for(worker_task, timeout=5)
            except asyncio.TimeoutError:
                worker_task.cancel()
                await asyncio.gather(worker_task, return_exceptions=True)
        worker_task = None
        await notifications.stop()


app = FastAPI(title="RHEN Pre-Open State", version="1.0", lifespan=lifespan)


@app.get("/")
async def root() -> dict[str, Any]:
    return {"ok": True, "service": "rhen-preopen-state", "shadow_only": True}


@app.get("/health")
async def health() -> dict[str, Any]:
    stamp = runtime["last_tick_at"]
    age = (datetime.now(NY) - datetime.fromisoformat(stamp)).total_seconds() if stamp else None
    alive = worker_task is not None and not worker_task.done() and age is not None and age < max(120, settings.preopen_poll_seconds * 4)
    if not alive:
        raise HTTPException(status_code=503, detail="preopen_worker_not_live")
    return {
        "ok": runtime["last_error"] is None,
        "service": "rhen-preopen-state",
        "enabled": settings.preopen_state_enabled,
        "shadow_only": True,
        "credentials_configured": settings.credentials_configured,
        "persistence_enabled": sink.enabled,
        "last_error": runtime["last_error"],
        "worker_alive": alive,
        "last_tick_age_seconds": age,
        "runtime_provenance": {
            "system_version": "rhen-preopen-state-v1.0",
            "git_commit": os.environ.get("RAILWAY_GIT_COMMIT_SHA") or None,
            "deployment_id": os.environ.get("RAILWAY_DEPLOYMENT_ID") or None,
            "runtime_started_at": runtime.get("started_at"),
        },
        "slack_notifications": notifications.status(),
    }


@app.get("/v1/status")
async def status() -> dict[str, Any]:
    return {
        "service": "rhen-preopen-state",
        "enabled": settings.preopen_state_enabled,
        "shadow_only": True,
        "feed": settings.data_feed,
        "targets": list(settings.target_symbols),
        "proxies": list(settings.proxy_symbols),
        "checkpoints": list(CHECKPOINTS),
        "outcome_horizons": list(OUTCOME_DUE),
        "runtime": runtime,
        "source_catalog": [item.to_dict() for item in source_catalog()],
        "model": {
            "configured": engine.model is not None,
            "error": engine.model_error,
            "execution_authority": False,
        },
    }
