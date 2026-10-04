from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel

from app.config import Settings
from app.persistence import TradingEventSink
from app.research_agent.strategy_runner import (
    RUNNER_VERSION as STRATEGY_RUNNER_VERSION,
)
from graen.crypto.candidate_shadow import CandidateForwardShadow


UTC = timezone.utc
SHADOW_HOST_VERSION = "graen.forward-shadow-host.v2"
STATE_SCHEMA_VERSION = "graen.forward-shadow-host.state.v2"
TERMINAL_STATUSES = {"SHADOW_REJECTED", "READY_FOR_PAPER"}


class ShadowActivationRequest(BaseModel):
    problem_id: str
    graen_run_id: str
    campaign_id: str
    epoch_index: int
    generation: int
    candidate_methodology: str
    candidate_spec: dict[str, Any]
    velum_artifact_id: str = ""
    evidence_phase: str = "FORWARD_SHADOW"


class CandidateShadowHost:
    """Durable broker-proof queue for forward-shadow candidates.

    One candidate is observed at a time to keep attribution deterministic.
    Additional validated candidates queue durably. Terminal evidence is retained
    by activation id so GRAEN can poll the exact candidate after the host has
    advanced to the next queued candidate.
    """

    def __init__(
        self,
        settings: Settings,
        event_sink: TradingEventSink,
        *,
        token: str | None = None,
        state_path: str | Path | None = None,
        poll_seconds: int | None = None,
    ) -> None:
        self.settings = settings.model_copy(
            update={
                "execution_enabled": False,
                "live_trading": False,
                "bot_armed": False,
                "crypto_execution_enabled": False,
                "scan_only": True,
            }
        )
        self.runtime = CandidateForwardShadow(self.settings)
        self.event_sink = event_sink
        self.token = str(
            token
            if token is not None
            else os.getenv("GRAEN_SHADOW_TOKEN", "")
            or os.getenv("GRAEN_GATEWAY_TOKEN", "")
        ).strip()
        default_root = str(
            os.getenv("RAILWAY_VOLUME_MOUNT_PATH", "") or "/data"
        ).strip()
        self.state_path = Path(
            state_path
            if state_path is not None
            else os.getenv(
                "GRAEN_SHADOW_STATE_PATH",
                str(Path(default_root) / "graen-candidate-shadow-state.json"),
            )
        )
        raw_poll = (
            poll_seconds
            if poll_seconds is not None
            else os.getenv("GRAEN_SHADOW_POLL_SECONDS", "60")
        )
        try:
            self.poll_seconds = max(15, min(int(raw_poll), 3600))
        except (TypeError, ValueError):
            self.poll_seconds = 60

        self.pending: list[dict[str, Any]] = []
        self.completed: dict[str, dict[str, Any]] = {}
        self.lock = asyncio.Lock()
        self.stop_event = asyncio.Event()
        self.task: asyncio.Task | None = None
        self.started_at = datetime.now(UTC)
        self.last_cycle_at: datetime | None = None
        self.last_persisted_at: datetime | None = None
        self.last_error: str | None = None

    @property
    def auth_configured(self) -> bool:
        return len(self.token) >= 32

    @property
    def running(self) -> bool:
        return self.task is not None and not self.task.done()

    @property
    def execution_authority(self) -> bool:
        return False

    @property
    def broker_orders_possible(self) -> bool:
        return False

    def require_token(self, supplied: str | None) -> None:
        if not self.auth_configured:
            raise HTTPException(
                status_code=503,
                detail="GRAEN_SHADOW_TOKEN is not configured",
            )
        if supplied is None or not hmac.compare_digest(supplied, self.token):
            raise HTTPException(status_code=401, detail="Unauthorized")

    @staticmethod
    def _activation_material(payload: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "problem_id": str(payload.get("problem_id") or ""),
            "graen_run_id": str(payload.get("graen_run_id") or ""),
            "campaign_id": str(payload.get("campaign_id") or ""),
            "epoch_index": int(payload.get("epoch_index") or 0),
            "generation": int(payload.get("generation") or 0),
            "candidate_methodology": str(
                payload.get("candidate_methodology") or ""
            ),
            "candidate_spec": dict(payload.get("candidate_spec") or {}),
            "velum_artifact_id": str(
                payload.get("velum_artifact_id") or ""
            ),
            "evidence_phase": str(
                payload.get("evidence_phase") or "FORWARD_SHADOW"
            ),
        }

    @classmethod
    def activation_id(cls, payload: Mapping[str, Any]) -> str:
        material = cls._activation_material(payload)
        # graen_run_id is a transient worker attempt identity. Excluding it makes
        # remote activation idempotent across crash/reclaim retries while the
        # frozen problem/candidate/corpus identity remains unchanged.
        material.pop("graen_run_id", None)
        digest = hashlib.sha256(
            json.dumps(
                material,
                sort_keys=True,
                separators=(",", ":"),
                default=str,
            ).encode("utf-8")
        ).hexdigest()
        return "shadow-" + digest[:32]

    @staticmethod
    def _activation_from_material(
        material: Mapping[str, Any],
        activation_id: str,
    ) -> dict[str, Any]:
        return {
            "schema_version": "graen.candidate_shadow.activation.v1",
            "activation_id": activation_id,
            **dict(material),
            "candidate_id": str(
                (material.get("candidate_spec") or {}).get("candidate_id")
                or ""
            ),
            "activated_at": datetime.now(UTC).isoformat(),
            "research_only": True,
            "promotion_authorized": False,
            "execution_authority": False,
            "broker_orders_possible": False,
            "live_execution_authorized": False,
        }

    @staticmethod
    def _project_checkpoint(runtime: CandidateForwardShadow) -> dict[str, Any]:
        shadow = runtime.status()
        raw = dict(shadow.get("last_checkpoint") or runtime._checkpoint())
        raw_status = str(
            shadow.get("last_checkpoint_status")
            or raw.get("status")
            or "COLLECTING"
        ).upper()
        status = raw_status
        if (
            shadow.get("candidate_methodology") == STRATEGY_RUNNER_VERSION
            and raw_status == "READY_FOR_HUMAN_REVIEW"
        ):
            status = "READY_FOR_PAPER"
        return {
            **raw,
            "raw_status": raw_status,
            "status": status,
            "candidate_id": shadow.get("candidate_id"),
            "candidate_methodology": shadow.get("candidate_methodology"),
            "execution_authority": False,
            "broker_orders_possible": False,
            "live_execution_authorized": False,
            "promotion_authorized": False,
        }

    def _state_payload(self) -> dict[str, Any]:
        return {
            "schema_version": STATE_SCHEMA_VERSION,
            "saved_at": datetime.now(UTC).isoformat(),
            "active": (
                self.runtime.snapshot()
                if self.runtime.active
                else None
            ),
            "pending": list(self.pending),
            "completed": dict(self.completed),
        }

    def _atomic_write(self, payload: Mapping[str, Any]) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.state_path.with_suffix(
            self.state_path.suffix + ".tmp"
        )
        encoded = (
            json.dumps(
                payload,
                sort_keys=True,
                separators=(",", ":"),
                default=str,
            )
            + "\n"
        )
        with tmp.open("w", encoding="utf-8") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, self.state_path)
        self.last_persisted_at = datetime.now(UTC)

    async def persist(self) -> None:
        await asyncio.to_thread(
            self._atomic_write,
            self._state_payload(),
        )

    def _restore_sync(self) -> None:
        if not self.state_path.exists():
            return
        with self.state_path.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
        if not isinstance(payload, Mapping):
            raise ValueError("candidate shadow state must be an object")

        # Backward-compatible restore from the original single-runtime file.
        if payload.get("schema_version") != STATE_SCHEMA_VERSION:
            self.runtime.restore(payload)
            self.pending = []
            self.completed = {}
            return

        active = payload.get("active")
        self.runtime = CandidateForwardShadow(self.settings)
        if isinstance(active, Mapping):
            self.runtime.restore(active)
        pending = payload.get("pending") or []
        self.pending = [
            dict(value)
            for value in pending
            if isinstance(value, Mapping)
        ]
        completed = payload.get("completed") or {}
        self.completed = {
            str(key): dict(value)
            for key, value in dict(completed).items()
            if isinstance(value, Mapping)
        }

    async def restore(self) -> None:
        try:
            await asyncio.to_thread(self._restore_sync)
            self.last_error = None
        except Exception as exc:
            self.runtime = CandidateForwardShadow(self.settings)
            self.pending = []
            self.completed = {}
            self.last_error = (
                "restore_failed:" + type(exc).__name__ + ":" + str(exc)
            )[:1000]

    def _emit(self, event: Mapping[str, Any]) -> None:
        event_type = str(
            event.get("event_type") or "graen_candidate_shadow_event"
        )
        occurred_at = str(
            event.get("occurred_at") or datetime.now(UTC).isoformat()
        )
        symbol = str(event.get("symbol") or "")
        payload = dict(event.get("payload") or {})
        activation_id = str(
            payload.get("activation_id")
            or (self.runtime.activation or {}).get("activation_id")
            or "inactive"
        )
        material = {
            "event_type": event_type,
            "activation_id": activation_id,
            "symbol": symbol,
            "occurred_at": occurred_at,
            "payload": payload,
        }
        event_key = (
            "graen:shadow:"
            + hashlib.sha256(
                json.dumps(
                    material,
                    sort_keys=True,
                    separators=(",", ":"),
                    default=str,
                ).encode("utf-8")
            ).hexdigest()
        )[:200]
        self.event_sink.emit(
            event_type=event_type,
            event_key=event_key,
            occurred_at=occurred_at,
            symbol=symbol,
            correlation_id=activation_id,
            payload={
                **payload,
                "source": "graen-forward-shadow-host",
                "research_only": True,
                "execution_authority": False,
                "broker_orders_possible": False,
                "live_execution_authorized": False,
            },
        )

    def _activate_now(self, activation: Mapping[str, Any]) -> None:
        runtime = CandidateForwardShadow(self.settings)
        runtime.activate(dict(activation))
        self.runtime = runtime

    def _active_id(self) -> str:
        return str(
            (self.runtime.activation or {}).get("activation_id") or ""
        )

    def _complete_active(self) -> None:
        activation_id = self._active_id()
        if not activation_id:
            return
        checkpoint = self._project_checkpoint(self.runtime)
        self.completed[activation_id] = {
            "activation": dict(self.runtime.activation or {}),
            "checkpoint": checkpoint,
            "candidate": self.runtime.status(),
            "completed_at": datetime.now(UTC).isoformat(),
        }
        self._emit(
            {
                "event_type": "graen_candidate_shadow_terminal_checkpoint",
                "occurred_at": datetime.now(UTC).isoformat(),
                "payload": {
                    "activation_id": activation_id,
                    "checkpoint": checkpoint,
                },
            }
        )
        self.runtime = CandidateForwardShadow(self.settings)

    def _advance_queue(self) -> None:
        if self.runtime.active or not self.pending:
            return
        activation = self.pending.pop(0)
        self._activate_now(activation)
        self._emit(
            {
                "event_type": "graen_candidate_shadow_activated",
                "occurred_at": activation.get("activated_at"),
                "payload": dict(activation),
            }
        )

    async def activate(
        self,
        payload: Mapping[str, Any],
    ) -> dict[str, Any]:
        material = self._activation_material(payload)
        if not all(
            str(material[key]).strip()
            for key in (
                "problem_id",
                "graen_run_id",
                "campaign_id",
                "candidate_methodology",
            )
        ):
            raise ValueError("complete shadow activation identity is required")
        if not material["candidate_spec"]:
            raise ValueError("candidate_spec is required")

        activation_id = self.activation_id(material)
        async with self.lock:
            if self._active_id() == activation_id:
                return {
                    "activation": dict(self.runtime.activation or {}),
                    "duplicate": True,
                    "queued": False,
                    "checkpoint": self._project_checkpoint(self.runtime),
                    "execution_authority": False,
                    "broker_orders_possible": False,
                    "live_execution_authorized": False,
                }
            for queued in self.pending:
                if str(queued.get("activation_id") or "") == activation_id:
                    return {
                        "activation": dict(queued),
                        "duplicate": True,
                        "queued": True,
                        "checkpoint": {
                            "status": "QUEUED",
                            "execution_authority": False,
                        },
                        "execution_authority": False,
                        "broker_orders_possible": False,
                        "live_execution_authorized": False,
                    }
            completed = self.completed.get(activation_id)
            if completed is not None:
                return {
                    "activation": dict(completed.get("activation") or {}),
                    "duplicate": True,
                    "queued": False,
                    "checkpoint": dict(completed.get("checkpoint") or {}),
                    "execution_authority": False,
                    "broker_orders_possible": False,
                    "live_execution_authorized": False,
                }

            activation = self._activation_from_material(
                material,
                activation_id,
            )
            queued = self.runtime.active
            if queued:
                self.pending.append(activation)
                self._emit(
                    {
                        "event_type": "graen_candidate_shadow_queued",
                        "occurred_at": datetime.now(UTC).isoformat(),
                        "payload": activation,
                    }
                )
            else:
                self._activate_now(activation)
                self._emit(
                    {
                        "event_type": "graen_candidate_shadow_activated",
                        "occurred_at": activation["activated_at"],
                        "payload": activation,
                    }
                )
            await self.persist()
            return {
                "activation": activation,
                "duplicate": False,
                "queued": queued,
                "checkpoint": {
                    "status": (
                        "QUEUED"
                        if queued
                        else self._project_checkpoint(self.runtime)["status"]
                    ),
                    "execution_authority": False,
                },
                "execution_authority": False,
                "broker_orders_possible": False,
                "live_execution_authorized": False,
            }

    def checkpoint(self, activation_id: str) -> dict[str, Any] | None:
        activation_id = str(activation_id or "").strip()
        if not activation_id:
            return None
        if self._active_id() == activation_id:
            return {
                "activation_id": activation_id,
                "queued": False,
                "completed": False,
                "checkpoint": self._project_checkpoint(self.runtime),
                "candidate": self.runtime.status(),
                "execution_authority": False,
                "broker_orders_possible": False,
            }
        for queued in self.pending:
            if str(queued.get("activation_id") or "") == activation_id:
                return {
                    "activation_id": activation_id,
                    "queued": True,
                    "completed": False,
                    "checkpoint": {
                        "status": "QUEUED",
                        "candidate_id": queued.get("candidate_id"),
                        "candidate_methodology": queued.get(
                            "candidate_methodology"
                        ),
                        "execution_authority": False,
                        "broker_orders_possible": False,
                    },
                    "candidate": {
                        "candidate_id": queued.get("candidate_id"),
                        "candidate_methodology": queued.get(
                            "candidate_methodology"
                        ),
                    },
                    "execution_authority": False,
                    "broker_orders_possible": False,
                }
        completed = self.completed.get(activation_id)
        if completed is not None:
            return {
                "activation_id": activation_id,
                "queued": False,
                "completed": True,
                "checkpoint": dict(completed.get("checkpoint") or {}),
                "candidate": dict(completed.get("candidate") or {}),
                "execution_authority": False,
                "broker_orders_possible": False,
            }
        return None

    async def cycle_once(self) -> list[dict[str, Any]]:
        async with self.lock:
            self._advance_queue()
            if not self.runtime.active:
                return []
            events = await self.runtime.cycle()
            self.last_cycle_at = datetime.now(UTC)
            for event in events:
                self._emit(event)
            checkpoint = self._project_checkpoint(self.runtime)
            if checkpoint["status"] in TERMINAL_STATUSES:
                self._complete_active()
                self._advance_queue()
            if events or checkpoint["status"] in TERMINAL_STATUSES:
                await self.persist()
            self.last_error = None
            return events

    async def _run(self) -> None:
        while not self.stop_event.is_set():
            try:
                await self.cycle_once()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.runtime.last_error = (
                    type(exc).__name__ + ": " + str(exc)
                )[:1000]
                self.last_error = self.runtime.last_error
            try:
                await asyncio.wait_for(
                    self.stop_event.wait(),
                    timeout=self.poll_seconds,
                )
            except asyncio.TimeoutError:
                pass

    async def start(self) -> None:
        if self.task is not None:
            return
        self.stop_event.clear()
        await self.restore()
        self.task = asyncio.create_task(
            self._run(),
            name="graen-candidate-forward-shadow",
        )

    async def stop(self) -> None:
        self.stop_event.set()
        if self.task is not None:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
            self.task = None
        try:
            async with self.lock:
                await self.persist()
        except Exception as exc:
            self.last_error = (
                "persist_failed:" + type(exc).__name__ + ":" + str(exc)
            )[:1000]

    def status(self) -> dict[str, Any]:
        shadow = self.runtime.status()
        checkpoint = (
            self._project_checkpoint(self.runtime)
            if self.runtime.active
            else {}
        )
        return {
            "ok": self.last_error is None,
            "system": "GRAEN",
            "service": "candidate-forward-shadow",
            "host_version": SHADOW_HOST_VERSION,
            "running": self.running,
            "auth_configured": self.auth_configured,
            "active": self.runtime.active,
            "queue_count": len(self.pending),
            "completed_count": len(self.completed),
            "current_activity": (
                "Forward-shadow observation "
                + str(shadow.get("candidate_id") or "")
                if self.runtime.active
                else "Awaiting validated candidate"
            ),
            "last_cycle_at": (
                self.last_cycle_at.isoformat()
                if self.last_cycle_at
                else None
            ),
            "last_persisted_at": (
                self.last_persisted_at.isoformat()
                if self.last_persisted_at
                else None
            ),
            "last_error": self.last_error or shadow.get("last_error"),
            "state_path": str(self.state_path),
            "checkpoint": checkpoint,
            "checkpoint_status": checkpoint.get("status"),
            "candidate_id": shadow.get("candidate_id"),
            "candidate_methodology": shadow.get(
                "candidate_methodology"
            ),
            "execution_authority": False,
            "broker_orders_possible": False,
            "crypto_execution_enabled": False,
            "live_execution_authorized": False,
        }


def create_shadow_router(host: CandidateShadowHost) -> APIRouter:
    router = APIRouter()

    @router.get("/v1/candidate-shadow/health")
    async def candidate_shadow_health() -> dict[str, Any]:
        status = host.status()
        return {
            "ok": status["ok"],
            "system": status["system"],
            "service": status["service"],
            "running": status["running"],
            "auth_configured": status["auth_configured"],
            "active": status["active"],
            "queue_count": status["queue_count"],
            "checkpoint_status": status["checkpoint_status"],
            "execution_authority": False,
            "broker_orders_possible": False,
        }

    @router.get("/v1/candidate-shadow/status")
    async def candidate_shadow_status(
        x_graen_shadow_token: str | None = Header(default=None),
    ) -> dict[str, Any]:
        host.require_token(x_graen_shadow_token)
        return host.status()

    @router.get("/v1/candidate-shadow/checkpoint/{activation_id}")
    async def candidate_shadow_checkpoint(
        activation_id: str,
        x_graen_shadow_token: str | None = Header(default=None),
    ) -> dict[str, Any]:
        host.require_token(x_graen_shadow_token)
        checkpoint = host.checkpoint(activation_id)
        if checkpoint is None:
            raise HTTPException(status_code=404, detail="activation not found")
        return checkpoint

    @router.post("/v1/candidate-shadow/activate")
    async def candidate_shadow_activate(
        request: ShadowActivationRequest,
        x_graen_shadow_token: str | None = Header(default=None),
    ) -> dict[str, Any]:
        host.require_token(x_graen_shadow_token)
        try:
            return await host.activate(request.model_dump())
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    return router
