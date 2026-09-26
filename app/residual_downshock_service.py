from __future__ import annotations

import asyncio
import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any

from fastapi import FastAPI

from app.residual_downshock_execution import emit_report_chunks, run_development


state: dict[str, Any] = {
    "system": "RHEN",
    "experiment": "residual-downshock-rebound-v2.1",
    "stage": "development",
    "status": "starting",
    "live_execution_enabled": False,
    "validation_opened": False,
    "holdout_opened": False,
    "quarantine_accessed": False,
}


async def _execute_once() -> None:
    state["status"] = "running"
    state["started_at"] = datetime.now(timezone.utc).isoformat()
    try:
        report = await asyncio.to_thread(
            run_development,
            "research/residual-downshock-rebound-v2.1.json",
            source_commit=os.environ.get("RDR21_SOURCE_COMMIT", "46c2de0d84c736b12f8a3bbeb64ef4ad1d5305cc"),
        )
        emit_report_chunks(report)
        state["status"] = "completed"
        state["verdict"] = report["verdict"]
        state["report_sha256_emitted"] = True
    except Exception as exc:
        state["status"] = "failed"
        state["error"] = f"{type(exc).__name__}: {exc}"
        print(f"RDR21_EXECUTION_ERROR {state['error']}", flush=True)
    finally:
        state["finished_at"] = datetime.now(timezone.utc).isoformat()


@asynccontextmanager
async def lifespan(_: FastAPI):
    task = asyncio.create_task(_execute_once())
    yield
    if not task.done():
        task.cancel()


app = FastAPI(title="RHEN RDR v2.1 one-shot research service", lifespan=lifespan)


@app.get("/")
async def health() -> dict[str, Any]:
    return state
