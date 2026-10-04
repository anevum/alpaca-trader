from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from fastapi import FastAPI, Header, HTTPException

from app.config import Settings, get_settings
from app.persistence import TradingEventSink
from app.research_agent.strategy_runner import RUNNER_VERSION as STRATEGY_RUNNER_VERSION
from graen.crypto.candidate_shadow import CandidateForwardShadow


UTC = timezone.utc
SERVICE_VERSION = "graen-forward-shadow-service-v1"
STATE_SCHEMA_VERSION = "graen.forward-shadow-service.state.v1"
DEFAULT_STATE_PATH = "/data/graen-forward-shadow-state.json"
DEFAULT_MAX_ACTIVE = 8
TERMINAL_RAW_STATUSES = {"SHADOW_REJECTED", "READY_FOR_HUMAN_REVIEW"}


def _truthy(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().casefold() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int, *, minimum: int = 1, maximum: int = 64) -> int:
    try:
        return max(minimum, min(maximum, int(os.getenv(name, str(default)))))
    except (TypeError, ValueError):
        return default


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def _activation_id(payload: Mapping[str, Any]) -> str:
    material = {
        "problem_id": str(payload.get("problem_id") or ""),
        "campaign_id": str(payload.get("campaign_id") or ""),
        "candidate_methodology": str(payload.get("candidate_methodology") or ""),
        "candidate_spec": payload.get("candidate_spec") or {},
        "evidence_phase": str(payload.get("evidence_phase") or "FORWARD_SHADOW"),
    }
    return "shadow-" + hashlib.sha256(_canonical(material).encode("utf-8")).hexdigest()[:24]


def _safe_research_settings(settings: Settings) -> Settings:
    return settings.model_copy(
        update={
            "crypto_execution_enabled": False,
            "execution_enabled": False,
            "live_trading": False,
            "bot_armed": False,
            "scan_only": True,
        }
    )


class ForwardShadowService:
    """Multi-candidate, market-data-only forward shadow host.

    The service has no broker client and no order interface. It can observe,
    score, persist and retire shadow candidates only.
    """

    def __init__(
        self,
        settings: Settings,
        *,
        state_path: str | Path | None = None,
        max_active: int | None = None,
        token: str | None = None,
    ):
        self.settings = _safe_research_settings(settings)
        self.state_path = Path(
            state_path
            or os.getenv("GRAEN_SHADOW_STATE_PATH", DEFAULT_STATE_PATH)
        )
        self.max_active = int(max_active or _env_int("GRAEN_SHADOW_MAX_ACTIVE", DEFAULT_MAX_ACTIVE))
        self.token = str(
            token
            if token is not None
            else os.getenv("GRAEN_SHADOW_TOKEN", "")
            or os.getenv("GRAEN_GATEWAY_TOKEN", "")
        ).strip()
        self.poll_seconds = _env_int("GRAEN_SHADOW_POLL_SECONDS", 60, minimum=15, maximum=3600)
        self.autorun = _truthy("GRAEN_SHADOW_AUTORUN", True)
        self.runtimes: dict[str, CandidateForwardShadow] = {}
        self.retired: dict[str, dict[str, Any]] = {}
        self.started_at = datetime.now(UTC)
        self.last_cycle_at: datetime | None = None
        self.last_error: str | None = None
        self.stop_event = asyncio.Event()
        self.task: asyncio.Task | None = None
        self._lock = asyncio.Lock()
        self.event_sink = TradingEventSink(self.settings)

    @property
    def execution_authority(self) -> bool:
        return False

    @property
    def broker_orders_possible(self) -> bool:
        return False

    @property
    def configured(self) -> bool:
        return bool(
            self.settings.credentials_configured
            and len(self.token) >= 32
        )

    def require_token(self, supplied: str | None) -> None:
        if len(self.token) < 32:
            raise HTTPException(status_code=503, detail="shadow activation token is not configured")
        if supplied is None or not hmac.compare_digest(supplied, self.token):
            raise HTTPException(status_code=401, detail="Unauthorized")

    @staticmethod
    def _project_checkpoint(runtime: CandidateForwardShadow) -> dict[str, Any]:
        raw = dict(runtime.last_checkpoint or runtime._checkpoint())
        status = str(raw.get("status") or "COLLECTING")
        if (
            runtime.status().get("candidate_methodology") == STRATEGY_RUNNER_VERSION
            and status == "READY_FOR_HUMAN_REVIEW"
        ):
            status = "READY_FOR_PAPER"
        return {
            **raw,
            "raw_status": raw.get("status"),
            "status": status,
            "autonomy_state": (
                "PAPER_ELIGIBLE"
                if status == "READY_FOR_PAPER"
                else "REJECTED"
                if status == "SHADOW_REJECTED"
                else "COLLECTING_FORWARD_EVIDENCE"
            ),
            "execution_authority": False,
            "broker_orders_possible": False,
            "live_promotion_authorized": False,
        }

    def _state(self) -> dict[str, Any]:
        return {
            "schema_version": STATE_SCHEMA_VERSION,
            "saved_at": datetime.now(UTC).isoformat(),
            "runtimes": {
                activation_id: runtime.snapshot()
                for activation_id, runtime in sorted(self.runtimes.items())
            },
            "retired": dict(self.retired),
        }

    def _persist(self) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.state_path.with_suffix(self.state_path.suffix + ".tmp")
        temp.write_text(_canonical(self._state()) + "\n", encoding="utf-8")
        os.replace(temp, self.state_path)

    def restore(self) -> None:
        if not self.state_path.exists():
            return
        payload = json.loads(self.state_path.read_text(encoding="utf-8"))
        if payload.get("schema_version") != STATE_SCHEMA_VERSION:
            raise ValueError("unsupported_forward_shadow_state_schema")
        runtimes = payload.get("runtimes") or {}
        if not isinstance(runtimes, Mapping):
            raise ValueError("invalid_forward_shadow_runtime_state")
        restored: dict[str, CandidateForwardShadow] = {}
        for activation_id, snapshot in runtimes.items():
            if not isinstance(snapshot, Mapping):
                continue
            runtime = CandidateForwardShadow(self.settings)
            runtime.restore(snapshot)
            if runtime._activation_id() != str(activation_id):
                raise ValueError("forward_shadow_activation_identity_mismatch")
            restored[str(activation_id)] = runtime
        self.runtimes = restored
        retired = payload.get("retired") or {}
        self.retired = {
            str(key): dict(value)
            for key, value in dict(retired).items()
            if isinstance(value, Mapping)
        }

    def status(self) -> dict[str, Any]:
        running = self.task is not None and not self.task.done()
        rows = []
        for activation_id, runtime in sorted(self.runtimes.items()):
            checkpoint = self._project_checkpoint(runtime)
            rows.append({
                "activation_id": activation_id,
                "candidate_id": runtime.status().get("candidate_id"),
                "candidate_methodology": runtime.status().get("candidate_methodology"),
                "checkpoint": checkpoint,
                "last_processed_bar_end": runtime.status().get("last_processed_bar_end"),
                "last_error": runtime.status().get("last_error"),
            })
        return {
            "ok": bool(self.configured and self.last_error is None and (running or not self.autorun)),
            "system": "GRAEN",
            "service": "forward-shadow",
            "service_version": SERVICE_VERSION,
            "mode": "market_data_only",
            "configured": self.configured,
            "running": running,
            "autorun": self.autorun,
            "active_count": len(self.runtimes),
            "max_active": self.max_active,
            "retired_count": len(self.retired),
            "candidates": rows,
            "last_cycle_at": self.last_cycle_at.isoformat() if self.last_cycle_at else None,
            "last_error": self.last_error,
            "state_path": str(self.state_path),
            "execution_authority": False,
            "broker_orders_possible": False,
            "crypto_execution_enabled": False,
            "live_promotion_authorized": False,
        }

    def activate(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        candidate_spec = payload.get("candidate_spec")
        methodology = str(payload.get("candidate_methodology") or "").strip()
        if not methodology or not isinstance(candidate_spec, Mapping):
            raise ValueError("candidate_methodology_and_spec_required")
        activation_id = _activation_id(payload)
        existing = self.runtimes.get(activation_id)
        if existing is not None:
            return {
                "activation": dict(existing.activation or {}),
                "duplicate": True,
                "checkpoint": self._project_checkpoint(existing),
                "execution_authority": False,
                "broker_orders_possible": False,
            }
        if len(self.runtimes) >= self.max_active:
            raise ValueError("forward_shadow_capacity_reached")

        activation = {
            "schema_version": "graen.candidate_shadow.activation.v1",
            "activation_id": activation_id,
            "problem_id": str(payload.get("problem_id") or ""),
            "graen_run_id": str(payload.get("graen_run_id") or ""),
            "campaign_id": str(payload.get("campaign_id") or ""),
            "epoch_index": int(payload.get("epoch_index") or 0),
            "generation": int(payload.get("generation") or 0),
            "candidate_methodology": methodology,
            "candidate_id": str(candidate_spec.get("candidate_id") or ""),
            "candidate_spec": dict(candidate_spec),
            "velum_artifact_id": str(payload.get("velum_artifact_id") or ""),
            "evidence_phase": str(payload.get("evidence_phase") or "FORWARD_SHADOW"),
            "activated_at": datetime.now(UTC).isoformat(),
            "research_only": True,
            "promotion_authorized": False,
            "execution_authority": False,
            "broker_orders_possible": False,
        }
        runtime = CandidateForwardShadow(self.settings)
        runtime.activate(activation)
        self.runtimes[activation_id] = runtime
        self._persist()
        self._emit(
            "graen_candidate_shadow_activation",
            {
                "activation": activation,
                "checkpoint": self._project_checkpoint(runtime),
            },
            activation_id=activation_id,
        )
        return {
            "activation": activation,
            "duplicate": False,
            "checkpoint": self._project_checkpoint(runtime),
            "execution_authority": False,
            "broker_orders_possible": False,
        }

    def retire(self, activation_id: str, *, reason: str) -> dict[str, Any]:
        runtime = self.runtimes.pop(activation_id, None)
        if runtime is None:
            if activation_id in self.retired:
                return {"retired": True, "duplicate": True, **self.retired[activation_id]}
            raise KeyError("forward_shadow_activation_not_found")
        record = {
            "activation_id": activation_id,
            "candidate_id": runtime.status().get("candidate_id"),
            "candidate_methodology": runtime.status().get("candidate_methodology"),
            "checkpoint": self._project_checkpoint(runtime),
            "reason": str(reason or "retired"),
            "retired_at": datetime.now(UTC).isoformat(),
            "execution_authority": False,
        }
        self.retired[activation_id] = record
        self._persist()
        self._emit("graen_candidate_shadow_retired", record, activation_id=activation_id)
        return {"retired": True, "duplicate": False, **record}

    def _emit(self, event_type: str, payload: Mapping[str, Any], *, activation_id: str) -> None:
        self.event_sink.emit(
            event_type=event_type,
            event_key=f"graen-shadow:{activation_id}:{event_type}:{hashlib.sha256(_canonical(payload).encode()).hexdigest()[:16]}",
            payload={
                **dict(payload),
                "source_service": SERVICE_VERSION,
                "execution_authority": False,
                "broker_orders_possible": False,
            },
        )

    async def cycle_once(self) -> dict[str, Any]:
        events_emitted = 0
        terminal = 0
        errors: dict[str, str] = {}
        async with self._lock:
            for activation_id, runtime in list(self.runtimes.items()):
                raw_status = str((runtime.last_checkpoint or {}).get("status") or "")
                if raw_status in TERMINAL_RAW_STATUSES:
                    terminal += 1
                    continue
                try:
                    events = await runtime.cycle()
                    for event in events:
                        self._emit(
                            str(event.get("event_type") or "graen_candidate_shadow_event"),
                            dict(event),
                            activation_id=activation_id,
                        )
                    events_emitted += len(events)
                    projected = self._project_checkpoint(runtime)
                    if projected["status"] in {"READY_FOR_PAPER", "SHADOW_REJECTED"}:
                        terminal += 1
                        self._emit(
                            "graen_candidate_shadow_terminal_checkpoint",
                            {
                                "activation_id": activation_id,
                                "checkpoint": projected,
                            },
                            activation_id=activation_id,
                        )
                except Exception as exc:
                    runtime.last_error = f"{type(exc).__name__}: {exc}"[:1000]
                    errors[activation_id] = runtime.last_error
            self.last_cycle_at = datetime.now(UTC)
            self.last_error = (
                "candidate_cycle_errors:" + ",".join(sorted(errors))
                if errors and len(errors) == len(self.runtimes)
                else None
            )
            self._persist()
        return {
            "candidate_count": len(self.runtimes),
            "events_emitted": events_emitted,
            "terminal_count": terminal,
            "errors": errors,
            "execution_authority": False,
            "broker_orders_possible": False,
        }

    async def run(self) -> None:
        while not self.stop_event.is_set():
            try:
                await self.cycle_once()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.last_error = f"{type(exc).__name__}: {exc}"[:1000]
            try:
                await asyncio.wait_for(self.stop_event.wait(), timeout=self.poll_seconds)
            except asyncio.TimeoutError:
                pass

    async def start(self) -> None:
        self.restore()
        await self.event_sink.start()
        if self.autorun and self.task is None:
            self.task = asyncio.create_task(self.run(), name="graen-forward-shadow")

    async def stop(self) -> None:
        self.stop_event.set()
        if self.task is not None:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
            self.task = None
        self._persist()
        await self.event_sink.stop()


runtime = ForwardShadowService(get_settings())


@asynccontextmanager
async def lifespan(_: FastAPI):
    await runtime.start()
    try:
        yield
    finally:
        await runtime.stop()


app = FastAPI(title="ANEVUM GRAEN Forward Shadow", lifespan=lifespan)


@app.get("/health")
async def health():
    return runtime.status()


@app.get("/v1/candidate-shadow/status")
async def shadow_status(x_graen_shadow_token: str | None = Header(default=None)):
    runtime.require_token(x_graen_shadow_token)
    return runtime.status()


@app.get("/v1/candidate-shadow/checkpoint/{activation_id}")
async def shadow_checkpoint(
    activation_id: str,
    x_graen_shadow_token: str | None = Header(default=None),
):
    runtime.require_token(x_graen_shadow_token)
    candidate = runtime.runtimes.get(activation_id)
    if candidate is None:
        retired = runtime.retired.get(activation_id)
        if retired is not None:
            return {"activation_id": activation_id, "retired": True, **retired}
        raise HTTPException(status_code=404, detail="activation not found")
    return {
        "activation_id": activation_id,
        "retired": False,
        "checkpoint": runtime._project_checkpoint(candidate),
        "candidate": candidate.status(),
        "execution_authority": False,
        "broker_orders_possible": False,
    }


@app.post("/v1/candidate-shadow/activate")
async def shadow_activate(
    payload: dict[str, Any],
    x_graen_shadow_token: str | None = Header(default=None),
):
    runtime.require_token(x_graen_shadow_token)
    try:
        return runtime.activate(payload)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/v1/candidate-shadow/{activation_id}/retire")
async def shadow_retire(
    activation_id: str,
    payload: dict[str, Any] | None = None,
    x_graen_shadow_token: str | None = Header(default=None),
):
    runtime.require_token(x_graen_shadow_token)
    try:
        return runtime.retire(
            activation_id,
            reason=str((payload or {}).get("reason") or "retired"),
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
