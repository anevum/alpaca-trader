from __future__ import annotations

from datetime import datetime, timezone
import hmac
import os
from typing import Any

from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

from .baselines import uniform_direction_baseline
from .contracts import build_forecast, build_snapshot
from .ledger import NostraLedger

UTC = timezone.utc
RUNTIME_VERSION = "nostra-runtime-v1.0.0"


def _source_commit() -> str | None:
    return os.getenv("RAILWAY_GIT_COMMIT_SHA") or os.getenv("NOSTRA_SOURCE_COMMIT")


def _deployment_id() -> str | None:
    return os.getenv("RAILWAY_DEPLOYMENT_ID")


class BaselineEvidenceRequest(BaseModel):
    symbol: str = Field(min_length=1, max_length=40)
    market_lane: str = Field(default="research", min_length=1, max_length=40)
    as_of_timestamp: datetime
    horizon_minutes: int = Field(default=10, ge=1, le=1440)
    feature_set_version: str = Field(default="nostra-features-v1", min_length=1, max_length=120)
    raw_features: dict[str, Any] = Field(default_factory=dict)
    normalized_features: dict[str, Any] = Field(default_factory=dict)
    market_state: dict[str, Any] = Field(default_factory=dict)
    data_quality: dict[str, Any] = Field(default_factory=dict)
    source: dict[str, Any] = Field(default_factory=dict)
    correlation_id: str | None = None


def require_nostra_api_token(token: str | None) -> None:
    expected = os.getenv("NOSTRA_API_TOKEN", "").strip()
    if not expected or token is None or not hmac.compare_digest(token, expected):
        raise HTTPException(status_code=401, detail="unauthorized")


class NostraRuntime:
    def __init__(self) -> None:
        self.started_at = datetime.now(UTC)
        self.ledger = NostraLedger()
        self.last_persisted_at: datetime | None = None
        self.last_error: str | None = None

    def health(self) -> dict[str, Any]:
        return {
            "ok": self.ledger.configured and self.last_error is None,
            "system": "NOSTRA",
            "program": "FORWARD",
            "service": "nostra",
            "runtime_version": RUNTIME_VERSION,
            "research_only": True,
            "execution_authority": False,
            "foundation_configured": self.ledger.configured,
            "last_persisted_at": self.last_persisted_at.isoformat() if self.last_persisted_at else None,
            "last_error": self.last_error,
            "runtime_provenance": {
                "system_version": RUNTIME_VERSION,
                "git_commit": _source_commit(),
                "deployment_id": _deployment_id(),
                "runtime_started_at": self.started_at.isoformat(),
            },
        }

    async def baseline_evidence(self, req: BaselineEvidenceRequest) -> dict[str, Any]:
        snapshot = build_snapshot(
            symbol=req.symbol,
            market_lane=req.market_lane,
            as_of_timestamp=req.as_of_timestamp,
            feature_set_version=req.feature_set_version,
            raw_features=req.raw_features,
            normalized_features=req.normalized_features,
            market_state=req.market_state,
            data_quality=req.data_quality,
            source=req.source,
            code_sha=_source_commit(),
            provenance={"runtime_version": RUNTIME_VERSION, "deployment_id": _deployment_id()},
        )
        baseline = uniform_direction_baseline()
        forecast = build_forecast(
            snapshot_id=snapshot["snapshot_id"],
            symbol=req.symbol,
            market_lane=req.market_lane,
            as_of_timestamp=req.as_of_timestamp,
            generated_at=datetime.now(UTC),
            horizon_minutes=req.horizon_minutes,
            target_kind="direction",
            model_id=baseline["baseline_id"],
            model_version=baseline["baseline_version"],
            feature_set_version=req.feature_set_version,
            forecast_payload={"probabilities": baseline["probabilities"]},
            authority_state="LOW_SUPPORT",
            code_sha=_source_commit(),
            provenance={"runtime_version": RUNTIME_VERSION, "deployment_id": _deployment_id()},
        )
        if not await self.ledger.append_snapshot(snapshot, correlation_id=req.correlation_id):
            self.last_error = "snapshot_persistence_failed"
            raise HTTPException(status_code=503, detail=self.last_error)
        if not await self.ledger.append_forecast(forecast, correlation_id=req.correlation_id):
            self.last_error = "forecast_persistence_failed"
            raise HTTPException(status_code=503, detail=self.last_error)
        self.last_error = None
        self.last_persisted_at = datetime.now(UTC)
        return {"ok": True, "snapshot": snapshot, "forecast": forecast, "baseline": baseline}


runtime = NostraRuntime()
app = FastAPI(title="ANEVUM NOSTRA", version=RUNTIME_VERSION)


@app.get("/live")
def live() -> dict[str, Any]:
    return {"ok": True, "service": "nostra", "runtime_version": RUNTIME_VERSION}


@app.get("/health")
def health() -> dict[str, Any]:
    return runtime.health()


@app.get("/ready")
def ready() -> dict[str, Any]:
    value = runtime.health()
    if not value["ok"]:
        raise HTTPException(status_code=503, detail=value)
    return value


@app.post("/v1/evidence/baseline")
async def baseline_evidence(
    req: BaselineEvidenceRequest,
    x_nostra_api_token: str | None = Header(default=None, alias="x-nostra-api-token"),
) -> dict[str, Any]:
    require_nostra_api_token(x_nostra_api_token)
    return await runtime.baseline_evidence(req)
