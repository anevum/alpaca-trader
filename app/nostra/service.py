from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timezone
import hmac
import os
from typing import Any, Mapping

import httpx
from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

from .baselines import uniform_direction_baseline, zero_return_baseline
from .contracts import build_evaluation, build_forecast, build_outcome, build_score_record, build_snapshot
from .ledger import NostraLedger
from .scoring import SCORING_VERSION, score_return_forecast


UTC = timezone.utc
RUNTIME_VERSION = "nostra-runtime-v1.2.0"
LIVE_BASELINE_VERSION = "nostra-live-zero-return-v1"
LIVE_HORIZON_MINUTES = 10


def _truthy(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _source_commit() -> str | None:
    return os.getenv("RAILWAY_GIT_COMMIT_SHA") or os.getenv("NOSTRA_SOURCE_COMMIT")


def _deployment_id() -> str | None:
    return os.getenv("RAILWAY_DEPLOYMENT_ID")


def _aware(value: Any, name: str) -> datetime:
    if isinstance(value, datetime):
        stamp = value
    else:
        try:
            stamp = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{name} must be an ISO timestamp") from exc
    if stamp.tzinfo is None:
        raise ValueError(f"{name} must be timezone-aware")
    return stamp.astimezone(UTC)


def _dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


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


class NostraGateway:
    def __init__(
        self,
        url: str | None = None,
        token: str | None = None,
        *,
        timeout_seconds: float = 15.0,
    ) -> None:
        events_url = os.getenv("FOUNDATION_EVENTS_URL", "").strip()
        derived = (
            events_url.rsplit("/v1/events", 1)[0] + "/v1/nostra-gateway"
            if "/v1/events" in events_url
            else ""
        )
        self.url = (
            url if url is not None else os.getenv("NOSTRA_GATEWAY_URL", derived)
        ).strip()
        self.token = (
            token if token is not None else os.getenv("NOSTRA_GATEWAY_TOKEN", "")
        ).strip()
        self.timeout_seconds = timeout_seconds

    @property
    def configured(self) -> bool:
        return self.url.startswith("http") and len(self.token) >= 32

    async def work(self) -> dict[str, Any]:
        if not self.configured:
            raise RuntimeError("NOSTRA gateway is not configured")
        headers = {
            "accept": "application/json",
            "x-nostra-gateway-token": self.token,
        }
        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            response = await client.get(self.url, headers=headers)
            response.raise_for_status()
            body = response.json()
        if (
            not isinstance(body, dict)
            or body.get("ok") is not True
            or body.get("research_only") is not True
            or body.get("execution_authority") is not False
        ):
            raise RuntimeError("NOSTRA gateway returned an invalid authority boundary")
        return body


class NostraRuntime:
    def __init__(
        self,
        *,
        ledger: NostraLedger | None = None,
        gateway: NostraGateway | None = None,
    ) -> None:
        self.started_at = datetime.now(UTC)
        self.ledger = ledger or NostraLedger()
        self.gateway = gateway or NostraGateway()
        self.interval_seconds = max(15, int(os.getenv("NOSTRA_TICK_SECONDS", "60")))
        self.autorun = _truthy("NOSTRA_AUTORUN", True)
        self.stop_event = asyncio.Event()
        self.task: asyncio.Task | None = None
        self.last_cycle_at: datetime | None = None
        self.last_persisted_at: datetime | None = None
        self.last_forecast_at: datetime | None = None
        self.last_score_at: datetime | None = None
        self.last_evaluation_at: datetime | None = None
        self.last_evaluation_id: str | None = None
        self.last_evaluation: dict[str, Any] | None = None
        self.last_error: str | None = None
        self.last_result: dict[str, Any] | None = None

    def health(self) -> dict[str, Any]:
        running = self.task is not None and not self.task.done()
        configured = self.ledger.configured and self.gateway.configured
        ready = configured and (running if self.autorun else True) and self.last_error is None
        return {
            "ok": ready,
            "system": "NOSTRA",
            "program": "FORWARD",
            "service": "nostra",
            "runtime_version": RUNTIME_VERSION,
            "research_only": True,
            "execution_authority": False,
            "broker_orders_possible": False,
            "risk_or_sizing_authority": False,
            "production_promotion_authority": False,
            "running": running,
            "worker_alive": running,
            "autorun": self.autorun,
            "forecast_horizon_minutes": LIVE_HORIZON_MINUTES,
            "baseline_methodology_version": LIVE_BASELINE_VERSION,
            "foundation_configured": configured,
            "gateway_configured": self.gateway.configured,
            "last_cycle_at": self.last_cycle_at.isoformat() if self.last_cycle_at else None,
            "last_persisted_at": self.last_persisted_at.isoformat() if self.last_persisted_at else None,
            "last_forecast_at": self.last_forecast_at.isoformat() if self.last_forecast_at else None,
            "last_score_at": self.last_score_at.isoformat() if self.last_score_at else None,
            "last_evaluation_at": self.last_evaluation_at.isoformat() if self.last_evaluation_at else None,
            "last_evaluation_id": self.last_evaluation_id,
            "last_evaluation": self.last_evaluation,
            "last_result": self.last_result,
            "last_error": self.last_error,
            "runtime_provenance": {
                "system_version": RUNTIME_VERSION,
                "git_commit": _source_commit(),
                "deployment_id": _deployment_id(),
                "runtime_started_at": self.started_at.isoformat(),
            },
        }

    async def start(self) -> None:
        if self.task is None:
            self.task = asyncio.create_task(self.run(), name="nostra-live-baseline")

    async def stop(self) -> None:
        self.stop_event.set()
        if self.task is not None:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
            self.task = None

    async def run(self) -> None:
        while not self.stop_event.is_set():
            try:
                if self.autorun:
                    await self.process_once()
                self.last_error = None
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.last_error = f"{type(exc).__name__}: {exc}"[:1000]
                print(
                    {
                        "event": "nostra_cycle_failed",
                        "at": datetime.now(UTC).isoformat(),
                        "error_type": type(exc).__name__,
                        "error": str(exc)[:500],
                    },
                    flush=True,
                )
            try:
                await asyncio.wait_for(self.stop_event.wait(), timeout=self.interval_seconds)
            except asyncio.TimeoutError:
                pass

    async def _forecast_candidate(self, candidate: Mapping[str, Any]) -> dict[str, Any]:
        identity = str(candidate.get("candidate_identity") or "").strip()
        symbol = str(candidate.get("symbol") or "").strip().upper()
        if not identity or not symbol:
            raise ValueError("candidate_identity and symbol are required")
        as_of = _aware(candidate.get("observed_at"), "observed_at")

        features = _dict(candidate.get("features"))
        feature_state = _dict(features.get("feature_state"))
        raw_features = _dict(feature_state.get("raw")) or features
        normalized_features = _dict(feature_state.get("normalized"))
        time_state = _dict(feature_state.get("time_state"))
        scan_cycle = _dict(candidate.get("scan_cycle"))
        attribution = _dict(candidate.get("research_attribution"))

        feature_set_version = str(
            feature_state.get("methodology_version")
            or features.get("methodology_version")
            or "crypto-candidate-point-in-time-v1"
        )
        provenance = {
            "runtime_version": RUNTIME_VERSION,
            "deployment_id": _deployment_id(),
            "candidate_identity": identity,
            "source_event_type": "decision_cycle",
            "live_baseline_version": LIVE_BASELINE_VERSION,
        }
        source = {
            "candidate_identity": identity,
            "scan_cycle_id": scan_cycle.get("scan_cycle_id"),
            "data_feed": scan_cycle.get("data_feed"),
            "bar_interval": scan_cycle.get("bar_interval"),
            "source_event_type": "decision_cycle",
        }

        snapshot = build_snapshot(
            symbol=symbol,
            market_lane="crypto",
            as_of_timestamp=as_of,
            feature_set_version=feature_set_version,
            raw_features=raw_features,
            normalized_features=normalized_features,
            market_state={"time_state": time_state, "research_attribution": attribution},
            data_quality={"data_status": scan_cycle.get("data_status")},
            source=source,
            run_id=candidate.get("run_id"),
            strategy_version_id=candidate.get("strategy_version_id"),
            code_sha=_source_commit(),
            provenance=provenance,
        )
        baseline = zero_return_baseline()
        forecast = build_forecast(
            snapshot_id=snapshot["snapshot_id"],
            symbol=symbol,
            market_lane="crypto",
            as_of_timestamp=as_of,
            generated_at=datetime.now(UTC),
            horizon_minutes=LIVE_HORIZON_MINUTES,
            target_kind="return",
            model_id=baseline["baseline_id"],
            model_version=baseline["baseline_version"],
            feature_set_version=feature_set_version,
            forecast_payload={"expected_return": baseline["expected_return"]},
            authority_state="LOW_SUPPORT",
            run_id=candidate.get("run_id"),
            strategy_version_id=candidate.get("strategy_version_id"),
            code_sha=_source_commit(),
            provenance=provenance,
        )

        correlation_id = f"nostra:{identity}"
        if not await self.ledger.append_snapshot(snapshot, correlation_id=correlation_id):
            raise RuntimeError("snapshot_persistence_failed")
        if not await self.ledger.append_forecast(forecast, correlation_id=correlation_id):
            raise RuntimeError("forecast_persistence_failed")
        self.last_persisted_at = datetime.now(UTC)
        self.last_forecast_at = self.last_persisted_at
        return forecast

    async def _score_outcome(self, row: Mapping[str, Any]) -> dict[str, Any]:
        forecast_id = str(row.get("forecast_id") or "").strip()
        identity = str(row.get("candidate_identity") or "").strip()
        if not forecast_id or not identity:
            raise ValueError("forecast_id and candidate_identity are required")
        observed_at = _aware(row.get("observed_at"), "observed_at")
        realized_return = float(row.get("realized_return"))

        baseline = zero_return_baseline()
        scored = score_return_forecast(
            baseline["expected_return"],
            realized_return,
            baseline_expected_return=baseline["expected_return"],
        )
        provenance = {
            "runtime_version": RUNTIME_VERSION,
            "deployment_id": _deployment_id(),
            "candidate_identity": identity,
            "live_baseline_version": LIVE_BASELINE_VERSION,
        }
        outcome = build_outcome(
            forecast_id=forecast_id,
            observed_at=observed_at,
            realized_payload={
                "candidate_identity": identity,
                "forward_return": realized_return,
                "max_favorable_return": row.get("max_favorable_return"),
                "max_adverse_return": row.get("max_adverse_return"),
                "source_methodology_version": row.get("outcome_methodology_version"),
            },
            provenance=provenance,
        )
        score = build_score_record(
            forecast_id=forecast_id,
            outcome_id=outcome["outcome_id"],
            metrics={
                "absolute_error": scored["absolute_error"],
                "squared_error": scored["squared_error"],
            },
            scoring_version=SCORING_VERSION,
            baseline_id=baseline["baseline_id"],
            baseline_version=baseline["baseline_version"],
            baseline_metrics=scored["baseline"],
            skill=scored["skill"],
            provenance=provenance,
        )

        correlation_id = f"nostra:{identity}"
        if not await self.ledger.append_outcome(outcome, correlation_id=correlation_id):
            raise RuntimeError("outcome_persistence_failed")
        if not await self.ledger.append_score(score, correlation_id=correlation_id):
            raise RuntimeError("score_persistence_failed")
        self.last_persisted_at = datetime.now(UTC)
        self.last_score_at = self.last_persisted_at
        return score

    async def _persist_baseline_evaluation(
        self,
        row: Mapping[str, Any],
    ) -> dict[str, Any] | None:
        if row.get("research_only") is not True or row.get("execution_authority") is not False:
            raise ValueError("baseline evaluation must preserve the NOSTRA authority boundary")

        evaluation = build_evaluation(
            model_id=str(row.get("model_id") or ""),
            model_version=str(row.get("model_version") or ""),
            horizon_minutes=int(row.get("horizon_minutes") or 0),
            target_kind=str(row.get("target_kind") or ""),
            window_start=_aware(row.get("window_start"), "window_start"),
            window_end=_aware(row.get("window_end"), "window_end"),
            sample_count=int(row.get("sample_count") or 0),
            through_score_id=str(row.get("through_score_id") or ""),
            metrics=_dict(row.get("metrics")),
            calibration=_dict(row.get("calibration")),
            evaluated_at=datetime.now(UTC),
            code_sha=_source_commit(),
            provenance={
                "runtime_version": RUNTIME_VERSION,
                "deployment_id": _deployment_id(),
                "source_schema_version": row.get("schema_version"),
                "live_baseline_version": LIVE_BASELINE_VERSION,
            },
        )
        if evaluation["evaluation_id"] == self.last_evaluation_id:
            return None
        if not await self.ledger.append_evaluation(
            evaluation,
            correlation_id=f"nostra:evaluation:{evaluation['through_score_id']}",
        ):
            raise RuntimeError("evaluation_persistence_failed")
        self.last_persisted_at = datetime.now(UTC)
        self.last_evaluation_at = self.last_persisted_at
        self.last_evaluation_id = evaluation["evaluation_id"]
        self.last_evaluation = {
            "evaluation_id": evaluation["evaluation_id"],
            "sample_count": evaluation["sample_count"],
            "window_start": evaluation["window_start"],
            "window_end": evaluation["window_end"],
            "metrics": evaluation["metrics"],
            "calibration": evaluation["calibration"],
        }
        return evaluation

    async def process_once(self) -> dict[str, Any]:
        work = await self.gateway.work()
        forecast_rows = work.get("forecast_candidates")
        score_rows = work.get("score_outcomes")
        forecasts = forecast_rows if isinstance(forecast_rows, list) else []
        outcomes = score_rows if isinstance(score_rows, list) else []
        evaluation_row = work.get("baseline_evaluation")

        emitted_forecasts = 0
        emitted_scores = 0
        emitted_evaluations = 0
        for candidate in forecasts[:200]:
            if not isinstance(candidate, Mapping):
                continue
            await self._forecast_candidate(candidate)
            emitted_forecasts += 1

        for outcome in outcomes[:500]:
            if not isinstance(outcome, Mapping):
                continue
            await self._score_outcome(outcome)
            emitted_scores += 1

        if isinstance(evaluation_row, Mapping):
            persisted_evaluation = await self._persist_baseline_evaluation(evaluation_row)
            emitted_evaluations = 1 if persisted_evaluation is not None else 0

        self.last_cycle_at = datetime.now(UTC)
        self.last_result = {
            "status": "HEALTHY",
            "forecast_candidates": len(forecasts),
            "forecasts_persisted": emitted_forecasts,
            "score_outcomes": len(outcomes),
            "scores_persisted": emitted_scores,
            "evaluations_persisted": emitted_evaluations,
            "evaluation_sample_count": (
                int(evaluation_row.get("sample_count") or 0)
                if isinstance(evaluation_row, Mapping)
                else 0
            ),
            "gateway_counts": work.get("counts") if isinstance(work.get("counts"), dict) else {},
            "observed_at": self.last_cycle_at.isoformat(),
        }
        return self.last_result

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
            generated_at=max(req.as_of_timestamp.astimezone(UTC), datetime.now(UTC)),
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


@asynccontextmanager
async def lifespan(app: FastAPI):
    await runtime.start()
    try:
        yield
    finally:
        await runtime.stop()


app = FastAPI(title="ANEVUM NOSTRA", version=RUNTIME_VERSION, lifespan=lifespan)


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
