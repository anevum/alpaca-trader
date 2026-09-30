from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
import os
from typing import Any, Mapping

import httpx
from fastapi import FastAPI, HTTPException

from app.config import Settings, get_settings
from app.market_data import MarketDataClient
from app.graen.service import GraenGateway
from graen.crypto.research_v7 import (
    CONTEXT_UNIVERSE,
    METHODOLOGY_VERSION as V7_METHODOLOGY_VERSION,
    run_crypto_research_v7,
)
from graen.crypto.leadlag_r2 import (
    UNIVERSE as LEADLAG_UNIVERSE,
    METHODOLOGY_VERSION as LEADLAG_METHODOLOGY_VERSION,
    research_specification as leadlag_research_specification,
    evaluate_stage as evaluate_leadlag_stage,
    development_gate as leadlag_development_gate,
    validation_gate as leadlag_validation_gate,
    holdout_gate as leadlag_holdout_gate,
)


UTC = timezone.utc
RUNTIME_VERSION = "graen-research-executor-v1.1.0"
PROBLEM_DOMAIN = "CRYPTO_STRATEGY_RESEARCH"

DEVELOPMENT_START = datetime(2025, 5, 1, tzinfo=UTC)
VALIDATION_START = datetime(2025, 7, 1, tzinfo=UTC)
HOLDOUT_START = datetime(2025, 8, 1, tzinfo=UTC)
HOLDOUT_END = datetime(2025, 9, 1, tzinfo=UTC)

PREVIOUSLY_INSPECTED_RANGES = (
    {
        "id": "graen-crypto-v6",
        "start": "2025-09-01T00:00:00+00:00",
        "end": "2025-12-01T00:00:00+00:00",
    },
    {
        "id": "crypto-v5-fetch",
        "start": "2026-01-31T16:00:00+00:00",
        "end": "2026-06-01T00:00:00+00:00",
    },
    {
        "id": "crypto-v4",
        "start": "2026-06-02T01:53:00+00:00",
        "end": "2026-07-02T01:53:00+00:00",
    },
    {
        "id": "crypto-v3",
        "start": "2026-07-02T01:53:00+00:00",
        "end": "2026-08-01T01:53:00+00:00",
    },
    {
        "id": "crypto-v2.1",
        "start": "2026-08-01T01:53:00+00:00",
        "end": "2026-08-31T01:53:00+00:00",
    },
    {
        "id": "crypto-v1",
        "start": "2026-08-31T01:53:00+00:00",
        "end": "2026-09-30T01:53:00+00:00",
    },
)


def _truthy(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _source_commit() -> str | None:
    return os.getenv("RAILWAY_GIT_COMMIT_SHA") or os.getenv("GRAEN_RESEARCH_SOURCE_COMMIT")


def _deployment_id() -> str | None:
    return os.getenv("RAILWAY_DEPLOYMENT_ID")


def _research_settings(settings: Settings) -> Settings:
    return settings.model_copy(
        update={
            "bar_timeframe": "5Min",
            "market_data_batch_size": len(CONTEXT_UNIVERSE),
            "dynamic_universe_enabled": False,
            "execution_enabled": False,
            "live_trading": False,
            "bot_armed": False,
            "scan_only": True,
            "crypto_execution_enabled": False,
        }
    )


def _execution_violations(settings: Settings) -> list[str]:
    violations: list[str] = []
    if bool(settings.execution_enabled):
        violations.append("EXECUTION_ENABLED")
    if bool(settings.live_trading):
        violations.append("LIVE_TRADING")
    if bool(settings.bot_armed):
        violations.append("BOT_ARMED")
    if bool(settings.crypto_execution_enabled):
        violations.append("CRYPTO_EXECUTION_ENABLED")
    return violations


class GraenResearchExecutor:
    def __init__(self) -> None:
        self.worker_id = os.getenv(
            "GRAEN_RESEARCH_WORKER_ID",
            "graen-crypto-research-executor",
        ).strip() or "graen-crypto-research-executor"
        self.interval_seconds = max(
            15,
            int(os.getenv("GRAEN_RESEARCH_TICK_SECONDS", "30")),
        )
        self.autorun = _truthy("GRAEN_RESEARCH_AUTORUN", True)
        self.settings = _research_settings(get_settings())
        self.market_data = MarketDataClient(self.settings)
        self.gateway = GraenGateway(
            os.getenv("GRAEN_GATEWAY_URL", ""),
            os.getenv("GRAEN_GATEWAY_TOKEN", ""),
            timeout_seconds=30.0,
        )
        self.callback_base_url = os.getenv("IREN_CALLBACK_BASE_URL", "").strip().rstrip("/")
        self.callback_token = os.getenv("IREN_CALLBACK_TOKEN", "").strip()
        self.stop_event = asyncio.Event()
        self.task: asyncio.Task | None = None
        self.started_at = datetime.now(UTC)
        self.last_heartbeat_at: datetime | None = None
        self.last_claim_at: datetime | None = None
        self.last_completion_at: datetime | None = None
        self.active_problem_id: str | None = None
        self.last_error: str | None = None
        self.last_result: dict[str, Any] | None = None
        self.active_methodology_version = V7_METHODOLOGY_VERSION

    @property
    def callback_configured(self) -> bool:
        return bool(self.callback_base_url.startswith("http") and len(self.callback_token) >= 32)

    def health(self) -> dict[str, Any]:
        running = self.task is not None and not self.task.done()
        violations = _execution_violations(self.settings)
        return {
            "ok": bool(
                running
                and self.gateway.configured
                and self.settings.credentials_configured
                and not violations
                and self.last_error is None
            ),
            "system": "GRAEN",
            "service": "graen-research-executor",
            "runtime_version": RUNTIME_VERSION,
            "methodology_version": self.active_methodology_version,
            "supported_methodologies": [V7_METHODOLOGY_VERSION, LEADLAG_METHODOLOGY_VERSION],
            "running": running,
            "autorun": self.autorun,
            "market_data_credentials_configured": bool(self.settings.credentials_configured),
            "gateway_configured": self.gateway.configured,
            "iren_callback_configured": self.callback_configured,
            "execution_authority": False,
            "broker_orders_possible": False,
            "risk_or_sizing_authority": False,
            "production_promotion_authority": False,
            "crypto_execution_enabled": False,
            "model_execution_enabled": False,
            "isolation_violations": violations,
            "active_problem_id": self.active_problem_id,
            "last_heartbeat_at": self.last_heartbeat_at.isoformat() if self.last_heartbeat_at else None,
            "last_claim_at": self.last_claim_at.isoformat() if self.last_claim_at else None,
            "last_completion_at": self.last_completion_at.isoformat() if self.last_completion_at else None,
            "last_error": self.last_error,
            "last_result": self.last_result,
            "research_window": {
                "development_start": DEVELOPMENT_START.isoformat(),
                "validation_start": VALIDATION_START.isoformat(),
                "holdout_start": HOLDOUT_START.isoformat(),
                "holdout_end": HOLDOUT_END.isoformat(),
            },
            "runtime_provenance": {
                "git_commit": _source_commit(),
                "deployment_id": _deployment_id(),
                "started_at": self.started_at.isoformat(),
            },
        }

    async def start(self) -> None:
        if self.task is None:
            self.task = asyncio.create_task(self.run(), name="graen-research-executor")

    async def stop(self) -> None:
        self.stop_event.set()
        if self.task is not None:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
            self.task = None

    async def _heartbeat(self) -> None:
        await self.gateway.executor_heartbeat(
            worker_id=self.worker_id,
            runtime_version=RUNTIME_VERSION,
            methodology_version=self.active_methodology_version,
            active_problem_id=self.active_problem_id,
            last_error=self.last_error,
        )
        self.last_heartbeat_at = datetime.now(UTC)

    async def run(self) -> None:
        while not self.stop_event.is_set():
            try:
                if self.autorun:
                    await self.process_once()
                else:
                    await self._heartbeat()
                if self.last_error and self.active_problem_id is None:
                    self.last_error = None
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.last_error = f"{type(exc).__name__}: {exc}"[:1000]
                try:
                    await self._heartbeat()
                except Exception:
                    pass
            try:
                await asyncio.wait_for(self.stop_event.wait(), timeout=self.interval_seconds)
            except asyncio.TimeoutError:
                pass

    async def _fetch_corpus(self) -> dict[str, list[dict[str, Any]]]:
        fetch_start = DEVELOPMENT_START - timedelta(hours=8)
        fetch_end = HOLDOUT_END + timedelta(hours=3)
        raw: dict[str, list[dict[str, Any]]] = {
            symbol: [] for symbol in CONTEXT_UNIVERSE
        }
        chunk_start = fetch_start
        while chunk_start < fetch_end:
            chunk_end = min(chunk_start + timedelta(days=20), fetch_end)
            chunk = await self.market_data.historical_crypto_bars_many(
                list(CONTEXT_UNIVERSE),
                start=chunk_start,
                end=chunk_end,
            )
            for symbol in CONTEXT_UNIVERSE:
                raw[symbol].extend(chunk.get(symbol, []))
            chunk_start = chunk_end

        clean: dict[str, list[dict[str, Any]]] = {}
        for symbol in CONTEXT_UNIVERSE:
            seen: set[str] = set()
            rows: list[dict[str, Any]] = []
            for bar in raw.get(symbol, []):
                identity = str(bar.get("t") or "")
                if not identity or identity in seen:
                    continue
                seen.add(identity)
                try:
                    stamp = datetime.fromisoformat(identity.replace("Z", "+00:00"))
                except ValueError:
                    continue
                if stamp.tzinfo is None:
                    stamp = stamp.replace(tzinfo=UTC)
                stamp = stamp.astimezone(UTC)
                if fetch_start <= stamp < fetch_end:
                    rows.append(bar)
            clean[symbol] = rows
        return clean

    async def _callback_iren(
        self,
        linked_job_id: str | None,
        *,
        status: str,
        result: dict[str, Any],
    ) -> bool:
        if not linked_job_id or not self.callback_configured:
            return False
        try:
            async with httpx.AsyncClient(timeout=20.0) as client:
                response = await client.post(
                    f"{self.callback_base_url}/v1/iren/jobs/{linked_job_id}/callback",
                    headers={"x-anevum-scheduler-token": self.callback_token},
                    json={"status": status, "result": result, "error": {}},
                )
                response.raise_for_status()
            return True
        except Exception as exc:
            print(
                "GRAEN_RESEARCH_IREN_CALLBACK_ERROR",
                {"error": type(exc).__name__, "job_id": linked_job_id},
                flush=True,
            )
            return False

    async def process_once(self) -> dict[str, Any]:
        claimed = await self.gateway.claim_research_problem(
            worker_id=self.worker_id,
            runtime_version=RUNTIME_VERSION,
            methodology_version=self.active_methodology_version,
            domain=PROBLEM_DOMAIN,
        )
        self.last_heartbeat_at = datetime.now(UTC)
        problem = claimed.get("problem")
        run = claimed.get("run")
        if not isinstance(problem, Mapping) or not isinstance(run, Mapping):
            self.active_problem_id = None
            await self._heartbeat()
            return {"status": "IDLE", "claimed": False}

        problem_id = str(problem.get("problem_id"))
        run_id = str(run.get("run_id"))
        linked_job_id = (
            str(problem.get("linked_iren_job_id"))
            if problem.get("linked_iren_job_id")
            else None
        )
        self.active_problem_id = problem_id
        self.last_claim_at = datetime.now(UTC)
        await self._heartbeat()

        bars = await self._fetch_corpus()
        result = run_crypto_research_v7(
            bars_by_symbol=bars,
            development_start=DEVELOPMENT_START,
            validation_start=VALIDATION_START,
            holdout_start=HOLDOUT_START,
            holdout_end=HOLDOUT_END,
            corpus_provenance_verified=True,
            previously_inspected_ranges=PREVIOUSLY_INSPECTED_RANGES,
        )
        result = {
            **result,
            "bar_counts": {symbol: len(rows) for symbol, rows in bars.items()},
            "source_commit": _source_commit(),
            "deployment_id": _deployment_id(),
            "problem_id": problem_id,
            "graen_run_id": run_id,
        }

        artifact = await self.gateway.record_artifact(
            problem_id=problem_id,
            run_id=run_id,
            artifact_type="CRYPTO_RESEARCH_BATCH_RESULT",
            methodology_version=METHODOLOGY_VERSION,
            content=result,
        )

        promote = result.get("decision") == "PROMOTE_TO_VELUM"
        problem_status = "SUCCEEDED" if promote else "WAITING"
        summary = {
            "state": (
                "CANDIDATE_READY_FOR_VELUM"
                if promote
                else "RESEARCH_BATCH_COMPLETE"
            ),
            "decision": result.get("decision"),
            "status": result.get("status"),
            "methodology_version": METHODOLOGY_VERSION,
            "research_batch_id": result.get("research_batch_id"),
            "candidate_count": result.get("candidate_count"),
            "candidate_family_count": result.get("candidate_family_count"),
            "validation_survivors": result.get("validation_survivors") or [],
            "selected_candidate": result.get("selected_candidate"),
            "holdout_passed": bool((result.get("holdout") or {}).get("passed")),
            "artifact_id": (
                (artifact.get("artifact") or {}).get("artifact_id")
                if isinstance(artifact.get("artifact"), Mapping)
                else None
            ),
            "content_hash": artifact.get("content_hash"),
            "model_invoked": False,
            "execution_authority": False,
            "production_state_changed": False,
            "next_action": result.get("next_action"),
        }
        await self.gateway.complete_research_problem(
            problem_id=problem_id,
            run_id=run_id,
            worker_id=self.worker_id,
            status=problem_status,
            result_summary=summary,
            model_usage={"invoked": False},
        )

        callback_status = "SUCCEEDED" if promote else "WAITING"
        callback_delivered = await self._callback_iren(
            linked_job_id,
            status=callback_status,
            result={
                "graen_problem_id": problem_id,
                "graen_run_id": run_id,
                **summary,
            },
        )
        summary["iren_callback_delivered"] = callback_delivered
        self.active_problem_id = None
        self.last_completion_at = datetime.now(UTC)
        self.last_result = summary
        self.last_error = None
        await self._heartbeat()
        print("GRAEN_RESEARCH_BATCH_COMPLETE", summary, flush=True)
        return {"claimed": True, "problem_id": problem_id, **summary}


runtime = GraenResearchExecutor()


@asynccontextmanager
async def lifespan(_: FastAPI):
    state = runtime.health()
    if not runtime.gateway.configured:
        raise RuntimeError("GRAEN gateway is not configured")
    if not runtime.settings.credentials_configured:
        raise RuntimeError("market-data credentials are not configured")
    if state["isolation_violations"]:
        raise RuntimeError("GRAEN research executor isolation check failed")
    await runtime.start()
    try:
        yield
    finally:
        await runtime.stop()


app = FastAPI(
    title="ANEVUM GRAEN Research Executor",
    version="1.0.0",
    lifespan=lifespan,
)


@app.get("/health")
async def health():
    state = runtime.health()
    if not state["ok"]:
        raise HTTPException(status_code=503, detail=state)
    return state


@app.get("/v1/status")
async def status():
    return runtime.health()
