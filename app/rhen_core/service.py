from __future__ import annotations

import hmac
import json
import os
from datetime import datetime, timezone
from typing import Any

from fastapi import FastAPI, Header, HTTPException, Query, Request
from pydantic import BaseModel, Field

from .store import RhenCoreStore

UTC = timezone.utc
RUNTIME_VERSION = "rhen-core-v3.0.0"
store = RhenCoreStore()


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


app = FastAPI(title="RHEN Core", version=RUNTIME_VERSION)


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
        revision = store.set_kv("iren", "state", state)
        return {
            "ok": True,
            "state": state,
            "revision": revision,
        }
    if action in {
        "iren_notifications_claim",
        "iren_notification_complete",
    }:
        return {
            "ok": True,
            "events": [],
            "claimed": False,
        }
    store.set_kv(
        "iren_work",
        str(body.get("job_id") or body.get("job_key") or action),
        body,
    )
    return {
        "ok": True,
        "status": "RECORDED",
        "execution_authority": False,
    }


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
        key = str(
            payload.get("run_key")
            or payload.get("run_id")
            or f"run:{hash(json.dumps(payload, sort_keys=True, default=str))}"
        )
        with store.connect() as conn:
            conn.execute(
                """insert or replace into research_runs(
                    run_key,payload_json,created_at
                ) values(?,?,?)""",
                (key, json.dumps(payload, default=str), now),
            )
            conn.commit()
        return {
            "ok": True,
            "run_key": key,
            "execution_authority": False,
        }
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
        run_key = str(
            run.get("run_key")
            or run.get("run_id")
            or f"run:{hash(json.dumps(run, sort_keys=True, default=str))}"
        )
        ledger_key = str(
            ledger.get("ledger_hash")
            or f"ledger:{hash(json.dumps(ledger, sort_keys=True, default=str))}"
        )
        with store.connect() as conn:
            conn.execute(
                """insert or replace into research_runs(
                    run_key,payload_json,created_at
                ) values(?,?,?)""",
                (run_key, json.dumps(run, default=str), now),
            )
            conn.execute(
                """insert or replace into research_ledgers(
                    ledger_hash,payload_json,created_at
                ) values(?,?,?)""",
                (ledger_key, json.dumps(ledger, default=str), now),
            )
            conn.commit()
        return {
            "ok": True,
            "run_key": run_key,
            "ledger_hash": ledger_key,
            "execution_authority": False,
        }
    raise HTTPException(status_code=422, detail="invalid_action")


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
    return {
        "ok": True,
        "deleted": store.prune(),
        "storage": store.storage_state(),
    }
