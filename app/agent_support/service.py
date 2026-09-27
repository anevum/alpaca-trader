"""Isolated deterministic support service; no trading or model authority."""
from __future__ import annotations

import asyncio
import hmac
import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any

import httpx
from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

from .runtime import SupportRuntime, isolation_violations


class ManualRun(BaseModel):
    railway_status: list[dict[str, Any]] = Field(max_length=12)
    railway_configs: dict[str, dict[str, Any]]
    persist: bool = False


def _runtime(client: httpx.AsyncClient) -> SupportRuntime:
    return SupportRuntime(
        client, gateway_url=os.environ.get("RHEN_SUPPORT_GATEWAY_URL", ""),
        read_token=os.environ.get("RHEN_SUPPORT_READ_TOKEN", ""),
        write_token=os.environ.get("RHEN_SUPPORT_WRITE_TOKEN", ""),
        research_url=os.environ.get("RHEN_SUPPORT_RESEARCH_URL", ""),
        preopen_url=os.environ.get("RHEN_SUPPORT_PREOPEN_URL", ""),
    )


def _require_operator(token: str | None) -> None:
    expected = os.environ.get("RHEN_SUPPORT_OPERATOR_TOKEN", "")
    if len(expected) < 64 or not token or not hmac.compare_digest(expected, token):
        raise HTTPException(status_code=401, detail="unauthorized")


async def _scheduled_loop(stop: asyncio.Event) -> None:
    # Scheduling has a hard dependency on a separately scoped read-only
    # observer. No broad Railway project token may be supplied to this service.
    observer = os.environ.get("RHEN_SUPPORT_OBSERVER_URL", "")
    observer_token = os.environ.get("RHEN_SUPPORT_OBSERVER_READ_TOKEN", "")
    if not observer or not observer_token:
        raise RuntimeError("read-only Railway observer required for scheduling")
    while not stop.is_set():
        try:
            async with httpx.AsyncClient(timeout=20) as client:
                reply = await client.get(observer, headers={"x-rhen-support-observer-token": observer_token})
                reply.raise_for_status()
                data = reply.json()
                if not isinstance(data, dict):
                    raise ValueError("Railway observation malformed")
                await _runtime(client).evaluate(
                    railway_status=data["status"], railway_configs=data["configs"], persist=True)
        except Exception as exc:
            print({"event": "agent_support_run_failed", "error_type": type(exc).__name__}, flush=True)
        try:
            await asyncio.wait_for(stop.wait(), timeout=3600)
        except asyncio.TimeoutError:
            pass


@asynccontextmanager
async def lifespan(_: FastAPI):
    if isolation_violations():
        raise RuntimeError("support service forbidden credential or execution variable")
    stop = asyncio.Event()
    task = None
    if os.environ.get("RHEN_SUPPORT_SCHEDULE_ENABLED", "").lower() == "true":
        if not (os.environ.get("RHEN_SUPPORT_OBSERVER_URL")
                and os.environ.get("RHEN_SUPPORT_OBSERVER_READ_TOKEN")):
            raise RuntimeError("schedule requires a read-only Railway observer")
        task = asyncio.create_task(_scheduled_loop(stop))
    try:
        yield
    finally:
        stop.set()
        if task:
            await task


app = FastAPI(title="RHEN Agent Support v1", version="1.0", lifespan=lifespan)


@app.get("/health")
async def health():
    violations = isolation_violations()
    if violations:
        raise HTTPException(status_code=503, detail="isolation violation")
    return {"ok": True, "service": "rhen-agent-support",
            "schedule_enabled": os.environ.get("RHEN_SUPPORT_SCHEDULE_ENABLED", "").lower() == "true",
            "broker_credentials_present": False, "model_dependency_present": False,
            "trading_execution_authority": False}


@app.post("/v1/run")
async def manual_run(request: ManualRun,
                     x_rhen_support_operator_token: str | None = Header(default=None)):
    _require_operator(x_rhen_support_operator_token)
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            result = await _runtime(client).evaluate(
                railway_status=request.railway_status,
                railway_configs=request.railway_configs,
                persist=request.persist,
                now=datetime.now(timezone.utc))
        print({"event": "agent_support_run", "persisted": request.persist,
               "state": result["integrity"]["state"],
               "applied_actions": result["applied_actions"]}, flush=True)
        return result
    except (httpx.HTTPError, ValueError, KeyError, TypeError, RuntimeError) as exc:
        raise HTTPException(status_code=503, detail=f"support evaluation unavailable: {type(exc).__name__}") from exc
