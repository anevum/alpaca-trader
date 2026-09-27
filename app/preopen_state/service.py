from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import FastAPI

from .catalog import source_catalog
from .config import get_preopen_settings
from .engine import CHECKPOINTS, OUTCOME_DUE, PreOpenStateEngine, checkpoint_time
from .persistence import PreOpenEventSink

NY = ZoneInfo("America/New_York")
settings = get_preopen_settings()
engine = PreOpenStateEngine(settings)
sink = PreOpenEventSink(settings)
stop_event = asyncio.Event()
worker_task: asyncio.Task | None = None
runtime: dict[str, Any] = {
    "started_at": None,
    "last_snapshot": None,
    "last_outcome": None,
    "last_error": None,
    "processed": [],
}


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
            runtime["last_error"] = None
        except Exception as exc:
            runtime["last_error"] = f"{type(exc).__name__}: {exc}"

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
        worker_task = None


app = FastAPI(title="RHEN Pre-Open State", version="1.0", lifespan=lifespan)


@app.get("/")
async def root() -> dict[str, Any]:
    return {"ok": True, "service": "rhen-preopen-state", "shadow_only": True}


@app.get("/health")
async def health() -> dict[str, Any]:
    return {
        "ok": runtime["last_error"] is None,
        "service": "rhen-preopen-state",
        "enabled": settings.preopen_state_enabled,
        "shadow_only": True,
        "credentials_configured": settings.credentials_configured,
        "persistence_enabled": sink.enabled,
        "last_error": runtime["last_error"],
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
