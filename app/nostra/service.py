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
from .models import MODEL_ID as DRIFT_MODEL_ID, shrunken_drift_forecast
from .contracts import build_evaluation, build_forecast, build_outcome, build_score_record, build_snapshot
from .ledger import NostraLedger
from .scoring import SCORING_VERSION, score_return_forecast


UTC = timezone.utc
RUNTIME_VERSION = "nostra-runtime-v1.3.0"
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
        self.last_evaluation_ids: dict[str, str] = {}
        self.last_evaluation: dict[str, Any] | None = None
        self.last_evaluations: dict[str, dict[str, Any]] = {}
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
            "last_evaluation_ids": dict(self.last_evaluation_ids),
            "last_evaluation": self.last_evaluation,
            "last_evaluations": dict(self.last_evaluations),
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

    async def _forecast_candidate(
        self,
        candidate: Mapping[str, Any],
        drift_training_state: Mapping[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
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
        requested_models = {
            str(value)
            for value in (candidate.get("missing_model_ids") or [])
            if str(value).strip()
        }
        if not requested_models:
            requested_models = {zero_return_baseline()["baseline_id"]}

        market_lane = str(candidate.get("market_lane") or "us_equity").strip().lower()
        if market_lane in {"", "equities"}:
            market_lane = "us_equity"
        if market_lane not in {"us_equity", "us_equity_extended"}:
            raise ValueError("unsupported_nostra_market_lane")
        feature_set_version = str(
            feature_state.get("methodology_version")
            or features.get("methodology_version")
            or "rhen-equity-candidate-point-in-time-v1"
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
            market_lane=market_lane,
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
        correlation_id = f"nostra:{identity}"
        if not await self.ledger.append_snapshot(snapshot, correlation_id=correlation_id):
            raise RuntimeError("snapshot_persistence_failed")

        forecasts: list[dict[str, Any]] = []
        generated_at = datetime.now(UTC)
        baseline = zero_return_baseline()
        if baseline["baseline_id"] in requested_models:
            baseline_forecast = build_forecast(
                snapshot_id=snapshot["snapshot_id"],
                symbol=symbol,
                market_lane=market_lane,
                as_of_timestamp=as_of,
                generated_at=generated_at,
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
            if not await self.ledger.append_forecast(
                baseline_forecast,
                correlation_id=correlation_id,
            ):
                raise RuntimeError("forecast_persistence_failed")
            forecasts.append(baseline_forecast)

        if DRIFT_MODEL_ID in requested_models:
            drift = shrunken_drift_forecast(_dict(drift_training_state))
            if drift.get("eligible") is not True:
                raise ValueError("drift forecast requested without eligible point-in-time training")
            cutoff = _aware(drift.get("training_cutoff"), "training_cutoff")
            if cutoff >= as_of:
                raise ValueError("drift training cutoff must precede candidate observation")
            drift_forecast = build_forecast(
                snapshot_id=snapshot["snapshot_id"],
                symbol=symbol,
                market_lane=market_lane,
                as_of_timestamp=as_of,
                generated_at=generated_at,
                horizon_minutes=LIVE_HORIZON_MINUTES,
                target_kind="return",
                model_id=str(drift["model_id"]),
                model_version=str(drift["model_version"]),
                feature_set_version=feature_set_version,
                forecast_payload={
                    "expected_return": drift["expected_return"],
                    "training": {
                        "methodology_version": drift["methodology_version"],
                        "training_cutoff": drift["training_cutoff"],
                        "training_window_start": drift["training_window_start"],
                        "training_window_end": drift["training_window_end"],
                        "through_score_id": drift["through_score_id"],
                        "independent_cycles": drift["independent_cycles"],
                        "raw_outcome_count": drift["raw_outcome_count"],
                        "raw_mean_cycle_return": drift["raw_mean_cycle_return"],
                        "shrinkage_weight": drift["shrinkage_weight"],
                        "shrinkage_cycles": drift["shrinkage_cycles"],
                    },
                },
                authority_state="LOW_SUPPORT",
                run_id=candidate.get("run_id"),
                strategy_version_id=candidate.get("strategy_version_id"),
                code_sha=_source_commit(),
                provenance={
                    **provenance,
                    "training_cutoff": drift["training_cutoff"],
                    "training_through_score_id": drift["through_score_id"],
                },
            )
            if not await self.ledger.append_forecast(
                drift_forecast,
                correlation_id=correlation_id,
            ):
                raise RuntimeError("drift_forecast_persistence_failed")
            forecasts.append(drift_forecast)

        if forecasts:
            self.last_persisted_at = datetime.now(UTC)
            self.last_forecast_at = self.last_persisted_at
        return forecasts

    async def _score_outcome(self, row: Mapping[str, Any]) -> dict[str, Any]:
        forecast_id = str(row.get("forecast_id") or "").strip()
        identity = str(row.get("candidate_identity") or "").strip()
        if not forecast_id or not identity:
            raise ValueError("forecast_id and candidate_identity are required")
        observed_at = _aware(row.get("observed_at"), "observed_at")
        realized_return = float(row.get("realized_return"))
        expected_return = float(row.get("expected_return"))
        model_id = str(row.get("model_id") or "").strip()
        model_version = str(row.get("model_version") or "").strip()
        if not model_id or not model_version:
            raise ValueError("model_id and model_version are required for scoring")

        baseline = zero_return_baseline()
        scored = score_return_forecast(
            expected_return,
            realized_return,
            baseline_expected_return=baseline["expected_return"],
        )
        provenance = {
            "runtime_version": RUNTIME_VERSION,
            "deployment_id": _deployment_id(),
            "candidate_identity": identity,
            "live_baseline_version": LIVE_BASELINE_VERSION,
            "model_id": model_id,
            "model_version": model_version,
            "expected_return": expected_return,
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

    async def _persist_model_evaluation(
        self,
        row: Mapping[str, Any],
    ) -> dict[str, Any] | None:
        if row.get("research_only") is not True or row.get("execution_authority") is not False:
            raise ValueError("model evaluation must preserve the NOSTRA authority boundary")

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
        evaluation_key = f"{evaluation['model_id']}:{evaluation['model_version']}"
        if self.last_evaluation_ids.get(evaluation_key) == evaluation["evaluation_id"]:
            return None
        if not await self.ledger.append_evaluation(
            evaluation,
            correlation_id=f"nostra:evaluation:{evaluation['through_score_id']}",
        ):
            raise RuntimeError("evaluation_persistence_failed")
        self.last_persisted_at = datetime.now(UTC)
        self.last_evaluation_at = self.last_persisted_at
        self.last_evaluation_id = evaluation["evaluation_id"]
        self.last_evaluation_ids[evaluation_key] = evaluation["evaluation_id"]
        self.last_evaluation = {
            "evaluation_id": evaluation["evaluation_id"],
            "sample_count": evaluation["sample_count"],
            "window_start": evaluation["window_start"],
            "window_end": evaluation["window_end"],
            "metrics": evaluation["metrics"],
            "model_id": evaluation["model_id"],
            "model_version": evaluation["model_version"],
            "calibration": evaluation["calibration"],
        }
        self.last_evaluations[evaluation_key] = dict(self.last_evaluation)
        return evaluation

    async def process_once(self) -> dict[str, Any]:
        work = await self.gateway.work()
        forecast_rows = work.get("forecast_candidates")
        score_rows = work.get("score_outcomes")
        forecasts = forecast_rows if isinstance(forecast_rows, list) else []
        outcomes = score_rows if isinstance(score_rows, list) else []
        evaluation_rows = work.get("model_evaluations")
        evaluations = evaluation_rows if isinstance(evaluation_rows, list) else []
        if not evaluations and isinstance(work.get("baseline_evaluation"), Mapping):
            evaluations = [work["baseline_evaluation"]]
        drift_training_state = _dict(work.get("drift_training_state"))

        emitted_forecasts = 0
        emitted_baseline_forecasts = 0
        emitted_drift_forecasts = 0
        emitted_scores = 0
        emitted_evaluations = 0
        for candidate in forecasts[:200]:
            if not isinstance(candidate, Mapping):
                continue
            persisted = await self._forecast_candidate(
                candidate,
                drift_training_state=drift_training_state,
            )
            emitted_forecasts += len(persisted)
            emitted_baseline_forecasts += sum(
                row.get("model_id") == zero_return_baseline()["baseline_id"]
                for row in persisted
            )
            emitted_drift_forecasts += sum(
                row.get("model_id") == DRIFT_MODEL_ID
                for row in persisted
            )

        for outcome in outcomes[:500]:
            if not isinstance(outcome, Mapping):
                continue
            await self._score_outcome(outcome)
            emitted_scores += 1

        for evaluation_row in evaluations:
            if not isinstance(evaluation_row, Mapping):
                continue
            persisted_evaluation = await self._persist_model_evaluation(evaluation_row)
            emitted_evaluations += int(persisted_evaluation is not None)

        self.last_cycle_at = datetime.now(UTC)
        self.last_result = {
            "status": "HEALTHY",
            "forecast_candidates": len(forecasts),
            "forecasts_persisted": emitted_forecasts,
            "baseline_forecasts_persisted": emitted_baseline_forecasts,
            "drift_forecasts_persisted": emitted_drift_forecasts,
            "score_outcomes": len(outcomes),
            "scores_persisted": emitted_scores,
            "evaluations_persisted": emitted_evaluations,
            "evaluation_models": len(evaluations),
            "drift_training": {
                "eligible": bool(drift_training_state.get("eligible")),
                "independent_cycles": int(
                    drift_training_state.get("independent_cycles") or 0
                ),
                "raw_outcome_count": int(
                    drift_training_state.get("raw_outcome_count") or 0
                ),
                "training_cutoff": drift_training_state.get("training_cutoff"),
                "mean_cycle_return": drift_training_state.get("mean_cycle_return"),
            },
            "gateway_counts": (
                work.get("counts")
                if isinstance(work.get("counts"), dict)
                else {}
            ),
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
