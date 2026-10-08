from __future__ import annotations

import hmac
import json
import os
from contextlib import asynccontextmanager
from datetime import date, datetime, timezone
from typing import Any, Literal

from fastapi import FastAPI, Header, HTTPException, Query, Request
from pydantic import BaseModel, Field

from app.nostra.embedded import CoreNostraGateway, CoreNostraLedger
from app.nostra.service import (
    BaselineEvidenceRequest,
    NostraRuntime,
    require_nostra_api_token,
)
from app.research_agent.embedded import EmbeddedResearchReview

from .store import RhenCoreStore

UTC = timezone.utc
RUNTIME_VERSION = "rhen-core-v3.2.0"
store = RhenCoreStore()
embedded_nostra = NostraRuntime(
    ledger=CoreNostraLedger(store),
    gateway=CoreNostraGateway(store),
)
embedded_research = EmbeddedResearchReview(store)


class EvidenceEvent(BaseModel):
    event_key: str = Field(min_length=1, max_length=512)
    run_id: str | None = None
    strategy_version_id: str | None = None
    event_type: str = Field(min_length=1, max_length=200)
    occurred_at: datetime
    symbol: str | None = None
    correlation_id: str | None = None
    source: str = Field(default="RHEN", min_length=1, max_length=200)
    payload: dict[str, Any] = Field(default_factory=dict)


class EvidenceBatch(BaseModel):
    events: list[EvidenceEvent] = Field(min_length=1, max_length=100)


class DeterministicResearchReviewRequest(BaseModel):
    cadence: Literal["daily", "weekly"] = "daily"
    invoke_model: bool = False
    persist: bool = True
    expected_session: date | None = None


def _expected(*names: str) -> list[str]:
    return [
        os.getenv(name, "").strip()
        for name in names
        if os.getenv(name, "").strip()
    ]


def _authorized(provided: str | None, *env_names: str) -> bool:
    expected = _expected(*env_names)
    if not expected:
        return True
    return bool(
        provided
        and any(hmac.compare_digest(provided, item) for item in expected)
    )


def _require(provided: str | None, *env_names: str) -> None:
    if not _authorized(provided, *env_names):
        raise HTTPException(status_code=401, detail="unauthorized")


def _require_research_operator(provided: str | None) -> None:
    expected = _expected(
        "RHEN_REVIEW_TOKEN",
        "RHEN_RESEARCH_ADMIN_TOKEN",
        "RHEN_CORE_TOKEN",
    )
    if not expected:
        raise HTTPException(
            status_code=503,
            detail="research operator token is not configured",
        )
    if not provided or not any(
        hmac.compare_digest(provided, item) for item in expected
    ):
        raise HTTPException(status_code=401, detail="unauthorized")


@asynccontextmanager
async def lifespan(_: FastAPI):
    await embedded_nostra.start()
    try:
        yield
    finally:
        await embedded_nostra.stop()


app = FastAPI(
    title="RHEN Core",
    version=RUNTIME_VERSION,
    lifespan=lifespan,
)


@app.get("/")
def root() -> dict[str, Any]:
    return {
        "ok": True,
        "system": "RHEN",
        "plane": "core",
        "runtime_version": RUNTIME_VERSION,
        "execution_authority": False,
        "broker_orders_possible": False,
    }


@app.get("/live")
def live() -> dict[str, Any]:
    return {
        "ok": True,
        "service": "rhen-core",
        "runtime_version": RUNTIME_VERSION,
    }


@app.get("/ready")
def ready() -> dict[str, Any]:
    try:
        with store.connect() as conn:
            conn.execute("select 1").fetchone()
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail=f"store_unavailable:{type(exc).__name__}",
        ) from exc
    return {
        "ok": True,
        "service": "rhen-core",
        "storage": store.storage_state(),
        "execution_authority": False,
        "broker_orders_possible": False,
    }


@app.get("/status")
def status() -> dict[str, Any]:
    snapshot = store.graen_snapshot()
    return {
        "ok": True,
        "system": "RHEN",
        "plane": "core",
        "runtime_version": RUNTIME_VERSION,
        "storage": store.storage_state(),
        "research": {
            "problems": len(snapshot.get("problems") or []),
            "runs": len(snapshot.get("runs") or []),
            "artifacts": len(snapshot.get("artifacts") or []),
            "runtime_state": snapshot.get("runtime_state") or {},
        },
        "execution_authority": False,
        "broker_orders_possible": False,
        "live_execution_authorized": False,
    }


@app.post("/v1/events")
def ingest_events(
    batch: EvidenceBatch,
    x_anevum_foundation_token: str | None = Header(
        default=None, alias="x-anevum-foundation-token"
    ),
    x_anevum_ingest_token: str | None = Header(
        default=None, alias="x-anevum-ingest-token"
    ),
    x_nostra_gateway_token: str | None = Header(
        default=None, alias="x-nostra-gateway-token"
    ),
) -> dict[str, Any]:
    provided = (
        x_anevum_foundation_token
        or x_anevum_ingest_token
        or x_nostra_gateway_token
    )
    _require(
        provided,
        "RHEN_CORE_TOKEN",
        "FOUNDATION_INGEST_TOKEN",
        "TRADING_INGEST_TOKEN",
        "NOSTRA_GATEWAY_TOKEN",
    )
    payload = [event.model_dump(mode="json") for event in batch.events]
    return store.ingest_events(payload)



@app.get("/v1/trading-public-feed")
def trading_public_feed() -> dict[str, Any]:
    return store.public_live_feed()


@app.get("/v1/strategy-pipeline")
def strategy_pipeline() -> dict[str, Any]:
    return store.strategy_pipeline_research()


@app.get("/v1/trading-report-read")
def trading_report_read(
    request: Request,
    x_anevum_foundation_token: str | None = Header(
        default=None, alias="x-anevum-foundation-token"
    ),
    x_anevum_ingest_token: str | None = Header(
        default=None, alias="x-anevum-ingest-token"
    ),
) -> dict[str, Any]:
    provided = x_anevum_foundation_token or x_anevum_ingest_token
    _require(
        provided,
        "RHEN_CORE_TOKEN",
        "FOUNDATION_INGEST_TOKEN",
        "TRADING_INGEST_TOKEN",
    )
    params = {key: value for key, value in request.query_params.items()}
    result = store.report_read(params)
    if result.get("ok") is False:
        error = str(result.get("error") or "invalid_request")
        raise HTTPException(
            status_code=422 if error.startswith("invalid_") else 400,
            detail=error,
        )
    return result


@app.post("/v1/trading-reconcile")
def trading_reconcile(
    body: dict[str, Any],
    x_anevum_foundation_token: str | None = Header(
        default=None, alias="x-anevum-foundation-token"
    ),
    x_anevum_ingest_token: str | None = Header(
        default=None, alias="x-anevum-ingest-token"
    ),
) -> dict[str, Any]:
    provided = x_anevum_foundation_token or x_anevum_ingest_token
    _require(
        provided,
        "RHEN_CORE_TOKEN",
        "FOUNDATION_INGEST_TOKEN",
        "TRADING_INGEST_TOKEN",
    )
    try:
        return store.reconcile(body)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.get("/v1/graen-gateway")
def graen_gateway_read(
    x_graen_gateway_token: str | None = Header(
        default=None, alias="x-graen-gateway-token"
    ),
) -> dict[str, Any]:
    _require(
        x_graen_gateway_token,
        "GRAEN_GATEWAY_TOKEN",
        "RHEN_CORE_TOKEN",
    )
    return store.graen_snapshot()


@app.post("/v1/graen-gateway")
def graen_gateway_write(
    body: dict[str, Any],
    x_graen_gateway_token: str | None = Header(
        default=None, alias="x-graen-gateway-token"
    ),
) -> dict[str, Any]:
    _require(
        x_graen_gateway_token,
        "GRAEN_GATEWAY_TOKEN",
        "RHEN_CORE_TOKEN",
    )
    try:
        return store.graen_action(str(body.get("action") or ""), body)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.get("/v1/scheduler-gateway")
def scheduler_gateway_read(
    limit: int = Query(default=100, ge=1, le=250),
    x_anevum_ingest_token: str | None = Header(
        default=None, alias="x-anevum-ingest-token"
    ),
) -> dict[str, Any]:
    _require(
        x_anevum_ingest_token,
        "TRADING_INGEST_TOKEN",
        "RHEN_CORE_TOKEN",
    )
    return {"ok": True, "runs": store.scheduler_recent(limit)}


@app.post("/v1/scheduler-gateway")
def scheduler_gateway_write(
    body: dict[str, Any],
    x_anevum_ingest_token: str | None = Header(
        default=None, alias="x-anevum-ingest-token"
    ),
) -> dict[str, Any]:
    _require(
        x_anevum_ingest_token,
        "TRADING_INGEST_TOKEN",
        "RHEN_CORE_TOKEN",
    )
    action = str(body.get("action") or "")
    if action == "claim":
        job = body.get("job")
        if not isinstance(job, dict):
            raise HTTPException(
                status_code=422,
                detail="invalid_claim_shape",
            )
        return store.scheduler_claim(job)
    if action == "complete":
        return store.scheduler_complete(body)
    if action == "iren_read":
        value, revision = store.get_kv("iren", "state", {})
        return {
            "ok": True,
            "state": value or {},
            "revision": revision,
        }
    if action == "iren_commit":
        state = dict(body.get("state") or {})
        expected = int(body.get("expected_revision") or 0)
        _current, current_revision = store.get_kv("iren", "state", {})
        if current_revision != expected:
            return {
                "ok": True,
                "committed": False,
                "state": _current or {},
                "revision": current_revision,
            }
        revision = store.set_kv("iren", "state", state)
        return {
            "ok": True,
            "committed": True,
            "state": state,
            "revision": revision,
        }
    if action == "iren_configuration_accept":
        try:
            return store.accept_iren_configuration(
                expected_revision=int(body.get("expected_revision") or 0),
                fingerprint=str(body.get("fingerprint") or ""),
                reviewed_by=str(body.get("reviewed_by") or "operator"),
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    if action in {
        "iren_notifications_claim",
        "iren_notification_complete",
    }:
        return {
            "ok": True,
            "events": [],
            "claimed": False,
        }
    if action.startswith("iren_"):
        try:
            return store.iren_work_action(action, body)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    store.set_kv(
        "rhen_runtime",
        str(body.get("job_id") or body.get("job_key") or action),
        body,
    )
    return {
        "ok": True,
        "status": "RECORDED",
        "execution_authority": False,
    }


@app.get("/v1/research/health")
def embedded_research_health() -> dict[str, Any]:
    return embedded_research.health()


@app.get("/v1/research/readiness/public")
async def embedded_public_research_readiness() -> dict[str, Any]:
    return await embedded_research.public_readiness()


@app.get("/v1/research/theory/public")
def embedded_public_research_theory() -> dict[str, Any]:
    return embedded_research.public_theory()


@app.get("/v1/research/status")
async def embedded_research_status(
    x_rhen_agent_admin_token: str | None = Header(
        default=None,
        alias="x-rhen-agent-admin-token",
    ),
) -> dict[str, Any]:
    _require_research_operator(x_rhen_agent_admin_token)
    return await embedded_research.status()


@app.get("/v1/research/readiness")
async def embedded_research_readiness(
    cadence: Literal["daily", "weekly"] = Query(default="daily"),
    x_rhen_agent_admin_token: str | None = Header(
        default=None,
        alias="x-rhen-agent-admin-token",
    ),
) -> dict[str, Any]:
    _require_research_operator(x_rhen_agent_admin_token)
    return await embedded_research.readiness(cadence)


@app.post("/v1/research/review")
async def embedded_research_review(
    request: DeterministicResearchReviewRequest,
    x_rhen_agent_admin_token: str | None = Header(
        default=None,
        alias="x-rhen-agent-admin-token",
    ),
) -> dict[str, Any]:
    _require_research_operator(x_rhen_agent_admin_token)
    if request.invoke_model:
        raise HTTPException(
            status_code=409,
            detail="semantic_review_not_available_in_permanent_runtime",
        )
    try:
        return await embedded_research.review(
            request.cadence,
            expected_session=(
                request.expected_session.isoformat()
                if request.expected_session
                else None
            ),
            persist=request.persist,
        )
    except RuntimeError as exc:
        message = str(exc)
        if message in {
            "canonical_report_not_current",
            "research_review_in_progress",
        }:
            raise HTTPException(status_code=409, detail=message) from exc
        raise HTTPException(
            status_code=503,
            detail=f"deterministic_research_review_failed:{message}",
        ) from exc


@app.get("/v1/research-agent-gateway")
def research_gateway_read(
    x_rhen_research_gateway_token: str | None = Header(
        default=None,
        alias="x-rhen-research-gateway-token",
    ),
    x_anevum_ingest_token: str | None = Header(
        default=None,
        alias="x-anevum-ingest-token",
    ),
) -> dict[str, Any]:
    provided = x_rhen_research_gateway_token or x_anevum_ingest_token
    _require(
        provided,
        "RHEN_RESEARCH_GATEWAY_TOKEN",
        "TRADING_INGEST_TOKEN",
        "RHEN_CORE_TOKEN",
    )
    return {
        "ok": True,
        "evidence": store.canonical_evidence(),
        "execution_authority": False,
        "broker_orders_possible": False,
    }


@app.post("/v1/research-agent-gateway")
def research_gateway_write(
    body: dict[str, Any],
    x_rhen_research_gateway_token: str | None = Header(
        default=None,
        alias="x-rhen-research-gateway-token",
    ),
) -> dict[str, Any]:
    _require(
        x_rhen_research_gateway_token,
        "RHEN_RESEARCH_GATEWAY_TOKEN",
        "RHEN_CORE_TOKEN",
    )
    action = str(body.get("action") or "record_run")
    now = datetime.now(UTC).isoformat()
    if action in {"record_run", "save_run"}:
        payload = (
            body.get("run")
            if isinstance(body.get("run"), dict)
            else body
        )
        return store.record_research_audit(payload)
    if action in {"record_search_ledger", "save_search_ledger"}:
        payload = (
            body.get("ledger")
            if isinstance(body.get("ledger"), dict)
            else body
        )
        key = str(
            payload.get("ledger_hash")
            or f"ledger:{hash(json.dumps(payload, sort_keys=True, default=str))}"
        )
        with store.connect() as conn:
            conn.execute(
                """insert or replace into research_ledgers(
                    ledger_hash,payload_json,created_at
                ) values(?,?,?)""",
                (key, json.dumps(payload, default=str), now),
            )
            conn.commit()
        return {
            "ok": True,
            "ledger_hash": key,
            "execution_authority": False,
        }
    if action == "record_run_and_search_ledger":
        run = body.get("run") if isinstance(body.get("run"), dict) else {}
        ledger = (
            body.get("search_ledger")
            if isinstance(body.get("search_ledger"), dict)
            else {}
        )
        return store.record_research_audit(
            run,
            search_ledger=ledger,
        )
    raise HTTPException(status_code=422, detail="invalid_action")


@app.get("/v1/nostra/health")
def embedded_nostra_health() -> dict[str, Any]:
    return {
        **embedded_nostra.health(),
        "embedded": True,
        "host": "RHEN_CORE",
        "independent_runtime": False,
    }


@app.post("/v1/nostra/evidence/baseline")
async def embedded_nostra_baseline_evidence(
    req: BaselineEvidenceRequest,
    x_nostra_api_token: str | None = Header(
        default=None,
        alias="x-nostra-api-token",
    ),
) -> dict[str, Any]:
    require_nostra_api_token(x_nostra_api_token)
    return await embedded_nostra.baseline_evidence(req)


@app.get("/v1/nostra-forecasts")
def nostra_forecasts(
    x_anevum_ingest_token: str | None = Header(
        default=None, alias="x-anevum-ingest-token"
    ),
) -> dict[str, Any]:
    _require(
        x_anevum_ingest_token,
        "FOUNDATION_INGEST_TOKEN",
        "TRADING_INGEST_TOKEN",
        "RHEN_CORE_TOKEN",
    )
    return store.nostra_forecasts()


@app.get("/v1/nostra-gateway")
def nostra_gateway(
    x_nostra_gateway_token: str | None = Header(
        default=None, alias="x-nostra-gateway-token"
    ),
) -> dict[str, Any]:
    _require(
        x_nostra_gateway_token,
        "NOSTRA_GATEWAY_TOKEN",
        "RHEN_CORE_TOKEN",
    )
    result = store.nostra_work()
    result["broker_orders_possible"] = False
    result["live_execution_authorized"] = False
    return result


@app.post("/v1/maintenance/prune")
def maintenance_prune(
    x_rhen_core_token: str | None = Header(
        default=None, alias="x-rhen-core-token"
    ),
) -> dict[str, Any]:
    _require(x_rhen_core_token, "RHEN_CORE_TOKEN")
    deleted = store.prune()
    compaction = store.compact_storage()
    return {
        "ok": True,
        "deleted": deleted,
        "compaction": compaction,
        "storage": store.storage_state(),
    }
