from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import os
from typing import Any, Mapping

import httpx
from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel

from app.config import Settings, get_settings
from app.market_data import MarketDataClient
from app.slack_brand import decorate_slack_message
from .research_v6 import (
    CONTEXT_UNIVERSE,
    METHODOLOGY_VERSION,
    STRATEGY_VERSION_ID,
    run_crypto_research_v6,
)
from .shadow_v6 import CryptoResidualReclaimShadow
from .candidate_shadow import CandidateForwardShadow
from .btc_slow_momentum_v14_r2f import (
    candidate_spec as v14_r2f_candidate_spec,
)
from .btc_consensus_trend_v14_r2g import (
    METHODOLOGY_VERSION as V14_R2G_METHODOLOGY_VERSION,
    candidate_spec as v14_r2g_candidate_spec,
)


DEVELOPMENT_START = datetime(2025, 9, 1, tzinfo=timezone.utc)
VALIDATION_START = datetime(2025, 10, 15, tzinfo=timezone.utc)
HOLDOUT_START = datetime(2025, 11, 15, tzinfo=timezone.utc)
HOLDOUT_END = datetime(2025, 12, 1, tzinfo=timezone.utc)

PREVIOUSLY_INSPECTED_RANGES = (
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


def _env_int(name: str, default: int, *, minimum: int = 1) -> int:
    try:
        return max(int(os.getenv(name, str(default))), minimum)
    except ValueError:
        return default


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _research_settings(settings: Settings) -> Settings:
    return settings.model_copy(
        update={
            "bar_timeframe": "5Min",
            "market_data_batch_size": len(CONTEXT_UNIVERSE),
            "dynamic_universe_enabled": False,
            "crypto_execution_enabled": False,
        }
    )


class GraenCryptoV6Runtime:
    def __init__(self, settings: Settings):
        self.settings = _research_settings(settings)
        self.market_data = MarketDataClient(self.settings)
        self.stop_event = asyncio.Event()
        self.task: asyncio.Task | None = None
        self.shadow_task: asyncio.Task | None = None
        self.legacy_v6_research_enabled = _env_bool(
            "GRAEN_LEGACY_V6_RESEARCH_ENABLED",
            False,
        )
        self.legacy_v6_shadow_enabled = _env_bool(
            "GRAEN_LEGACY_V6_SHADOW_ENABLED",
            False,
        )
        self.interval_seconds = _env_int("GRAEN_CRYPTO_V6_INTERVAL_SECONDS", 86400, minimum=3600)
        self.shadow_interval_seconds = _env_int(
            "GRAEN_CRYPTO_V6_SHADOW_INTERVAL_SECONDS",
            30,
            minimum=15,
        )
        self.shadow = CryptoResidualReclaimShadow(self.settings)
        self.candidate_shadow = CandidateForwardShadow(self.settings)
        self.comparison_shadow = CandidateForwardShadow(self.settings)
        self.candidate_shadow_task: asyncio.Task | None = None
        self.comparison_shadow_task: asyncio.Task | None = None
        self.candidate_shadow_interval_seconds = _env_int(
            "GRAEN_CANDIDATE_SHADOW_INTERVAL_SECONDS",
            30,
            minimum=15,
        )
        self.last_error: str | None = None
        self.last_result_summary: dict[str, Any] | None = None
        self.started_at = datetime.now(timezone.utc)

    def status(self) -> dict[str, Any]:
        return {
            "ok": self.last_error is None,
            "system": "GRAEN",
            "program": "Crypto Research Gateway",
            "methodology_version": METHODOLOGY_VERSION,
            "strategy_version_id": STRATEGY_VERSION_ID,
            "running": self.task is not None and not self.task.done(),
            "execution_authority": False,
            "broker_orders_possible": False,
            "crypto_execution_enabled": False,
            "runtime_provenance": {
                "system_version": "graen-crypto-research-gateway-v1",
                "git_commit": os.getenv("RAILWAY_GIT_COMMIT_SHA") or None,
                "deployment_id": os.getenv("RAILWAY_DEPLOYMENT_ID") or None,
                "runtime_started_at": self.started_at.isoformat(),
            },
            "fresh_corpus": {
                "development_start": DEVELOPMENT_START.isoformat(),
                "validation_start": VALIDATION_START.isoformat(),
                "holdout_start": HOLDOUT_START.isoformat(),
                "holdout_end": HOLDOUT_END.isoformat(),
            },
            "last_error": self.last_error,
            "last_result_summary": self.last_result_summary,
            "legacy_v6": {
                "research_enabled": self.legacy_v6_research_enabled,
                "shadow_enabled": self.legacy_v6_shadow_enabled,
            },
            "shadow": self.shadow.status(),
            "candidate_shadow": self.candidate_shadow.status(),
            "candidate_shadow_comparison": self.comparison_shadow.status(),
        }

    async def start(self) -> None:
        await self._restore_candidate_shadow(
            self.candidate_shadow,
            candidate_id=v14_r2f_candidate_spec().candidate_id,
        )
        await self._restore_candidate_shadow(
            self.comparison_shadow,
            candidate_id=v14_r2g_candidate_spec().candidate_id,
        )
        if self.legacy_v6_research_enabled and self.task is None:
            self.task = asyncio.create_task(self._run(), name="graen-crypto-native-v6")
        if self.legacy_v6_shadow_enabled and self.shadow_task is None:
            self.shadow_task = asyncio.create_task(
                self._run_shadow(),
                name="graen-crypto-native-v6-shadow",
            )
        if self.candidate_shadow_task is None:
            self.candidate_shadow_task = asyncio.create_task(
                self._run_candidate_shadow(
                    self.candidate_shadow,
                    role="primary",
                ),
                name="graen-candidate-forward-shadow",
            )
        if self.comparison_shadow_task is None:
            self.comparison_shadow_task = asyncio.create_task(
                self._run_candidate_shadow(
                    self.comparison_shadow,
                    role="comparison",
                ),
                name="graen-candidate-forward-shadow-comparison",
            )

    async def stop(self) -> None:
        self.stop_event.set()
        tasks = [
            task
            for task in (
                self.task,
                self.shadow_task,
                self.candidate_shadow_task,
                self.comparison_shadow_task,
            )
            if task is not None
        ]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self.task = None
        self.shadow_task = None
        self.candidate_shadow_task = None
        self.comparison_shadow_task = None

    async def _run(self) -> None:
        while not self.stop_event.is_set():
            try:
                await self.run_once()
                self.last_error = None
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.last_error = f"{type(exc).__name__}: {exc}"
                print("GRAEN_CRYPTO_V6_ERROR", {"error": self.last_error}, flush=True)
                await self._emit(
                    "crypto_native_research_v6_error",
                    {
                        "methodology_version": METHODOLOGY_VERSION,
                        "strategy_version_id": STRATEGY_VERSION_ID,
                        "error": self.last_error,
                        "execution_authority": False,
                        "broker_orders_possible": False,
                    },
                    key_suffix=hashlib.sha256(self.last_error.encode()).hexdigest()[:16],
                )
            try:
                await asyncio.wait_for(self.stop_event.wait(), timeout=self.interval_seconds)
            except asyncio.TimeoutError:
                pass

    async def _run_shadow(self) -> None:
        while not self.stop_event.is_set():
            try:
                events = await self.shadow.cycle()
                for event in events:
                    event_type = str(event.get("event_type") or "crypto_shadow_v6_event")
                    symbol = str(event.get("symbol") or "")
                    occurred_at = str(event.get("occurred_at") or datetime.now(timezone.utc).isoformat())
                    payload = dict(event.get("payload") or {})
                    payload.update({
                        "system": "GRAEN",
                        "program": "Crypto Native Research v6 Shadow",
                        "strategy_version_id": STRATEGY_VERSION_ID,
                        "mode": "shadow",
                        "execution_authority": False,
                        "broker_orders_possible": False,
                        "crypto_execution_enabled": False,
                    })
                    key_material = f"{event_type}:{symbol}:{occurred_at}"
                    await self._emit(
                        event_type,
                        payload,
                        key_suffix=hashlib.sha256(key_material.encode()).hexdigest()[:24],
                        symbol=symbol,
                        occurred_at=occurred_at,
                    )
                    if event_type == "crypto_shadow_v6_entry":
                        await self._slack(
                            "*GRAEN // CRYPTO SHADOW ENTRY*\n"
                            + str(symbol)
                            + " | CRR-001 simulated entry"
                            + " | broker orders: disabled"
                        )
                    elif event_type == "crypto_shadow_v6_exit":
                        net = payload.get("stressed_cost_net_return")
                        pct = (
                            f"{float(net) * 100:.3f}%"
                            if net is not None
                            else "n/a"
                        )
                        await self._slack(
                            "*GRAEN // CRYPTO SHADOW EXIT*\n"
                            + str(symbol)
                            + " | CRR-001 | stressed-cost return: "
                            + pct
                        )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                print(
                    "GRAEN_CRYPTO_V6_SHADOW_ERROR",
                    {"error": f"{type(exc).__name__}: {exc}"},
                    flush=True,
                )
            try:
                await asyncio.wait_for(
                    self.stop_event.wait(),
                    timeout=self.shadow_interval_seconds,
                )
            except asyncio.TimeoutError:
                pass

    @property
    def _report_read_url(self) -> str:
        ingest_url = str(self.settings.trading_ingest_url or "").strip()
        return (
            ingest_url.rsplit("/", 1)[0] + "/trading-report-read"
            if ingest_url
            else ""
        )

    async def _restore_candidate_shadow(
        self,
        shadow: CandidateForwardShadow | None = None,
        *,
        candidate_id: str | None = None,
    ) -> None:
        target = shadow or self.candidate_shadow
        url = self._report_read_url
        token = str(self.settings.trading_ingest_token or "").strip()
        if not url or not token:
            return
        try:
            params = {"latest": "graen_shadow"}
            if candidate_id:
                params["shadow_candidate_id"] = candidate_id
            async with httpx.AsyncClient(timeout=15.0) as http:
                response = await http.get(
                    url,
                    headers={"x-anevum-ingest-token": token},
                    params=params,
                )
                response.raise_for_status()
                payload = response.json()
            if not isinstance(payload, Mapping) or not payload.get("ok"):
                return
            activation_row = payload.get("activation")
            state_row = payload.get("state")
            activation = (
                activation_row.get("payload")
                if isinstance(activation_row, Mapping)
                else None
            )
            state = (
                state_row.get("payload")
                if isinstance(state_row, Mapping)
                else None
            )
            if isinstance(state, Mapping):
                target.restore(state)
            elif isinstance(activation, Mapping):
                target.activate(activation)
        except Exception as exc:
            print(
                "GRAEN_CANDIDATE_SHADOW_RESTORE_ERROR",
                {
                    "candidate_id": candidate_id,
                    "error": f"{type(exc).__name__}: {exc}",
                },
                flush=True,
            )

    async def _emit_candidate_shadow(
        self,
        event_type: str,
        payload: Mapping[str, Any],
        *,
        occurred_at: str,
        symbol: str = "",
    ) -> bool:
        url = str(self.settings.trading_ingest_url or "").strip()
        token = str(self.settings.trading_ingest_token or "").strip()
        if not url or not token:
            return False
        activation_id = str(payload.get("activation_id") or "")
        candidate_id = str(payload.get("candidate_id") or "")
        key_material = (
            activation_id
            + ":"
            + event_type
            + ":"
            + symbol
            + ":"
            + occurred_at
        )
        event = {
            "event_key": (
                "graen:forward-shadow:"
                + hashlib.sha256(key_material.encode()).hexdigest()
            )[:200],
            "run_id": None,
            "strategy_version_id": candidate_id or None,
            "event_type": event_type,
            "occurred_at": occurred_at,
            "symbol": symbol or None,
            "source": "graen-candidate-forward-shadow",
            "payload": {
                **dict(payload),
                "system": "GRAEN",
                "program": "Candidate Forward Shadow",
                "mode": "forward_shadow",
                "execution_authority": False,
                "broker_orders_possible": False,
                "crypto_execution_enabled": False,
            },
        }
        async with httpx.AsyncClient(timeout=30.0) as http:
            response = await http.post(
                url,
                headers={"x-anevum-ingest-token": token},
                json={"events": [event]},
            )
            response.raise_for_status()
        return True

    async def _sync_candidate_shadow_checkpoint(
        self,
        checkpoint: Mapping[str, Any],
        shadow: CandidateForwardShadow | None = None,
    ) -> bool:
        target = shadow or self.candidate_shadow
        url = os.getenv("GRAEN_GATEWAY_URL", "").strip()
        token = (
            os.getenv("GRAEN_GATEWAY_TOKEN", "").strip()
            or str(self.settings.trading_ingest_token or "").strip()
        )
        if not url or len(token) < 32:
            return False
        async with httpx.AsyncClient(timeout=15.0) as http:
            response = await http.post(
                url,
                headers={"x-graen-gateway-token": token},
                json={
                    "action": "shadow_checkpoint",
                    "problem_id": str(
                        (target.activation or {}).get("problem_id") or ""
                    ),
                    "activation_id": str(
                        checkpoint.get("activation_id") or ""
                    ),
                    "candidate_id": str(
                        checkpoint.get("candidate_id") or ""
                    ),
                    "status": str(checkpoint.get("status") or ""),
                    "evidence": dict(checkpoint),
                },
            )
            response.raise_for_status()
            payload = response.json()
        return bool(isinstance(payload, Mapping) and payload.get("ok"))

    async def _persist_candidate_shadow_state(
        self,
        shadow: CandidateForwardShadow | None = None,
    ) -> None:
        target = shadow or self.candidate_shadow
        state = target.snapshot()
        activation_id = str(state.get("activation_id") or "")
        if not activation_id:
            return
        stamp = (
            str(state.get("last_processed_bar_end") or "")
            or datetime.now(timezone.utc).isoformat()
        )
        persisted = await self._emit_candidate_shadow(
            "graen_candidate_shadow_state",
            state,
            occurred_at=stamp,
        )
        if not persisted:
            raise RuntimeError("candidate_shadow_state_not_persisted")

    async def activate_candidate_shadow(
        self,
        request: Mapping[str, Any],
    ) -> dict[str, Any]:
        candidate_spec = request.get("candidate_spec")
        if not isinstance(candidate_spec, Mapping):
            raise ValueError("candidate_spec_missing")
        candidate_id = str(candidate_spec.get("candidate_id") or "")
        if not candidate_id:
            raise ValueError("candidate_id_missing")
        candidate_methodology = str(
            request.get("candidate_methodology") or ""
        )
        problem_id = str(request.get("problem_id") or "")
        target_shadow = (
            self.comparison_shadow
            if candidate_methodology == V14_R2G_METHODOLOGY_VERSION
            else self.candidate_shadow
        )
        velum_artifact_id = str(
            request.get("velum_artifact_id") or ""
        )
        evidence_phase = str(request.get("evidence_phase") or "FORWARD_SHADOW").strip().upper()
        if evidence_phase not in {"FORWARD_SHADOW", "VALIDATION", "HOLDOUT"}:
            raise ValueError("invalid_candidate_shadow_evidence_phase")
        activation_key = ":".join(
            [
                problem_id,
                candidate_methodology,
                candidate_id,
                velum_artifact_id,
                evidence_phase,
            ]
        )
        activation_id = hashlib.sha256(
            activation_key.encode()
        ).hexdigest()[:32]
        activation = {
            "schema_version": "graen.candidate_shadow.activation.v1",
            "activation_id": activation_id,
            "problem_id": problem_id,
            "graen_run_id": str(request.get("graen_run_id") or ""),
            "campaign_id": str(request.get("campaign_id") or ""),
            "epoch_index": int(request.get("epoch_index") or 0),
            "generation": int(request.get("generation") or 0),
            "candidate_methodology": candidate_methodology,
            "candidate_id": candidate_id,
            "candidate_spec": dict(candidate_spec),
            "velum_artifact_id": velum_artifact_id,
            "evidence_phase": evidence_phase,
            "activated_at": datetime.now(timezone.utc).isoformat(),
            "research_only": True,
            "promotion_authorized": False,
            "execution_authority": False,
            "broker_orders_possible": False,
        }
        if (
            target_shadow.active
            and str(
                (target_shadow.activation or {}).get(
                    "activation_id"
                )
                or ""
            )
            == activation_id
        ):
            return {
                "activation": dict(
                    target_shadow.activation or {}
                ),
                "duplicate": True,
            }

        persisted = await self._emit_candidate_shadow(
            "graen_candidate_shadow_activation",
            activation,
            occurred_at=activation["activated_at"],
        )
        if not persisted:
            raise RuntimeError("candidate_shadow_activation_not_persisted")
        target_shadow.activate(activation)
        await self._persist_candidate_shadow_state(target_shadow)

        checkpoint = target_shadow._checkpoint()
        target_shadow.last_checkpoint = dict(checkpoint)
        target_shadow.last_checkpoint_status = str(
            checkpoint["status"]
        )
        persisted = await self._emit_candidate_shadow(
            "graen_candidate_shadow_checkpoint",
            checkpoint,
            occurred_at=activation["activated_at"],
        )
        if not persisted:
            raise RuntimeError("candidate_shadow_checkpoint_not_persisted")
        if not await self._sync_candidate_shadow_checkpoint(checkpoint, target_shadow):
            raise RuntimeError("candidate_shadow_checkpoint_not_synced")

        await self._slack(
            "*GRAEN // FORWARD SHADOW ACTIVATED*\n"
            + candidate_id
            + " | broker orders: disabled"
        )
        return {"activation": activation, "duplicate": False}

    async def _run_candidate_shadow(
        self,
        shadow: CandidateForwardShadow | None = None,
        *,
        role: str = "primary",
    ) -> None:
        target = shadow or self.candidate_shadow
        while not self.stop_event.is_set():
            before = target.snapshot()
            try:
                events = await target.cycle()
                if events:
                    for event in events:
                        event_type = str(
                            event.get("event_type")
                            or "graen_candidate_shadow_event"
                        )
                        payload = dict(event.get("payload") or {})
                        occurred_at = str(
                            event.get("occurred_at")
                            or datetime.now(timezone.utc).isoformat()
                        )
                        symbol = str(event.get("symbol") or "")
                        if not await self._emit_candidate_shadow(
                            event_type,
                            payload,
                            occurred_at=occurred_at,
                            symbol=symbol,
                        ):
                            raise RuntimeError(
                                f"candidate_shadow_event_not_persisted:{event_type}"
                            )
                        if event_type == "graen_candidate_shadow_checkpoint":
                            if not await self._sync_candidate_shadow_checkpoint(
                                payload, target
                            ):
                                raise RuntimeError(
                                    "candidate_shadow_checkpoint_not_synced"
                                )
                            status = str(payload.get("status") or "")
                            if status in {
                                "READY_FOR_HUMAN_REVIEW",
                                "SHADOW_REJECTED",
                            }:
                                await self._slack(
                                    "*GRAEN // FORWARD SHADOW "
                                    + status.replace("_", " ")
                                    + "*\n"
                                    + str(payload.get("candidate_id") or "")
                                    + " | trades: "
                                    + str(payload.get("trade_count") or 0)
                                    + " | broker orders: disabled"
                                )
                    await self._persist_candidate_shadow_state(target)
                target.last_error = None
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                try:
                    target.restore(before)
                except Exception:
                    pass
                target.last_error = (
                    f"{type(exc).__name__}: {exc}"
                )
                print(
                    "GRAEN_CANDIDATE_SHADOW_ERROR",
                    {"role": role, "error": target.last_error},
                    flush=True,
                )
            try:
                await asyncio.wait_for(
                    self.stop_event.wait(),
                    timeout=self.candidate_shadow_interval_seconds,
                )
            except asyncio.TimeoutError:
                pass

    async def run_once(self) -> dict[str, Any]:
        fetch_start = DEVELOPMENT_START - timedelta(hours=8)
        fetch_end = HOLDOUT_END + timedelta(minutes=135)
        raw: dict[str, list[dict[str, Any]]] = {symbol: [] for symbol in CONTEXT_UNIVERSE}
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

        bars: dict[str, list[dict[str, Any]]] = {}
        for symbol in CONTEXT_UNIVERSE:
            clean: list[dict[str, Any]] = []
            seen: set[str] = set()
            for bar in raw.get(symbol, []):
                identity = str(bar.get("t") or "")
                if identity in seen:
                    continue
                seen.add(identity)
                try:
                    stamp = datetime.fromisoformat(identity.replace("Z", "+00:00"))
                except ValueError:
                    continue
                if stamp.tzinfo is None:
                    stamp = stamp.replace(tzinfo=timezone.utc)
                stamp = stamp.astimezone(timezone.utc)
                if fetch_start <= stamp < fetch_end:
                    clean.append(bar)
            bars[symbol] = clean

        result = run_crypto_research_v6(
            bars_by_symbol=bars,
            development_start=DEVELOPMENT_START,
            validation_start=VALIDATION_START,
            holdout_start=HOLDOUT_START,
            holdout_end=HOLDOUT_END,
            corpus_provenance_verified=True,
            previously_inspected_ranges=PREVIOUSLY_INSPECTED_RANGES,
        )
        payload = {
            **result,
            "system": "GRAEN",
            "program": "Crypto Native Research v6",
            "runtime_git_commit": os.getenv("RAILWAY_GIT_COMMIT_SHA"),
            "requested_range": {
                "start": fetch_start.isoformat(),
                "end": fetch_end.isoformat(),
            },
            "corpus_provenance": {
                "verified": True,
                "prior_ranges": list(PREVIOUSLY_INSPECTED_RANGES),
                "selected_fresh_range": {
                    "development_start": DEVELOPMENT_START.isoformat(),
                    "validation_start": VALIDATION_START.isoformat(),
                    "holdout_start": HOLDOUT_START.isoformat(),
                    "holdout_end": HOLDOUT_END.isoformat(),
                },
            },
            "bar_counts": {symbol: len(rows) for symbol, rows in bars.items()},
            "execution_authority": False,
            "broker_orders_possible": False,
            "crypto_execution_enabled": False,
            "risk_or_sizing_authority": False,
            "live_configuration_changed": False,
        }
        persisted = await self._emit(
            "crypto_native_research_v6_result",
            payload,
            key_suffix=HOLDOUT_END.date().isoformat(),
        )
        self.last_result_summary = {
            "status": result.get("status"),
            "validation_passed": bool(result.get("validation_passed")),
            "holdout_opened": bool((result.get("holdout") or {}).get("opened")),
            "holdout_passed": bool((result.get("holdout") or {}).get("passed")),
            "development_trade_count": int(
                (((result.get("development") or {}).get("primary_reclaim") or {}).get("trade_count") or 0)
            ),
            "development_expectancy": (
                ((result.get("development") or {}).get("primary_reclaim") or {}).get("expectancy_per_trade")
            ),
            "validation_trade_count": int(
                (((result.get("validation") or {}).get("primary_reclaim") or {}).get("trade_count") or 0)
            ),
            "validation_expectancy": (
                ((result.get("validation") or {}).get("primary_reclaim") or {}).get("expectancy_per_trade")
            ),
            "persisted": persisted,
        }
        print("GRAEN_CRYPTO_V6_COMPLETE", self.last_result_summary, flush=True)
        await self._slack(
            "*GRAEN // CRYPTO NATIVE RESEARCH V6*\n"
            + "state: " + str(self.last_result_summary["status"])
            + " | development trades: " + str(self.last_result_summary["development_trade_count"])
            + " | validation trades: " + str(self.last_result_summary["validation_trade_count"])
            + " | holdout opened: " + ("yes" if self.last_result_summary["holdout_opened"] else "no")
            + " | mode: research-only"
        )
        return payload

    async def _emit(
        self,
        event_type: str,
        payload: dict[str, Any],
        *,
        key_suffix: str,
        symbol: str = "",
        occurred_at: str | None = None,
    ) -> bool:
        url = str(self.settings.trading_ingest_url or "").strip()
        token = str(self.settings.trading_ingest_token or "").strip()
        if not url or not token:
            return False
        event = {
            "event_key": (
                "graen:crypto-native-v6:"
                + event_type
                + ":"
                + METHODOLOGY_VERSION
                + ":"
                + key_suffix
            )[:200],
            "run_id": None,
            "strategy_version_id": STRATEGY_VERSION_ID,
            "event_type": event_type,
            "occurred_at": occurred_at or datetime.now(timezone.utc).isoformat(),
            "symbol": symbol or None,
            "source": "graen-crypto-native-v6",
            "payload": payload,
        }
        try:
            async with httpx.AsyncClient(timeout=60.0) as http:
                response = await http.post(
                    url,
                    headers={"x-anevum-ingest-token": token},
                    json={"events": [event]},
                )
                response.raise_for_status()
            return True
        except Exception as exc:
            print(
                "GRAEN_CRYPTO_V6_INGEST_ERROR",
                {
                    "event_type": event_type,
                    "error": f"{type(exc).__name__}: {exc}",
                },
                flush=True,
            )
            return False

    async def _slack(self, message: str) -> None:
        url = str(self.settings.slack_webhook_url or "").strip()
        if not url:
            return
        try:
            async with httpx.AsyncClient(timeout=8.0) as http:
                response = await http.post(url, json={"text": decorate_slack_message(message, system="GRAEN")})
                response.raise_for_status()
        except Exception as exc:
            print("GRAEN_CRYPTO_V6_SLACK_ERROR", {"error": type(exc).__name__}, flush=True)


class CandidateShadowActivationRequest(BaseModel):
    problem_id: str
    graen_run_id: str
    campaign_id: str
    epoch_index: int
    generation: int
    candidate_methodology: str
    candidate_spec: dict[str, Any]
    velum_artifact_id: str
    evidence_phase: str = "FORWARD_SHADOW"


def require_shadow_token(
    x_graen_shadow_token: str | None,
) -> None:
    # Match the research executor contract: a dedicated shadow token may
    # override the gateway token, but the existing gateway token is the
    # canonical no-new-secret fallback for internal GRAEN shadow activation.
    expected = (
        os.getenv("GRAEN_SHADOW_TOKEN", "")
        or os.getenv("GRAEN_GATEWAY_TOKEN", "")
    ).strip()
    if len(expected) < 32:
        raise HTTPException(
            status_code=503,
            detail="GRAEN shadow token is not configured",
        )
    if x_graen_shadow_token is None or not hmac.compare_digest(
        x_graen_shadow_token,
        expected,
    ):
        raise HTTPException(status_code=401, detail="Unauthorized")


settings = get_settings()
runtime = GraenCryptoV6Runtime(settings)


@asynccontextmanager
async def lifespan(_: FastAPI):
    await runtime.start()
    try:
        yield
    finally:
        await runtime.stop()


app = FastAPI(title="ANEVUM GRAEN Crypto Research Gateway", lifespan=lifespan)


@app.get("/health")
async def health():
    state = runtime.status()
    return {
        "ok": state["ok"],
        "system": state["system"],
        "program": state["program"],
        "running": state["running"],
        "execution_authority": False,
        "broker_orders_possible": False,
        "last_error": state["last_error"],
        "runtime_provenance": state["runtime_provenance"],
    }


@app.post("/v1/candidate-shadow/activate")
async def activate_candidate_shadow(
    request: CandidateShadowActivationRequest,
    x_graen_shadow_token: str | None = Header(default=None),
):
    require_shadow_token(x_graen_shadow_token)
    try:
        result = await runtime.activate_candidate_shadow(
            request.model_dump()
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {
        "ok": True,
        **result,
        "execution_authority": False,
        "broker_orders_possible": False,
        "promotion_authorized": False,
    }


@app.get("/status")
async def status():
    return runtime.status()
