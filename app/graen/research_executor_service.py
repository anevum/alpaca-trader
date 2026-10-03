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
from app.graen.research_promotion import ResearchPromotion, engineering_problem_ids
from graen.crypto.research_v7 import (
    CONTEXT_UNIVERSE,
    METHODOLOGY_VERSION as V7_METHODOLOGY_VERSION,
    RESEARCH_BATCH_ID as V7_RESEARCH_BATCH_ID,
    candidate_specs as v7_candidate_specs,
    verify_v7_corpus_contract,
    evaluate_v7_development,
    evaluate_v7_validation,
    evaluate_v7_holdout,
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
from graen.crypto.autonomous_campaign import (
    CAMPAIGN_ID as AUTONOMOUS_CAMPAIGN_ID,
    CONTEXT_UNIVERSE as AUTONOMOUS_UNIVERSE,
    MAX_GENERATIONS_PER_EPOCH,
    METHODOLOGY_PREFIX as AUTONOMOUS_METHODOLOGY_PREFIX,
    epoch_contract as autonomous_epoch_contract,
    evaluate_development as evaluate_autonomous_development,
    evaluate_holdout as evaluate_autonomous_holdout,
    evaluate_validation as evaluate_autonomous_validation,
    methodology_version as autonomous_methodology_version,
    next_epoch_index as autonomous_next_epoch_index,
    prespecification as autonomous_prespecification,
    stage_metadata as autonomous_stage_metadata,
)
from graen.crypto.activity_shock_v9 import (
    FAMILY as V9_FAMILY,
    METHODOLOGY_VERSION as V9_METHODOLOGY_VERSION,
    UNIVERSE as V9_UNIVERSE,
    candidate_specs as v9_candidate_specs,
    evaluate_development as evaluate_v9_development,
    evaluate_holdout as evaluate_v9_holdout,
    evaluate_validation as evaluate_v9_validation,
    verify_stage_corpus as verify_v9_stage_corpus,
)
from graen.crypto.trend_pullback_v10 import (
    FAMILY as V10_FAMILY,
    METHODOLOGY_VERSION as V10_METHODOLOGY_VERSION,
    UNIVERSE as V10_UNIVERSE,
    candidate_specs as v10_candidate_specs,
    evaluate_development as evaluate_v10_development,
    evaluate_holdout as evaluate_v10_holdout,
    evaluate_validation as evaluate_v10_validation,
    verify_stage_corpus as verify_v10_stage_corpus,
)
from graen.crypto.btc_trend_pullback_v11 import (
    CAMPAIGN_ID as V11_CAMPAIGN_ID,
    DEVELOPMENT_END as V11_DEVELOPMENT_END,
    DEVELOPMENT_START as V11_DEVELOPMENT_START,
    FAMILY as V11_FAMILY,
    METHODOLOGY_VERSION as V11_METHODOLOGY_VERSION,
    UNIVERSE as V11_UNIVERSE,
    candidate_specs as v11_candidate_specs,
    evaluate_development as evaluate_v11_development,
    verify_development_corpus as verify_v11_development_corpus,
)


UTC = timezone.utc
RUNTIME_VERSION = "graen-research-executor-v1.9.0"
PROBLEM_DOMAIN = "CRYPTO_STRATEGY_RESEARCH"

DEVELOPMENT_START = datetime(2025, 5, 1, tzinfo=UTC)
VALIDATION_START = datetime(2025, 7, 1, tzinfo=UTC)
HOLDOUT_START = datetime(2025, 8, 1, tzinfo=UTC)
HOLDOUT_END = datetime(2025, 9, 1, tzinfo=UTC)

LEADLAG_DEVELOPMENT_START = datetime(2025, 12, 1, tzinfo=UTC)
LEADLAG_VALIDATION_START = datetime(2025, 12, 21, tzinfo=UTC)
LEADLAG_HOLDOUT_START = datetime(2026, 1, 11, tzinfo=UTC)
LEADLAG_HOLDOUT_END = datetime(2026, 1, 31, 16, 0, tzinfo=UTC)
LEADLAG_STAGE_KEY = "CRYPTO_LEADLAG_R2_READY"

AUTONOMOUS_DEVELOPMENT_STAGE = "CRYPTO_AUTONOMOUS_DEVELOPMENT"
AUTONOMOUS_VALIDATION_STAGE = "CRYPTO_AUTONOMOUS_VALIDATION"
AUTONOMOUS_HOLDOUT_STAGE = "CRYPTO_AUTONOMOUS_HOLDOUT"
AUTONOMOUS_VELUM_STAGE = "CRYPTO_AUTONOMOUS_VELUM_REPLAY"
AUTONOMOUS_STAGE_KEYS = {
    AUTONOMOUS_DEVELOPMENT_STAGE,
    AUTONOMOUS_VALIDATION_STAGE,
    AUTONOMOUS_HOLDOUT_STAGE,
    AUTONOMOUS_VELUM_STAGE,
}

V9_CAMPAIGN_ID = "crypto-activity-shock-v9"
V9_DEVELOPMENT_STAGE = "CRYPTO_ACTIVITY_SHOCK_V9_DEVELOPMENT"
V9_VALIDATION_STAGE = "CRYPTO_ACTIVITY_SHOCK_V9_VALIDATION"
V9_HOLDOUT_STAGE = "CRYPTO_ACTIVITY_SHOCK_V9_HOLDOUT"
V9_VELUM_STAGE = "CRYPTO_ACTIVITY_SHOCK_V9_VELUM_REPLAY"
V9_STAGE_KEYS = {
    V9_DEVELOPMENT_STAGE,
    V9_VALIDATION_STAGE,
    V9_HOLDOUT_STAGE,
    V9_VELUM_STAGE,
}
V9_EPOCHS = (
    {
        "name": "PRE2024-A",
        "development_start": datetime(2022, 1, 1, tzinfo=UTC),
        "validation_start": datetime(2022, 7, 1, tzinfo=UTC),
        "holdout_start": datetime(2022, 10, 1, tzinfo=UTC),
        "holdout_end": datetime(2023, 1, 1, tzinfo=UTC),
        "development_availability_probe_disclosed": [
            "2022-01-01T00:00:00+00:00",
            "2022-01-03T00:00:00+00:00",
        ],
    },
    {
        "name": "PRE2024-B",
        "development_start": datetime(2023, 1, 1, tzinfo=UTC),
        "validation_start": datetime(2023, 7, 1, tzinfo=UTC),
        "holdout_start": datetime(2023, 10, 1, tzinfo=UTC),
        "holdout_end": datetime(2024, 1, 1, tzinfo=UTC),
        "development_availability_probe_disclosed": [
            "2023-01-01T00:00:00+00:00",
            "2023-01-05T00:00:00+00:00",
        ],
    },
)


def _v9_epoch_contract(epoch_index: int) -> dict[str, Any]:
    if epoch_index < 0 or epoch_index >= len(V9_EPOCHS):
        raise ValueError("v9_epoch_out_of_range")
    return dict(V9_EPOCHS[epoch_index])


def _v9_next_epoch(epoch_index: int) -> int | None:
    candidate = epoch_index + 1
    return candidate if candidate < len(V9_EPOCHS) else None

V10_CAMPAIGN_ID = "crypto-trend-pullback-v10"
V10_DEVELOPMENT_STAGE = "CRYPTO_TREND_PULLBACK_V10_DEVELOPMENT"
V10_VALIDATION_STAGE = "CRYPTO_TREND_PULLBACK_V10_VALIDATION"
V10_HOLDOUT_STAGE = "CRYPTO_TREND_PULLBACK_V10_HOLDOUT"
V10_VELUM_STAGE = "CRYPTO_TREND_PULLBACK_V10_VELUM_REPLAY"
V10_STAGE_KEYS = {
    V10_DEVELOPMENT_STAGE,
    V10_VALIDATION_STAGE,
    V10_HOLDOUT_STAGE,
    V10_VELUM_STAGE,
}
V10_EPOCHS = V9_EPOCHS

V11_DEVELOPMENT_STAGE = "CRYPTO_BTC_TREND_PULLBACK_V11_DEVELOPMENT"
V11_VELUM_STAGE = "CRYPTO_BTC_TREND_PULLBACK_V11_VELUM_REPLAY"
V11_STAGE_KEYS = {
    V11_DEVELOPMENT_STAGE,
    V11_VELUM_STAGE,
}


def _v10_epoch_contract(epoch_index: int) -> dict[str, Any]:
    if epoch_index < 0 or epoch_index >= len(V10_EPOCHS):
        raise ValueError("v10_epoch_out_of_range")
    return dict(V10_EPOCHS[epoch_index])


def _v10_next_epoch(epoch_index: int) -> int | None:
    candidate = epoch_index + 1
    return candidate if candidate < len(V10_EPOCHS) else None

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
        self.research_promotion = ResearchPromotion(self.gateway)
        self.callback_base_url = os.getenv("IREN_CALLBACK_BASE_URL", "").strip().rstrip("/")
        self.callback_token = os.getenv("IREN_CALLBACK_TOKEN", "").strip()
        self.velum_base_url = os.getenv("VELUM_SERVICE_URL", "").strip().rstrip("/")
        self.velum_token = (
            os.getenv("VELUM_GRAEN_TOKEN", "")
            or os.getenv("GRAEN_GATEWAY_TOKEN", "")
        ).strip()
        self.shadow_base_url = os.getenv(
            "RHEN_SHADOW_SERVICE_URL",
            "",
        ).strip().rstrip("/")
        self.shadow_token = (
            os.getenv("GRAEN_SHADOW_TOKEN", "")
            or os.getenv("GRAEN_GATEWAY_TOKEN", "")
        ).strip()
        self.stop_event = asyncio.Event()
        self.task: asyncio.Task | None = None
        self.started_at = datetime.now(UTC)
        self.last_heartbeat_at: datetime | None = None
        self.last_claim_at: datetime | None = None
        self.last_completion_at: datetime | None = None
        self.active_problem_id: str | None = None
        self.last_error: str | None = None
        self.last_result: dict[str, Any] | None = None
        self.last_observed_blocked_run_id: str | None = None
        self.active_methodology_version = V7_METHODOLOGY_VERSION

    @property
    def callback_configured(self) -> bool:
        return bool(self.callback_base_url.startswith("http") and len(self.callback_token) >= 32)

    @property
    def velum_configured(self) -> bool:
        return bool(self.velum_base_url.startswith("http") and len(self.velum_token) >= 32)

    @property
    def shadow_configured(self) -> bool:
        return bool(
            self.shadow_base_url.startswith("http")
            and len(self.shadow_token) >= 32
        )

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
            "supported_methodologies": [V7_METHODOLOGY_VERSION, LEADLAG_METHODOLOGY_VERSION, AUTONOMOUS_METHODOLOGY_PREFIX, V9_METHODOLOGY_VERSION, V10_METHODOLOGY_VERSION],
            "running": running,
            "autorun": self.autorun,
            "market_data_credentials_configured": bool(self.settings.credentials_configured),
            "gateway_configured": self.gateway.configured,
            "research_code_promotion_enabled": True,
            "runtime_github_authorization_configured": self.research_promotion.repository.configured,
            "iren_callback_configured": self.callback_configured,
            "velum_candidate_replay_configured": self.velum_configured,
            "forward_shadow_configured": self.shadow_configured,
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
                "generic_v7": {
                    "development_start": DEVELOPMENT_START.isoformat(),
                    "validation_start": VALIDATION_START.isoformat(),
                    "holdout_start": HOLDOUT_START.isoformat(),
                    "holdout_end": HOLDOUT_END.isoformat(),
                },
                "leadlag_r2_confirmatory": {
                    "development_start": LEADLAG_DEVELOPMENT_START.isoformat(),
                    "validation_start": LEADLAG_VALIDATION_START.isoformat(),
                    "holdout_start": LEADLAG_HOLDOUT_START.isoformat(),
                    "holdout_end": LEADLAG_HOLDOUT_END.isoformat(),
                },
                "activity_shock_v9": [
                    {
                        "epoch": row["name"],
                        "development_start": row["development_start"].isoformat(),
                        "validation_start": row["validation_start"].isoformat(),
                        "holdout_start": row["holdout_start"].isoformat(),
                        "holdout_end": row["holdout_end"].isoformat(),
                    }
                    for row in V9_EPOCHS
                ],
                "trend_pullback_v10": [
                    {
                        "epoch": row["name"],
                        "development_start": row["development_start"].isoformat(),
                        "validation_start": row["validation_start"].isoformat(),
                        "holdout_start": row["holdout_start"].isoformat(),
                        "holdout_end": row["holdout_end"].isoformat(),
                        "development_previously_inspected": True,
                        "validation_previously_inspected": False,
                        "holdout_previously_inspected": False,
                    }
                    for row in V10_EPOCHS
                ],
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

    async def _fetch_stage(
        self,
        symbols: tuple[str, ...],
        *,
        start: datetime,
        end: datetime,
        warmup_hours: int = 169,
    ) -> dict[str, list[dict[str, Any]]]:
        fetch_start = start - timedelta(hours=warmup_hours)
        # Stage boundaries are strict. Never fetch any bar at or beyond the
        # next sealed stage; provider end timestamps may be inclusive.
        fetch_end = end
        raw: dict[str, list[dict[str, Any]]] = {symbol: [] for symbol in symbols}
        chunk_start = fetch_start
        while chunk_start < fetch_end:
            chunk_end = min(chunk_start + timedelta(days=20), fetch_end)
            chunk = await self.market_data.historical_crypto_bars_many(
                list(symbols),
                start=chunk_start,
                end=chunk_end - timedelta(microseconds=1),
            )
            for symbol in symbols:
                raw[symbol].extend(chunk.get(symbol, []))
            chunk_start = chunk_end

        clean: dict[str, list[dict[str, Any]]] = {}
        for symbol in symbols:
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

    async def _finalize(
        self,
        *,
        problem: Mapping[str, Any],
        run: Mapping[str, Any],
        status: str,
        summary: dict[str, Any],
        next_stage: str | None = None,
        next_metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        problem_id = str(problem.get("problem_id"))
        run_id = str(run.get("run_id"))
        linked_job_id = (
            str(problem.get("linked_iren_job_id"))
            if problem.get("linked_iren_job_id")
            else None
        )
        await self.gateway.complete_research_problem(
            problem_id=problem_id,
            run_id=run_id,
            worker_id=self.worker_id,
            status=status,
            result_summary=summary,
            model_usage={"invoked": False},
        )

        if (
            status == "WAITING"
            and next_stage is None
            and summary.get("decision") == "CONTINUE_RESEARCH"
            and summary.get("next_action") == "DESIGN_NEXT_FROZEN_RESEARCH_BATCH"
        ):
            next_stage = AUTONOMOUS_DEVELOPMENT_STAGE
            next_metadata = {
                "campaign_id": AUTONOMOUS_CAMPAIGN_ID,
                "campaign_epoch": 0,
                "campaign_generation": 1,
                "campaign_origin_methodology": summary.get("methodology_version"),
            }

        if next_stage is not None:
            queued = await self.gateway.queue_research_stage(
                problem_id=problem_id,
                stage=next_stage,
                metadata=next_metadata or {},
            )
            summary["continuation_queued"] = bool(queued.get("problem"))
            summary["next_research_stage"] = next_stage
            if next_metadata:
                summary["next_research_metadata"] = next_metadata

        callback_status = "SUCCEEDED" if status == "SUCCEEDED" else "WAITING"
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
        print(
            "GRAEN_RESEARCH_RESULT",
            {
                key: summary.get(key)
                for key in (
                    "state", "status", "decision", "campaign_id", "epoch",
                    "epoch_index", "candidate_id", "candidate_family",
                    "validation_opened", "holdout_opened", "holdout_passed",
                    "next_action", "next_research_stage", "continuation_queued",
                    "execution_authority", "broker_orders_possible",
                )
                if key in summary
            },
            flush=True,
        )
        await self._heartbeat()
        return {"claimed": True, "problem_id": problem_id, **summary}

    async def _execute_v7_staged(
        self,
        problem: Mapping[str, Any],
        run: Mapping[str, Any],
    ) -> dict[str, Any]:
        problem_id = str(problem.get("problem_id"))
        run_id = str(run.get("run_id"))
        self.active_methodology_version = V7_METHODOLOGY_VERSION

        verify_v7_corpus_contract(
            development_start=DEVELOPMENT_START,
            validation_start=VALIDATION_START,
            holdout_start=HOLDOUT_START,
            holdout_end=HOLDOUT_END,
            corpus_provenance_verified=True,
            previously_inspected_ranges=PREVIOUSLY_INSPECTED_RANGES,
        )

        prespec = {
            "schema_version": "graen.crypto_v7.batch_specification.v2",
            "methodology_version": V7_METHODOLOGY_VERSION,
            "research_batch_id": V7_RESEARCH_BATCH_ID,
            "candidate_registry": [row.to_dict() for row in v7_candidate_specs()],
            "candidate_count": len(v7_candidate_specs()),
            "stage_order": ["DEVELOPMENT", "VALIDATION", "HOLDOUT"],
            "development": [DEVELOPMENT_START.isoformat(), VALIDATION_START.isoformat()],
            "validation": [VALIDATION_START.isoformat(), HOLDOUT_START.isoformat()],
            "holdout": [HOLDOUT_START.isoformat(), HOLDOUT_END.isoformat()],
            "validation_fetch_requires_development_survivor": True,
            "holdout_fetch_requires_validation_survivor": True,
            "corpus_provenance_verified": True,
            "previously_inspected_ranges": list(PREVIOUSLY_INSPECTED_RANGES),
            "source_commit": _source_commit(),
            "deployment_id": _deployment_id(),
            "problem_id": problem_id,
            "graen_run_id": run_id,
            "frozen_before_corpus_access": True,
            "authority": {
                "research_only": True,
                "execution_authority": False,
                "broker_orders_possible": False,
                "risk_or_sizing_authority": False,
                "production_promotion_authority": False,
            },
        }
        prespec_artifact = await self.gateway.record_artifact(
            problem_id=problem_id,
            run_id=run_id,
            artifact_type="CRYPTO_V7_BATCH_SPECIFICATION",
            methodology_version=V7_METHODOLOGY_VERSION,
            content=prespec,
        )

        development_bars = await self._fetch_stage(
            CONTEXT_UNIVERSE,
            start=DEVELOPMENT_START,
            end=VALIDATION_START,
            warmup_hours=8,
        )
        development = evaluate_v7_development(
            bars_by_symbol=development_bars,
            start=DEVELOPMENT_START,
            end=VALIDATION_START,
        )
        development_artifact = await self.gateway.record_artifact(
            problem_id=problem_id,
            run_id=run_id,
            artifact_type="CRYPTO_V7_DEVELOPMENT_RESULT",
            methodology_version=V7_METHODOLOGY_VERSION,
            content={
                **development,
                "bar_counts": {
                    symbol: len(rows) for symbol, rows in development_bars.items()
                },
                "source_commit": _source_commit(),
            },
        )

        result: dict[str, Any] = {
            "methodology_version": V7_METHODOLOGY_VERSION,
            "research_batch_id": V7_RESEARCH_BATCH_ID,
            "problem_id": problem_id,
            "graen_run_id": run_id,
            "source_commit": _source_commit(),
            "deployment_id": _deployment_id(),
            "research_only": True,
            "model_invoked": False,
            "execution_authority": False,
            "broker_orders_possible": False,
            "risk_or_sizing_authority": False,
            "production_promotion_authority": False,
            "production_state_changed": False,
            "prespec_artifact_id": (
                (prespec_artifact.get("artifact") or {}).get("artifact_id")
                if isinstance(prespec_artifact.get("artifact"), Mapping) else None
            ),
            "development_artifact_id": (
                (development_artifact.get("artifact") or {}).get("artifact_id")
                if isinstance(development_artifact.get("artifact"), Mapping) else None
            ),
            "development": development,
            "validation": {"opened": False},
            "holdout": {"opened": False},
        }

        if not development.get("survivors"):
            result.update({
                "status": "NO_DEVELOPMENT_SURVIVOR",
                "decision": "CONTINUE_RESEARCH",
                "next_action": "DESIGN_NEXT_FROZEN_RESEARCH_BATCH",
            })
            final_artifact = await self.gateway.record_artifact(
                problem_id=problem_id,
                run_id=run_id,
                artifact_type="CRYPTO_RESEARCH_BATCH_RESULT",
                methodology_version=V7_METHODOLOGY_VERSION,
                content=result,
            )
            summary = {
                "state": "RESEARCH_BATCH_COMPLETE",
                "decision": result["decision"],
                "status": result["status"],
                "methodology_version": V7_METHODOLOGY_VERSION,
                "research_batch_id": V7_RESEARCH_BATCH_ID,
                "candidate_count": len(v7_candidate_specs()),
                "development_survivors": [],
                "validation_opened": False,
                "holdout_opened": False,
                "artifact_id": (
                    (final_artifact.get("artifact") or {}).get("artifact_id")
                    if isinstance(final_artifact.get("artifact"), Mapping) else None
                ),
                "content_hash": final_artifact.get("content_hash"),
                "model_invoked": False,
                "execution_authority": False,
                "production_state_changed": False,
                "next_action": result["next_action"],
            }
            return await self._finalize(
                problem=problem,
                run=run,
                status="WAITING",
                summary=summary,
            )

        validation_bars = await self._fetch_stage(
            CONTEXT_UNIVERSE,
            start=VALIDATION_START,
            end=HOLDOUT_START,
            warmup_hours=8,
        )
        validation = evaluate_v7_validation(
            bars_by_symbol=validation_bars,
            development_results=development["results"],
            survivor_ids=development["survivors"],
            start=VALIDATION_START,
            end=HOLDOUT_START,
        )
        validation_artifact = await self.gateway.record_artifact(
            problem_id=problem_id,
            run_id=run_id,
            artifact_type="CRYPTO_V7_VALIDATION_RESULT",
            methodology_version=V7_METHODOLOGY_VERSION,
            content={
                **validation,
                "bar_counts": {
                    symbol: len(rows) for symbol, rows in validation_bars.items()
                },
                "source_commit": _source_commit(),
            },
        )
        result["validation"] = {
            **validation,
            "artifact_id": (
                (validation_artifact.get("artifact") or {}).get("artifact_id")
                if isinstance(validation_artifact.get("artifact"), Mapping) else None
            ),
        }

        selected = validation.get("selected_candidate_id")
        if not selected:
            result.update({
                "status": "NO_VALIDATION_SURVIVOR",
                "decision": "CONTINUE_RESEARCH",
                "next_action": "DESIGN_NEXT_FROZEN_RESEARCH_BATCH",
            })
            final_artifact = await self.gateway.record_artifact(
                problem_id=problem_id,
                run_id=run_id,
                artifact_type="CRYPTO_RESEARCH_BATCH_RESULT",
                methodology_version=V7_METHODOLOGY_VERSION,
                content=result,
            )
            summary = {
                "state": "RESEARCH_BATCH_COMPLETE",
                "decision": result["decision"],
                "status": result["status"],
                "methodology_version": V7_METHODOLOGY_VERSION,
                "research_batch_id": V7_RESEARCH_BATCH_ID,
                "candidate_count": len(v7_candidate_specs()),
                "development_survivors": development["survivors"],
                "validation_survivors": validation.get("survivors") or [],
                "selected_candidate": None,
                "holdout_opened": False,
                "artifact_id": (
                    (final_artifact.get("artifact") or {}).get("artifact_id")
                    if isinstance(final_artifact.get("artifact"), Mapping) else None
                ),
                "content_hash": final_artifact.get("content_hash"),
                "model_invoked": False,
                "execution_authority": False,
                "production_state_changed": False,
                "next_action": result["next_action"],
            }
            return await self._finalize(
                problem=problem,
                run=run,
                status="WAITING",
                summary=summary,
            )

        holdout_bars = await self._fetch_stage(
            CONTEXT_UNIVERSE,
            start=HOLDOUT_START,
            end=HOLDOUT_END,
            warmup_hours=8,
        )
        holdout = evaluate_v7_holdout(
            bars_by_symbol=holdout_bars,
            candidate_id=str(selected),
            start=HOLDOUT_START,
            end=HOLDOUT_END,
        )
        holdout_artifact = await self.gateway.record_artifact(
            problem_id=problem_id,
            run_id=run_id,
            artifact_type="CRYPTO_V7_HOLDOUT_RESULT",
            methodology_version=V7_METHODOLOGY_VERSION,
            content={
                **holdout,
                "bar_counts": {
                    symbol: len(rows) for symbol, rows in holdout_bars.items()
                },
                "source_commit": _source_commit(),
            },
        )
        result["holdout"] = {
            **holdout,
            "artifact_id": (
                (holdout_artifact.get("artifact") or {}).get("artifact_id")
                if isinstance(holdout_artifact.get("artifact"), Mapping) else None
            ),
        }
        passed = bool(holdout.get("passed"))
        result.update({
            "status": "HOLDOUT_PASS" if passed else "HOLDOUT_FAIL",
            "decision": "PROMOTE_TO_VELUM" if passed else "CONTINUE_RESEARCH",
            "selected_candidate": str(selected),
            "next_action": "VELUM_REPLAY" if passed else "DESIGN_NEXT_FROZEN_RESEARCH_BATCH",
        })
        final_artifact = await self.gateway.record_artifact(
            problem_id=problem_id,
            run_id=run_id,
            artifact_type="CRYPTO_RESEARCH_BATCH_RESULT",
            methodology_version=V7_METHODOLOGY_VERSION,
            content=result,
        )
        summary = {
            "state": "CANDIDATE_READY_FOR_VELUM" if passed else "RESEARCH_BATCH_COMPLETE",
            "decision": result["decision"],
            "status": result["status"],
            "methodology_version": V7_METHODOLOGY_VERSION,
            "research_batch_id": V7_RESEARCH_BATCH_ID,
            "candidate_count": len(v7_candidate_specs()),
            "development_survivors": development["survivors"],
            "validation_survivors": validation.get("survivors") or [],
            "selected_candidate": str(selected),
            "holdout_opened": True,
            "holdout_passed": passed,
            "artifact_id": (
                (final_artifact.get("artifact") or {}).get("artifact_id")
                if isinstance(final_artifact.get("artifact"), Mapping) else None
            ),
            "content_hash": final_artifact.get("content_hash"),
            "model_invoked": False,
            "execution_authority": False,
            "production_state_changed": False,
            "next_action": result["next_action"],
        }
        return await self._finalize(
            problem=problem,
            run=run,
            status="SUCCEEDED" if passed else "WAITING",
            summary=summary,
        )

    async def _execute_leadlag_r2(
        self,
        problem: Mapping[str, Any],
        run: Mapping[str, Any],
    ) -> dict[str, Any]:
        problem_id = str(problem.get("problem_id"))
        run_id = str(run.get("run_id"))
        self.active_methodology_version = LEADLAG_METHODOLOGY_VERSION

        prespec = {
            **leadlag_research_specification(),
            "corpus": {
                "development": [
                    LEADLAG_DEVELOPMENT_START.isoformat(),
                    LEADLAG_VALIDATION_START.isoformat(),
                ],
                "validation": [
                    LEADLAG_VALIDATION_START.isoformat(),
                    LEADLAG_HOLDOUT_START.isoformat(),
                ],
                "holdout": [
                    LEADLAG_HOLDOUT_START.isoformat(),
                    LEADLAG_HOLDOUT_END.isoformat(),
                ],
                "overlap_allowed": False,
                "holdout_fetch_before_validation_pass": False,
            },
            "source_commit": _source_commit(),
            "deployment_id": _deployment_id(),
            "problem_id": problem_id,
            "graen_run_id": run_id,
            "frozen_before_corpus_access": True,
        }
        prespec_artifact = await self.gateway.record_artifact(
            problem_id=problem_id,
            run_id=run_id,
            artifact_type="RESEARCH_HYPOTHESIS_SPECIFICATION_REVISION",
            methodology_version=LEADLAG_METHODOLOGY_VERSION,
            content=prespec,
        )

        development_bars = await self._fetch_stage(
            LEADLAG_UNIVERSE,
            start=LEADLAG_DEVELOPMENT_START,
            end=LEADLAG_VALIDATION_START,
        )
        development = evaluate_leadlag_stage(
            development_bars,
            start=LEADLAG_DEVELOPMENT_START,
            end=LEADLAG_VALIDATION_START,
            scenario="high",
            seed=82101,
        )
        development_passed, development_reasons = leadlag_development_gate(development)
        development_artifact = await self.gateway.record_artifact(
            problem_id=problem_id,
            run_id=run_id,
            artifact_type="CRYPTO_LEADLAG_DEVELOPMENT_RESULT",
            methodology_version=LEADLAG_METHODOLOGY_VERSION,
            content={
                "stage": "DEVELOPMENT",
                "passed": development_passed,
                "reasons": development_reasons,
                "bar_counts": {symbol: len(rows) for symbol, rows in development_bars.items()},
                "result": development,
                "source_commit": _source_commit(),
            },
        )

        result: dict[str, Any] = {
            "methodology_version": LEADLAG_METHODOLOGY_VERSION,
            "hypothesis_id": "CRYPTO-LEADLAG-001",
            "candidate_id": "CRYPTO-LEADLAG-001-R2",
            "prespec_revision": 2,
            "problem_id": problem_id,
            "graen_run_id": run_id,
            "source_commit": _source_commit(),
            "deployment_id": _deployment_id(),
            "research_only": True,
            "model_invoked": False,
            "execution_authority": False,
            "broker_orders_possible": False,
            "risk_or_sizing_authority": False,
            "production_promotion_authority": False,
            "production_state_changed": False,
            "prespec_artifact_id": (
                (prespec_artifact.get("artifact") or {}).get("artifact_id")
                if isinstance(prespec_artifact.get("artifact"), Mapping) else None
            ),
            "development_artifact_id": (
                (development_artifact.get("artifact") or {}).get("artifact_id")
                if isinstance(development_artifact.get("artifact"), Mapping) else None
            ),
            "development": {
                "opened": True,
                "passed": development_passed,
                "reasons": development_reasons,
                "result": development,
            },
            "validation": {"opened": False},
            "holdout": {"opened": False},
        }

        if not development_passed:
            result.update({
                "status": "REJECTED_IN_DEVELOPMENT",
                "decision": "CONTINUE_RESEARCH",
                "next_action": "DESIGN_NEXT_FROZEN_RESEARCH_BATCH",
            })
            final_artifact = await self.gateway.record_artifact(
                problem_id=problem_id,
                run_id=run_id,
                artifact_type="CRYPTO_LEADLAG_R2_RESULT",
                methodology_version=LEADLAG_METHODOLOGY_VERSION,
                content=result,
            )
            summary = {
                "state": "CANDIDATE_REJECTED_DEVELOPMENT",
                "decision": result["decision"],
                "status": result["status"],
                "methodology_version": LEADLAG_METHODOLOGY_VERSION,
                "candidate_id": result["candidate_id"],
                "development_passed": False,
                "validation_opened": False,
                "holdout_opened": False,
                "artifact_id": (
                    (final_artifact.get("artifact") or {}).get("artifact_id")
                    if isinstance(final_artifact.get("artifact"), Mapping) else None
                ),
                "content_hash": final_artifact.get("content_hash"),
                "model_invoked": False,
                "execution_authority": False,
                "production_state_changed": False,
                "next_action": result["next_action"],
            }
            return await self._finalize(
                problem=problem,
                run=run,
                status="WAITING",
                summary=summary,
            )

        validation_bars = await self._fetch_stage(
            LEADLAG_UNIVERSE,
            start=LEADLAG_VALIDATION_START,
            end=LEADLAG_HOLDOUT_START,
        )
        validation = evaluate_leadlag_stage(
            validation_bars,
            start=LEADLAG_VALIDATION_START,
            end=LEADLAG_HOLDOUT_START,
            scenario="high",
            seed=82201,
        )
        validation_passed, validation_reasons = leadlag_validation_gate(validation)
        validation_artifact = await self.gateway.record_artifact(
            problem_id=problem_id,
            run_id=run_id,
            artifact_type="CRYPTO_LEADLAG_VALIDATION_RESULT",
            methodology_version=LEADLAG_METHODOLOGY_VERSION,
            content={
                "stage": "VALIDATION",
                "passed": validation_passed,
                "reasons": validation_reasons,
                "bar_counts": {symbol: len(rows) for symbol, rows in validation_bars.items()},
                "result": validation,
                "source_commit": _source_commit(),
            },
        )
        result["validation"] = {
            "opened": True,
            "passed": validation_passed,
            "reasons": validation_reasons,
            "artifact_id": (
                (validation_artifact.get("artifact") or {}).get("artifact_id")
                if isinstance(validation_artifact.get("artifact"), Mapping) else None
            ),
            "result": validation,
        }

        if not validation_passed:
            result.update({
                "status": "REJECTED_IN_VALIDATION",
                "decision": "CONTINUE_RESEARCH",
                "next_action": "DESIGN_NEXT_FROZEN_RESEARCH_BATCH",
            })
            final_artifact = await self.gateway.record_artifact(
                problem_id=problem_id,
                run_id=run_id,
                artifact_type="CRYPTO_LEADLAG_R2_RESULT",
                methodology_version=LEADLAG_METHODOLOGY_VERSION,
                content=result,
            )
            summary = {
                "state": "CANDIDATE_REJECTED_VALIDATION",
                "decision": result["decision"],
                "status": result["status"],
                "methodology_version": LEADLAG_METHODOLOGY_VERSION,
                "candidate_id": result["candidate_id"],
                "development_passed": True,
                "validation_passed": False,
                "holdout_opened": False,
                "artifact_id": (
                    (final_artifact.get("artifact") or {}).get("artifact_id")
                    if isinstance(final_artifact.get("artifact"), Mapping) else None
                ),
                "content_hash": final_artifact.get("content_hash"),
                "model_invoked": False,
                "execution_authority": False,
                "production_state_changed": False,
                "next_action": result["next_action"],
            }
            return await self._finalize(
                problem=problem,
                run=run,
                status="WAITING",
                summary=summary,
            )

        holdout_bars = await self._fetch_stage(
            LEADLAG_UNIVERSE,
            start=LEADLAG_HOLDOUT_START,
            end=LEADLAG_HOLDOUT_END,
        )
        holdout_high = evaluate_leadlag_stage(
            holdout_bars,
            start=LEADLAG_HOLDOUT_START,
            end=LEADLAG_HOLDOUT_END,
            scenario="high",
            seed=82301,
        )
        holdout_base = evaluate_leadlag_stage(
            holdout_bars,
            start=LEADLAG_HOLDOUT_START,
            end=LEADLAG_HOLDOUT_END,
            scenario="base",
            seed=82311,
        )
        holdout_low = evaluate_leadlag_stage(
            holdout_bars,
            start=LEADLAG_HOLDOUT_START,
            end=LEADLAG_HOLDOUT_END,
            scenario="low",
            seed=82321,
        )
        holdout_passed, holdout_reasons = leadlag_holdout_gate(holdout_high)
        holdout_artifact = await self.gateway.record_artifact(
            problem_id=problem_id,
            run_id=run_id,
            artifact_type="CRYPTO_LEADLAG_HOLDOUT_RESULT",
            methodology_version=LEADLAG_METHODOLOGY_VERSION,
            content={
                "stage": "HOLDOUT",
                "passed": holdout_passed,
                "reasons": holdout_reasons,
                "bar_counts": {symbol: len(rows) for symbol, rows in holdout_bars.items()},
                "scenarios": {
                    "high": holdout_high,
                    "base": holdout_base,
                    "low": holdout_low,
                },
                "source_commit": _source_commit(),
            },
        )
        result["holdout"] = {
            "opened": True,
            "passed": holdout_passed,
            "reasons": holdout_reasons,
            "artifact_id": (
                (holdout_artifact.get("artifact") or {}).get("artifact_id")
                if isinstance(holdout_artifact.get("artifact"), Mapping) else None
            ),
            "scenarios": {
                "high": holdout_high,
                "base": holdout_base,
                "low": holdout_low,
            },
        }
        result.update({
            "status": "HOLDOUT_PASS" if holdout_passed else "HOLDOUT_FAIL",
            "decision": "PROMOTE_TO_VELUM" if holdout_passed else "CONTINUE_RESEARCH",
            "next_action": "VELUM_REPLAY" if holdout_passed else "DESIGN_NEXT_FROZEN_RESEARCH_BATCH",
        })
        final_artifact = await self.gateway.record_artifact(
            problem_id=problem_id,
            run_id=run_id,
            artifact_type="CRYPTO_LEADLAG_R2_RESULT",
            methodology_version=LEADLAG_METHODOLOGY_VERSION,
            content=result,
        )
        summary = {
            "state": "CANDIDATE_READY_FOR_VELUM" if holdout_passed else "CANDIDATE_REJECTED_HOLDOUT",
            "decision": result["decision"],
            "status": result["status"],
            "methodology_version": LEADLAG_METHODOLOGY_VERSION,
            "candidate_id": result["candidate_id"],
            "development_passed": True,
            "validation_passed": True,
            "holdout_opened": True,
            "holdout_passed": holdout_passed,
            "artifact_id": (
                (final_artifact.get("artifact") or {}).get("artifact_id")
                if isinstance(final_artifact.get("artifact"), Mapping) else None
            ),
            "content_hash": final_artifact.get("content_hash"),
            "model_invoked": False,
            "execution_authority": False,
            "production_state_changed": False,
            "next_action": result["next_action"],
        }
        return await self._finalize(
            problem=problem,
            run=run,
            status="SUCCEEDED" if holdout_passed else "WAITING",
            summary=summary,
        )

    async def _execute_autonomous_campaign(
        self,
        problem: Mapping[str, Any],
        run: Mapping[str, Any],
    ) -> dict[str, Any]:
        problem_id = str(problem.get("problem_id"))
        run_id = str(run.get("run_id"))
        metadata = problem.get("metadata") if isinstance(problem.get("metadata"), Mapping) else {}
        stage = str(metadata.get("research_stage") or AUTONOMOUS_DEVELOPMENT_STAGE)
        epoch_index = int(metadata.get("campaign_epoch") or 0)
        generation = int(metadata.get("campaign_generation") or 1)
        contract = autonomous_epoch_contract(epoch_index)
        self.active_methodology_version = autonomous_methodology_version(
            epoch_index,
            generation,
            stage,
        )

        def artifact_id(response: Mapping[str, Any]) -> str | None:
            artifact = response.get("artifact")
            return (
                str(artifact.get("artifact_id"))
                if isinstance(artifact, Mapping) and artifact.get("artifact_id")
                else None
            )

        async def record_stage(
            artifact_type: str,
            content: dict[str, Any],
        ) -> str | None:
            response = await self.gateway.record_artifact(
                problem_id=problem_id,
                run_id=run_id,
                artifact_type=artifact_type,
                methodology_version=self.active_methodology_version,
                content={
                    **content,
                    "campaign_id": AUTONOMOUS_CAMPAIGN_ID,
                    "epoch": contract["epoch"],
                    "epoch_index": epoch_index,
                    "generation": generation,
                    "source_commit": _source_commit(),
                    "deployment_id": _deployment_id(),
                    "research_only": True,
                    "model_invoked": False,
                    "execution_authority": False,
                    "production_state_changed": False,
                },
            )
            return artifact_id(response)

        def next_epoch_transition() -> tuple[str | None, dict[str, Any] | None]:
            next_epoch = autonomous_next_epoch_index(epoch_index)
            if next_epoch is None:
                return None, None
            return (
                AUTONOMOUS_DEVELOPMENT_STAGE,
                autonomous_stage_metadata(
                    epoch_index=next_epoch,
                    generation=1,
                ),
            )

        if stage == AUTONOMOUS_DEVELOPMENT_STAGE:
            prespec = autonomous_prespecification(epoch_index, generation)
            prespec_artifact = await record_stage(
                "CRYPTO_AUTONOMOUS_PRESPEC",
                prespec,
            )
            bars = await self._fetch_stage(
                AUTONOMOUS_UNIVERSE,
                start=contract["development_start"],
                end=contract["validation_start"],
                warmup_hours=8,
            )
            development = evaluate_autonomous_development(
                bars,
                start=contract["development_start"],
                end=contract["validation_start"],
                generation=generation,
            )
            development_artifact = await record_stage(
                "CRYPTO_AUTONOMOUS_DEVELOPMENT_RESULT",
                {
                    **development,
                    "bar_counts": {symbol: len(rows) for symbol, rows in bars.items()},
                },
            )
            selected_spec = development.get("selected_candidate_spec")
            selected_result = development.get("selected_development_result")
            if isinstance(selected_spec, Mapping) and isinstance(selected_result, Mapping):
                next_metadata = autonomous_stage_metadata(
                    epoch_index=epoch_index,
                    generation=generation,
                    candidate_spec=selected_spec,
                    development_result=selected_result,
                )
                summary = {
                    "state": "AUTONOMOUS_CANDIDATE_FROZEN_FOR_VALIDATION",
                    "decision": "CONTINUE_RESEARCH",
                    "status": "DEVELOPMENT_PASS",
                    "campaign_id": AUTONOMOUS_CAMPAIGN_ID,
                    "epoch": contract["epoch"],
                    "epoch_index": epoch_index,
                    "generation": generation,
                    "candidate_id": selected_spec.get("candidate_id"),
                    "candidate_family": selected_spec.get("family"),
                    "prespec_artifact_id": prespec_artifact,
                    "development_artifact_id": development_artifact,
                    "validation_opened": False,
                    "holdout_opened": False,
                    "model_invoked": False,
                    "execution_authority": False,
                    "production_state_changed": False,
                    "next_action": "RUN_FRESH_VALIDATION",
                }
                return await self._finalize(
                    problem=problem,
                    run=run,
                    status="WAITING",
                    summary=summary,
                    next_stage=AUTONOMOUS_VALIDATION_STAGE,
                    next_metadata=next_metadata,
                )

            if generation < MAX_GENERATIONS_PER_EPOCH:
                next_stage = AUTONOMOUS_DEVELOPMENT_STAGE
                next_metadata = autonomous_stage_metadata(
                    epoch_index=epoch_index,
                    generation=generation + 1,
                )
                state = "AUTONOMOUS_GENERATION_REJECTED"
                next_action = "GENERATE_NEXT_DEVELOPMENT_BATCH"
            else:
                next_stage, next_metadata = next_epoch_transition()
                state = (
                    "AUTONOMOUS_EPOCH_REJECTED_DEVELOPMENT"
                    if next_stage
                    else "AUTONOMOUS_CAMPAIGN_EXHAUSTED"
                )
                next_action = (
                    "START_NEXT_UNTOUCHED_EPOCH"
                    if next_stage
                    else "MODEL_HYPOTHESIS_GENERATION_REQUIRED"
                )

            summary = {
                "state": state,
                "decision": "CONTINUE_RESEARCH" if next_stage else "NEEDS_NEW_HYPOTHESIS_ENGINE",
                "status": "NO_DEVELOPMENT_SURVIVOR",
                "campaign_id": AUTONOMOUS_CAMPAIGN_ID,
                "epoch": contract["epoch"],
                "epoch_index": epoch_index,
                "generation": generation,
                "prespec_artifact_id": prespec_artifact,
                "development_artifact_id": development_artifact,
                "validation_opened": False,
                "holdout_opened": False,
                "model_invoked": False,
                "execution_authority": False,
                "production_state_changed": False,
                "next_action": next_action,
            }
            return await self._finalize(
                problem=problem,
                run=run,
                status="WAITING",
                summary=summary,
                next_stage=next_stage,
                next_metadata=next_metadata,
            )

        candidate_spec = metadata.get("candidate_spec")
        development_result = metadata.get("development_result")
        if not isinstance(candidate_spec, Mapping) or not isinstance(development_result, Mapping):
            raise RuntimeError("autonomous_campaign_missing_frozen_candidate")

        if stage == AUTONOMOUS_VALIDATION_STAGE:
            bars = await self._fetch_stage(
                AUTONOMOUS_UNIVERSE,
                start=contract["validation_start"],
                end=contract["holdout_start"],
                warmup_hours=8,
            )
            validation = evaluate_autonomous_validation(
                bars,
                start=contract["validation_start"],
                end=contract["holdout_start"],
                candidate_spec=candidate_spec,
                development_result=development_result,
                seed=89000 + epoch_index * 100 + generation,
            )
            validation_artifact = await record_stage(
                "CRYPTO_AUTONOMOUS_VALIDATION_RESULT",
                {
                    **validation,
                    "bar_counts": {symbol: len(rows) for symbol, rows in bars.items()},
                },
            )
            if validation.get("passed") is True:
                summary = {
                    "state": "AUTONOMOUS_CANDIDATE_FROZEN_FOR_HOLDOUT",
                    "decision": "CONTINUE_RESEARCH",
                    "status": "VALIDATION_PASS",
                    "campaign_id": AUTONOMOUS_CAMPAIGN_ID,
                    "epoch": contract["epoch"],
                    "epoch_index": epoch_index,
                    "generation": generation,
                    "candidate_id": candidate_spec.get("candidate_id"),
                    "candidate_family": candidate_spec.get("family"),
                    "validation_artifact_id": validation_artifact,
                    "holdout_opened": False,
                    "model_invoked": False,
                    "execution_authority": False,
                    "production_state_changed": False,
                    "next_action": "OPEN_FRESH_HOLDOUT",
                }
                return await self._finalize(
                    problem=problem,
                    run=run,
                    status="WAITING",
                    summary=summary,
                    next_stage=AUTONOMOUS_HOLDOUT_STAGE,
                    next_metadata=autonomous_stage_metadata(
                        epoch_index=epoch_index,
                        generation=generation,
                        candidate_spec=candidate_spec,
                        development_result=development_result,
                    ),
                )

            next_stage, next_metadata = next_epoch_transition()
            summary = {
                "state": (
                    "AUTONOMOUS_CANDIDATE_REJECTED_VALIDATION"
                    if next_stage
                    else "AUTONOMOUS_CAMPAIGN_EXHAUSTED"
                ),
                "decision": "CONTINUE_RESEARCH" if next_stage else "NEEDS_NEW_HYPOTHESIS_ENGINE",
                "status": "VALIDATION_FAIL",
                "campaign_id": AUTONOMOUS_CAMPAIGN_ID,
                "epoch": contract["epoch"],
                "epoch_index": epoch_index,
                "generation": generation,
                "candidate_id": candidate_spec.get("candidate_id"),
                "candidate_family": candidate_spec.get("family"),
                "validation_artifact_id": validation_artifact,
                "holdout_opened": False,
                "model_invoked": False,
                "execution_authority": False,
                "production_state_changed": False,
                "next_action": (
                    "START_NEXT_UNTOUCHED_EPOCH"
                    if next_stage
                    else "MODEL_HYPOTHESIS_GENERATION_REQUIRED"
                ),
            }
            return await self._finalize(
                problem=problem,
                run=run,
                status="WAITING",
                summary=summary,
                next_stage=next_stage,
                next_metadata=next_metadata,
            )

        if stage == AUTONOMOUS_HOLDOUT_STAGE:
            bars = await self._fetch_stage(
                AUTONOMOUS_UNIVERSE,
                start=contract["holdout_start"],
                end=contract["holdout_end"],
                warmup_hours=8,
            )
            holdout = evaluate_autonomous_holdout(
                bars,
                start=contract["holdout_start"],
                end=contract["holdout_end"],
                candidate_spec=candidate_spec,
                seed=90000 + epoch_index * 100 + generation,
            )
            holdout_artifact = await record_stage(
                "CRYPTO_AUTONOMOUS_HOLDOUT_RESULT",
                {
                    **holdout,
                    "bar_counts": {symbol: len(rows) for symbol, rows in bars.items()},
                },
            )
            if holdout.get("passed") is True:
                next_metadata = autonomous_stage_metadata(
                    epoch_index=epoch_index,
                    generation=generation,
                    candidate_spec=candidate_spec,
                    development_result=development_result,
                )
                next_metadata["holdout_result"] = holdout
                next_metadata["holdout_artifact_id"] = holdout_artifact
                summary = {
                    "state": "CANDIDATE_READY_FOR_VELUM",
                    "decision": "CONTINUE_RESEARCH",
                    "status": "HOLDOUT_PASS",
                    "campaign_id": AUTONOMOUS_CAMPAIGN_ID,
                    "epoch": contract["epoch"],
                    "epoch_index": epoch_index,
                    "generation": generation,
                    "candidate_id": candidate_spec.get("candidate_id"),
                    "candidate_family": candidate_spec.get("family"),
                    "holdout_artifact_id": holdout_artifact,
                    "holdout_opened": True,
                    "holdout_passed": True,
                    "model_invoked": False,
                    "execution_authority": False,
                    "production_state_changed": False,
                    "next_action": "VELUM_CANDIDATE_REPLAY",
                }
                return await self._finalize(
                    problem=problem,
                    run=run,
                    status="WAITING",
                    summary=summary,
                    next_stage=AUTONOMOUS_VELUM_STAGE,
                    next_metadata=next_metadata,
                )

            next_stage, next_metadata = next_epoch_transition()
            summary = {
                "state": (
                    "AUTONOMOUS_CANDIDATE_REJECTED_HOLDOUT"
                    if next_stage
                    else "AUTONOMOUS_CAMPAIGN_EXHAUSTED"
                ),
                "decision": "CONTINUE_RESEARCH" if next_stage else "NEEDS_NEW_HYPOTHESIS_ENGINE",
                "status": "HOLDOUT_FAIL",
                "campaign_id": AUTONOMOUS_CAMPAIGN_ID,
                "epoch": contract["epoch"],
                "epoch_index": epoch_index,
                "generation": generation,
                "candidate_id": candidate_spec.get("candidate_id"),
                "candidate_family": candidate_spec.get("family"),
                "holdout_artifact_id": holdout_artifact,
                "holdout_opened": True,
                "holdout_passed": False,
                "model_invoked": False,
                "execution_authority": False,
                "production_state_changed": False,
                "next_action": (
                    "START_NEXT_UNTOUCHED_EPOCH"
                    if next_stage
                    else "MODEL_HYPOTHESIS_GENERATION_REQUIRED"
                ),
            }
            return await self._finalize(
                problem=problem,
                run=run,
                status="WAITING",
                summary=summary,
                next_stage=next_stage,
                next_metadata=next_metadata,
            )

        if stage == AUTONOMOUS_VELUM_STAGE:
            holdout_result = metadata.get("holdout_result")
            holdout_artifact_id = metadata.get("holdout_artifact_id")
            if not isinstance(candidate_spec, Mapping) or not isinstance(holdout_result, Mapping):
                raise RuntimeError("autonomous_velum_stage_missing_frozen_candidate")
            replay_start = contract["holdout_end"]
            replay_end = min(
                replay_start + timedelta(days=30),
                datetime.now(UTC) - timedelta(minutes=10),
            )
            if replay_end <= replay_start:
                summary = {
                    "state": "VELUM_REPLAY_WAITING_FOR_DATA",
                    "decision": "CONTINUE_RESEARCH",
                    "status": "WAITING_FOR_REPLAY_WINDOW",
                    "campaign_id": AUTONOMOUS_CAMPAIGN_ID,
                    "epoch": contract["epoch"],
                    "epoch_index": epoch_index,
                    "generation": generation,
                    "candidate_id": candidate_spec.get("candidate_id"),
                    "holdout_artifact_id": holdout_artifact_id,
                    "execution_authority": False,
                    "production_state_changed": False,
                    "next_action": "RETRY_VELUM_REPLAY",
                }
                return await self._finalize(
                    problem=problem,
                    run=run,
                    status="WAITING",
                    summary=summary,
                    next_stage=AUTONOMOUS_VELUM_STAGE,
                    next_metadata=dict(metadata),
                )

            velum_result = await self._replay_in_velum(
                problem_id=problem_id,
                graen_run_id=run_id,
                campaign_id=AUTONOMOUS_CAMPAIGN_ID,
                epoch_index=epoch_index,
                generation=generation,
                candidate_methodology=AUTONOMOUS_METHODOLOGY_PREFIX,
                candidate_spec=candidate_spec,
                replay_start=replay_start,
                replay_end=replay_end,
                seed=91000 + epoch_index * 100 + generation,
            )
            velum_artifact = await record_stage(
                "CRYPTO_AUTONOMOUS_VELUM_RESULT",
                {
                    "velum_result": velum_result,
                    "holdout_artifact_id": holdout_artifact_id,
                    "replay_start": replay_start.isoformat(),
                    "replay_end": replay_end.isoformat(),
                },
            )
            gate = velum_result.get("engineering_gate")
            passed = bool(
                isinstance(gate, Mapping)
                and gate.get("passed") is True
            )
            if passed:
                summary = {
                    "state": "CANDIDATE_READY_FOR_FORWARD_SHADOW",
                    "decision": "FORWARD_SHADOW_REQUIRED",
                    "status": "VELUM_PASS",
                    "campaign_id": AUTONOMOUS_CAMPAIGN_ID,
                    "epoch": contract["epoch"],
                    "epoch_index": epoch_index,
                    "generation": generation,
                    "candidate_id": candidate_spec.get("candidate_id"),
                    "candidate_family": candidate_spec.get("family"),
                    "holdout_artifact_id": holdout_artifact_id,
                    "velum_artifact_id": velum_artifact,
                    "velum_engineering_gate": dict(gate),
                    "execution_authority": False,
                    "broker_orders_possible": False,
                    "production_state_changed": False,
                    "next_action": "FORWARD_SHADOW_OBSERVATION",
                }
                return await self._finalize(
                    problem=problem,
                    run=run,
                    status="SUCCEEDED",
                    summary=summary,
                )

            next_stage, next_metadata = next_epoch_transition()
            summary = {
                "state": (
                    "AUTONOMOUS_CANDIDATE_REJECTED_VELUM"
                    if next_stage
                    else "AUTONOMOUS_CAMPAIGN_EXHAUSTED"
                ),
                "decision": "CONTINUE_RESEARCH" if next_stage else "NEEDS_NEW_HYPOTHESIS_ENGINE",
                "status": "VELUM_FAIL",
                "campaign_id": AUTONOMOUS_CAMPAIGN_ID,
                "epoch": contract["epoch"],
                "epoch_index": epoch_index,
                "generation": generation,
                "candidate_id": candidate_spec.get("candidate_id"),
                "candidate_family": candidate_spec.get("family"),
                "holdout_artifact_id": holdout_artifact_id,
                "velum_artifact_id": velum_artifact,
                "velum_engineering_gate": dict(gate) if isinstance(gate, Mapping) else {},
                "execution_authority": False,
                "broker_orders_possible": False,
                "production_state_changed": False,
                "next_action": (
                    "START_NEXT_UNTOUCHED_EPOCH"
                    if next_stage
                    else "MODEL_HYPOTHESIS_GENERATION_REQUIRED"
                ),
            }
            return await self._finalize(
                problem=problem,
                run=run,
                status="WAITING",
                summary=summary,
                next_stage=next_stage,
                next_metadata=next_metadata,
            )

        raise RuntimeError(f"unsupported_autonomous_campaign_stage:{stage}")

    async def _execute_activity_shock_v9(
        self,
        problem: Mapping[str, Any],
        run: Mapping[str, Any],
    ) -> dict[str, Any]:
        problem_id = str(problem.get("problem_id"))
        run_id = str(run.get("run_id"))
        metadata = problem.get("metadata") if isinstance(problem.get("metadata"), Mapping) else {}
        stage = str(metadata.get("research_stage") or V9_DEVELOPMENT_STAGE)
        epoch_index = int(metadata.get("v9_epoch_index") or 0)
        generation = 1
        contract = _v9_epoch_contract(epoch_index)
        self.active_methodology_version = V9_METHODOLOGY_VERSION

        def artifact_id(response: Mapping[str, Any]) -> str | None:
            artifact = response.get("artifact")
            return (
                str(artifact.get("artifact_id"))
                if isinstance(artifact, Mapping) and artifact.get("artifact_id")
                else None
            )

        async def record_stage(
            artifact_type: str,
            content: dict[str, Any],
        ) -> str | None:
            response = await self.gateway.record_artifact(
                problem_id=problem_id,
                run_id=run_id,
                artifact_type=artifact_type,
                methodology_version=V9_METHODOLOGY_VERSION,
                content={
                    **content,
                    "campaign_id": V9_CAMPAIGN_ID,
                    "epoch": contract["name"],
                    "epoch_index": epoch_index,
                    "source_commit": _source_commit(),
                    "deployment_id": _deployment_id(),
                    "research_only": True,
                    "model_invoked": False,
                    "execution_authority": False,
                    "broker_orders_possible": False,
                    "production_state_changed": False,
                },
            )
            return artifact_id(response)

        def next_epoch_transition() -> tuple[str | None, dict[str, Any] | None]:
            next_epoch = _v9_next_epoch(epoch_index)
            if next_epoch is None:
                return None, None
            return (
                V9_DEVELOPMENT_STAGE,
                {
                    "v9_campaign_id": V9_CAMPAIGN_ID,
                    "v9_epoch_index": next_epoch,
                    "v9_generation": 1,
                },
            )

        if stage == V9_DEVELOPMENT_STAGE:
            prespec = {
                "schema_version": "graen.crypto_activity_shock.prespec.v1",
                "campaign_id": V9_CAMPAIGN_ID,
                "methodology_version": V9_METHODOLOGY_VERSION,
                "family": V9_FAMILY,
                "epoch": contract["name"],
                "epoch_index": epoch_index,
                "candidate_registry": [row.to_dict() for row in v9_candidate_specs()],
                "candidate_count": len(v9_candidate_specs()),
                "selection_rule": (
                    "select one development survivor by expectancy_per_trade * sqrt(trade_count); "
                    "validation and holdout remain unopened until prior gate passes"
                ),
                "stage_order": ["DEVELOPMENT", "VALIDATION", "HOLDOUT", "VELUM_REPLAY"],
                "development": [
                    contract["development_start"].isoformat(),
                    contract["validation_start"].isoformat(),
                ],
                "validation": [
                    contract["validation_start"].isoformat(),
                    contract["holdout_start"].isoformat(),
                ],
                "holdout": [
                    contract["holdout_start"].isoformat(),
                    contract["holdout_end"].isoformat(),
                ],
                "development_availability_probe_disclosed": list(
                    contract["development_availability_probe_disclosed"]
                ),
                "validation_previously_inspected": False,
                "holdout_previously_inspected": False,
                "frozen_before_stage_corpus_access": True,
                "authority": {
                    "research_only": True,
                    "execution_authority": False,
                    "broker_orders_possible": False,
                    "risk_or_sizing_authority": False,
                    "production_promotion_authority": False,
                    "model_execution_enabled": False,
                },
            }
            prespec_artifact = await record_stage(
                "CRYPTO_ACTIVITY_SHOCK_V9_PRESPEC",
                prespec,
            )
            bars = await self._fetch_stage(
                V9_UNIVERSE,
                start=contract["development_start"],
                end=contract["validation_start"],
                warmup_hours=25,
            )
            development_corpus = verify_v9_stage_corpus(
                bars,
                start=contract["development_start"],
                end=contract["validation_start"],
            )
            development = evaluate_v9_development(
                bars,
                start=contract["development_start"],
                end=contract["validation_start"],
            )
            development_artifact = await record_stage(
                "CRYPTO_ACTIVITY_SHOCK_V9_DEVELOPMENT_RESULT",
                {
                    **development,
                    "bar_counts": {symbol: len(rows) for symbol, rows in bars.items()},
                    "corpus_gate": development_corpus,
                },
            )
            selected_spec = development.get("selected_candidate_spec")
            if isinstance(selected_spec, Mapping):
                summary = {
                    "state": "V9_CANDIDATE_FROZEN_FOR_VALIDATION",
                    "decision": "CONTINUE_RESEARCH",
                    "status": "DEVELOPMENT_PASS",
                    "campaign_id": V9_CAMPAIGN_ID,
                    "epoch": contract["name"],
                    "epoch_index": epoch_index,
                    "candidate_id": selected_spec.get("candidate_id"),
                    "candidate_family": V9_FAMILY,
                    "prespec_artifact_id": prespec_artifact,
                    "development_artifact_id": development_artifact,
                    "validation_opened": False,
                    "holdout_opened": False,
                    "model_invoked": False,
                    "execution_authority": False,
                    "next_action": "RUN_FRESH_VALIDATION",
                }
                return await self._finalize(
                    problem=problem,
                    run=run,
                    status="WAITING",
                    summary=summary,
                    next_stage=V9_VALIDATION_STAGE,
                    next_metadata={
                        "v9_campaign_id": V9_CAMPAIGN_ID,
                        "v9_epoch_index": epoch_index,
                        "v9_generation": 1,
                        "v9_candidate_spec": dict(selected_spec),
                        "v9_development_artifact_id": development_artifact,
                    },
                )

            next_stage, next_metadata = next_epoch_transition()
            summary = {
                "state": (
                    "V9_EPOCH_REJECTED_DEVELOPMENT"
                    if next_stage
                    else "V9_CAMPAIGN_EXHAUSTED"
                ),
                "decision": "CONTINUE_RESEARCH" if next_stage else "NEEDS_NEW_HYPOTHESIS_ENGINE",
                "status": "NO_DEVELOPMENT_SURVIVOR",
                "campaign_id": V9_CAMPAIGN_ID,
                "epoch": contract["name"],
                "epoch_index": epoch_index,
                "prespec_artifact_id": prespec_artifact,
                "development_artifact_id": development_artifact,
                "validation_opened": False,
                "holdout_opened": False,
                "model_invoked": False,
                "execution_authority": False,
                "next_action": (
                    "START_NEXT_UNTOUCHED_EPOCH"
                    if next_stage
                    else "MODEL_HYPOTHESIS_GENERATION_REQUIRED"
                ),
            }
            return await self._finalize(
                problem=problem,
                run=run,
                status="WAITING",
                summary=summary,
                next_stage=next_stage,
                next_metadata=next_metadata,
            )

        candidate_spec = metadata.get("v9_candidate_spec")
        if not isinstance(candidate_spec, Mapping):
            raise RuntimeError("v9_missing_frozen_candidate")

        if stage == V9_VALIDATION_STAGE:
            bars = await self._fetch_stage(
                V9_UNIVERSE,
                start=contract["validation_start"],
                end=contract["holdout_start"],
                warmup_hours=25,
            )
            validation_corpus = verify_v9_stage_corpus(
                bars,
                start=contract["validation_start"],
                end=contract["holdout_start"],
            )
            validation = evaluate_v9_validation(
                bars,
                candidate_spec=candidate_spec,
                start=contract["validation_start"],
                end=contract["holdout_start"],
                seed=93000 + epoch_index * 100,
            )
            validation_artifact = await record_stage(
                "CRYPTO_ACTIVITY_SHOCK_V9_VALIDATION_RESULT",
                {
                    **validation,
                    "bar_counts": {symbol: len(rows) for symbol, rows in bars.items()},
                    "corpus_gate": validation_corpus,
                },
            )
            if validation.get("passed") is True:
                summary = {
                    "state": "V9_CANDIDATE_FROZEN_FOR_HOLDOUT",
                    "decision": "CONTINUE_RESEARCH",
                    "status": "VALIDATION_PASS",
                    "campaign_id": V9_CAMPAIGN_ID,
                    "epoch": contract["name"],
                    "epoch_index": epoch_index,
                    "candidate_id": candidate_spec.get("candidate_id"),
                    "candidate_family": V9_FAMILY,
                    "validation_artifact_id": validation_artifact,
                    "holdout_opened": False,
                    "model_invoked": False,
                    "execution_authority": False,
                    "next_action": "OPEN_FRESH_HOLDOUT",
                }
                return await self._finalize(
                    problem=problem,
                    run=run,
                    status="WAITING",
                    summary=summary,
                    next_stage=V9_HOLDOUT_STAGE,
                    next_metadata={
                        "v9_campaign_id": V9_CAMPAIGN_ID,
                        "v9_epoch_index": epoch_index,
                        "v9_generation": 1,
                        "v9_candidate_spec": dict(candidate_spec),
                        "v9_validation_artifact_id": validation_artifact,
                    },
                )

            next_stage, next_metadata = next_epoch_transition()
            summary = {
                "state": (
                    "V9_CANDIDATE_REJECTED_VALIDATION"
                    if next_stage
                    else "V9_CAMPAIGN_EXHAUSTED"
                ),
                "decision": "CONTINUE_RESEARCH" if next_stage else "NEEDS_NEW_HYPOTHESIS_ENGINE",
                "status": "VALIDATION_FAIL",
                "campaign_id": V9_CAMPAIGN_ID,
                "epoch": contract["name"],
                "epoch_index": epoch_index,
                "candidate_id": candidate_spec.get("candidate_id"),
                "candidate_family": V9_FAMILY,
                "validation_artifact_id": validation_artifact,
                "holdout_opened": False,
                "model_invoked": False,
                "execution_authority": False,
                "next_action": (
                    "START_NEXT_UNTOUCHED_EPOCH"
                    if next_stage
                    else "MODEL_HYPOTHESIS_GENERATION_REQUIRED"
                ),
            }
            return await self._finalize(
                problem=problem,
                run=run,
                status="WAITING",
                summary=summary,
                next_stage=next_stage,
                next_metadata=next_metadata,
            )

        if stage == V9_HOLDOUT_STAGE:
            bars = await self._fetch_stage(
                V9_UNIVERSE,
                start=contract["holdout_start"],
                end=contract["holdout_end"],
                warmup_hours=25,
            )
            holdout_corpus = verify_v9_stage_corpus(
                bars,
                start=contract["holdout_start"],
                end=contract["holdout_end"],
            )
            holdout = evaluate_v9_holdout(
                bars,
                candidate_spec=candidate_spec,
                start=contract["holdout_start"],
                end=contract["holdout_end"],
                seed=94000 + epoch_index * 100,
            )
            holdout_artifact = await record_stage(
                "CRYPTO_ACTIVITY_SHOCK_V9_HOLDOUT_RESULT",
                {
                    **holdout,
                    "bar_counts": {symbol: len(rows) for symbol, rows in bars.items()},
                    "corpus_gate": holdout_corpus,
                },
            )
            if holdout.get("passed") is True:
                summary = {
                    "state": "V9_CANDIDATE_READY_FOR_VELUM",
                    "decision": "CONTINUE_RESEARCH",
                    "status": "HOLDOUT_PASS",
                    "campaign_id": V9_CAMPAIGN_ID,
                    "epoch": contract["name"],
                    "epoch_index": epoch_index,
                    "candidate_id": candidate_spec.get("candidate_id"),
                    "candidate_family": V9_FAMILY,
                    "holdout_artifact_id": holdout_artifact,
                    "holdout_opened": True,
                    "holdout_passed": True,
                    "model_invoked": False,
                    "execution_authority": False,
                    "next_action": "VELUM_CANDIDATE_REPLAY",
                }
                return await self._finalize(
                    problem=problem,
                    run=run,
                    status="WAITING",
                    summary=summary,
                    next_stage=V9_VELUM_STAGE,
                    next_metadata={
                        "v9_campaign_id": V9_CAMPAIGN_ID,
                        "v9_epoch_index": epoch_index,
                        "v9_generation": 1,
                        "v9_candidate_spec": dict(candidate_spec),
                        "v9_holdout_result": holdout,
                        "v9_holdout_artifact_id": holdout_artifact,
                    },
                )

            next_stage, next_metadata = next_epoch_transition()
            summary = {
                "state": (
                    "V9_CANDIDATE_REJECTED_HOLDOUT"
                    if next_stage
                    else "V9_CAMPAIGN_EXHAUSTED"
                ),
                "decision": "CONTINUE_RESEARCH" if next_stage else "NEEDS_NEW_HYPOTHESIS_ENGINE",
                "status": "HOLDOUT_FAIL",
                "campaign_id": V9_CAMPAIGN_ID,
                "epoch": contract["name"],
                "epoch_index": epoch_index,
                "candidate_id": candidate_spec.get("candidate_id"),
                "candidate_family": V9_FAMILY,
                "holdout_artifact_id": holdout_artifact,
                "holdout_opened": True,
                "holdout_passed": False,
                "model_invoked": False,
                "execution_authority": False,
                "next_action": (
                    "START_NEXT_UNTOUCHED_EPOCH"
                    if next_stage
                    else "MODEL_HYPOTHESIS_GENERATION_REQUIRED"
                ),
            }
            return await self._finalize(
                problem=problem,
                run=run,
                status="WAITING",
                summary=summary,
                next_stage=next_stage,
                next_metadata=next_metadata,
            )

        if stage == V9_VELUM_STAGE:
            holdout_artifact_id = metadata.get("v9_holdout_artifact_id")
            replay_start = contract["holdout_end"]
            replay_end = min(
                replay_start + timedelta(days=30),
                datetime.now(UTC) - timedelta(minutes=10),
            )
            if replay_end <= replay_start:
                return await self._finalize(
                    problem=problem,
                    run=run,
                    status="WAITING",
                    summary={
                        "state": "V9_VELUM_REPLAY_WAITING_FOR_DATA",
                        "decision": "CONTINUE_RESEARCH",
                        "status": "WAITING_FOR_REPLAY_WINDOW",
                        "campaign_id": V9_CAMPAIGN_ID,
                        "epoch": contract["name"],
                        "epoch_index": epoch_index,
                        "candidate_id": candidate_spec.get("candidate_id"),
                        "execution_authority": False,
                        "next_action": "RETRY_VELUM_REPLAY",
                    },
                    next_stage=V9_VELUM_STAGE,
                    next_metadata=dict(metadata),
                )

            velum_result = await self._replay_in_velum(
                problem_id=problem_id,
                graen_run_id=run_id,
                campaign_id=V9_CAMPAIGN_ID,
                epoch_index=epoch_index,
                generation=generation,
                candidate_methodology=V9_METHODOLOGY_VERSION,
                candidate_spec=candidate_spec,
                replay_start=replay_start,
                replay_end=replay_end,
                seed=95000 + epoch_index * 100,
            )
            velum_artifact = await record_stage(
                "CRYPTO_ACTIVITY_SHOCK_V9_VELUM_RESULT",
                {
                    "velum_result": velum_result,
                    "holdout_artifact_id": holdout_artifact_id,
                    "replay_start": replay_start.isoformat(),
                    "replay_end": replay_end.isoformat(),
                },
            )
            gate = velum_result.get("engineering_gate")
            passed = bool(isinstance(gate, Mapping) and gate.get("passed") is True)
            if passed:
                shadow = await self._activate_forward_shadow(
                    problem_id=problem_id,
                    graen_run_id=run_id,
                    campaign_id=V9_CAMPAIGN_ID,
                    epoch_index=epoch_index,
                    generation=generation,
                    candidate_methodology=V9_METHODOLOGY_VERSION,
                    candidate_spec=candidate_spec,
                    velum_artifact_id=velum_artifact,
                )
                return await self._finalize(
                    problem=problem,
                    run=run,
                    status="WAITING",
                    summary={
                        "state": "FORWARD_SHADOW_RUNNING",
                        "decision": "COLLECT_FORWARD_EVIDENCE",
                        "status": "SHADOW_ACTIVE",
                        "campaign_id": V9_CAMPAIGN_ID,
                        "epoch": contract["name"],
                        "epoch_index": epoch_index,
                        "candidate_id": candidate_spec.get("candidate_id"),
                        "candidate_family": V9_FAMILY,
                        "holdout_artifact_id": holdout_artifact_id,
                        "velum_artifact_id": velum_artifact,
                        "velum_engineering_gate": dict(gate),
                        "shadow_activation": shadow,
                        "execution_authority": False,
                        "broker_orders_possible": False,
                        "promotion_authorized": False,
                        "production_state_changed": False,
                        "next_action": "AWAIT_NATIVE_SHADOW_CHECKPOINT",
                    },
                )

            next_stage, next_metadata = next_epoch_transition()
            return await self._finalize(
                problem=problem,
                run=run,
                status="WAITING",
                summary={
                    "state": (
                        "V9_CANDIDATE_REJECTED_VELUM"
                        if next_stage
                        else "V9_CAMPAIGN_EXHAUSTED"
                    ),
                    "decision": "CONTINUE_RESEARCH" if next_stage else "NEEDS_NEW_HYPOTHESIS_ENGINE",
                    "status": "VELUM_FAIL",
                    "campaign_id": V9_CAMPAIGN_ID,
                    "epoch": contract["name"],
                    "epoch_index": epoch_index,
                    "candidate_id": candidate_spec.get("candidate_id"),
                    "candidate_family": V9_FAMILY,
                    "holdout_artifact_id": holdout_artifact_id,
                    "velum_artifact_id": velum_artifact,
                    "velum_engineering_gate": dict(gate) if isinstance(gate, Mapping) else {},
                    "execution_authority": False,
                    "broker_orders_possible": False,
                    "production_state_changed": False,
                    "next_action": (
                        "START_NEXT_UNTOUCHED_EPOCH"
                        if next_stage
                        else "MODEL_HYPOTHESIS_GENERATION_REQUIRED"
                    ),
                },
                next_stage=next_stage,
                next_metadata=next_metadata,
            )

        raise RuntimeError(f"unsupported_v9_stage:{stage}")

    async def _execute_trend_pullback_v10(
        self,
        problem: Mapping[str, Any],
        run: Mapping[str, Any],
    ) -> dict[str, Any]:
        problem_id = str(problem.get("problem_id"))
        run_id = str(run.get("run_id"))
        metadata = problem.get("metadata") if isinstance(problem.get("metadata"), Mapping) else {}
        stage = str(metadata.get("research_stage") or V10_DEVELOPMENT_STAGE)
        epoch_index = int(metadata.get("v10_epoch_index") or 0)
        generation = 1
        contract = _v10_epoch_contract(epoch_index)
        self.active_methodology_version = V10_METHODOLOGY_VERSION

        def artifact_id(response: Mapping[str, Any]) -> str | None:
            artifact = response.get("artifact")
            return (
                str(artifact.get("artifact_id"))
                if isinstance(artifact, Mapping) and artifact.get("artifact_id")
                else None
            )

        async def record_stage(
            artifact_type: str,
            content: dict[str, Any],
        ) -> str | None:
            response = await self.gateway.record_artifact(
                problem_id=problem_id,
                run_id=run_id,
                artifact_type=artifact_type,
                methodology_version=V10_METHODOLOGY_VERSION,
                content={
                    **content,
                    "campaign_id": V10_CAMPAIGN_ID,
                    "epoch": contract["name"],
                    "epoch_index": epoch_index,
                    "source_commit": _source_commit(),
                    "deployment_id": _deployment_id(),
                    "research_only": True,
                    "model_invoked": False,
                    "execution_authority": False,
                    "broker_orders_possible": False,
                    "production_state_changed": False,
                },
            )
            return artifact_id(response)

        def next_epoch_transition() -> tuple[str | None, dict[str, Any] | None]:
            next_epoch = _v10_next_epoch(epoch_index)
            if next_epoch is None:
                return None, None
            return (
                V10_DEVELOPMENT_STAGE,
                {
                    "v10_campaign_id": V10_CAMPAIGN_ID,
                    "v10_epoch_index": next_epoch,
                    "v10_generation": 1,
                },
            )

        if stage == V10_DEVELOPMENT_STAGE:
            prespec = {
                "schema_version": "graen.crypto_trend_pullback.prespec.v1",
                "campaign_id": V10_CAMPAIGN_ID,
                "methodology_version": V10_METHODOLOGY_VERSION,
                "family": V10_FAMILY,
                "epoch": contract["name"],
                "epoch_index": epoch_index,
                "candidate_registry": [row.to_dict() for row in v10_candidate_specs()],
                "candidate_count": len(v10_candidate_specs()),
                "selection_rule": (
                    "select one development survivor by expectancy_per_trade * sqrt(trade_count); "
                    "validation and holdout remain unopened until prior gate passes"
                ),
                "stage_order": ["DEVELOPMENT", "VALIDATION", "HOLDOUT", "VELUM_REPLAY"],
                "development": [
                    contract["development_start"].isoformat(),
                    contract["validation_start"].isoformat(),
                ],
                "validation": [
                    contract["validation_start"].isoformat(),
                    contract["holdout_start"].isoformat(),
                ],
                "holdout": [
                    contract["holdout_start"].isoformat(),
                    contract["holdout_end"].isoformat(),
                ],
                "development_availability_probe_disclosed": list(
                    contract["development_availability_probe_disclosed"]
                ),
                "development_previously_inspected": True,
                "development_prior_use": [
                    "graen-crypto-activity-shock-v9",
                ],
                "validation_previously_inspected": False,
                "holdout_previously_inspected": False,
                "frozen_before_stage_corpus_access": True,
                "authority": {
                    "research_only": True,
                    "execution_authority": False,
                    "broker_orders_possible": False,
                    "risk_or_sizing_authority": False,
                    "production_promotion_authority": False,
                    "model_execution_enabled": False,
                },
            }
            prespec_artifact = await record_stage(
                "CRYPTO_TREND_PULLBACK_V10_PRESPEC",
                prespec,
            )
            bars = await self._fetch_stage(
                V10_UNIVERSE,
                start=contract["development_start"],
                end=contract["validation_start"],
                warmup_hours=26,
            )
            development_corpus = verify_v10_stage_corpus(
                bars,
                start=contract["development_start"],
                end=contract["validation_start"],
            )
            development = evaluate_v10_development(
                bars,
                start=contract["development_start"],
                end=contract["validation_start"],
            )
            development_artifact = await record_stage(
                "CRYPTO_TREND_PULLBACK_V10_DEVELOPMENT_RESULT",
                {
                    **development,
                    "bar_counts": {symbol: len(rows) for symbol, rows in bars.items()},
                    "corpus_gate": development_corpus,
                },
            )
            selected_spec = development.get("selected_candidate_spec")
            if isinstance(selected_spec, Mapping):
                summary = {
                    "state": "V10_CANDIDATE_FROZEN_FOR_VALIDATION",
                    "decision": "CONTINUE_RESEARCH",
                    "status": "DEVELOPMENT_PASS",
                    "campaign_id": V10_CAMPAIGN_ID,
                    "epoch": contract["name"],
                    "epoch_index": epoch_index,
                    "candidate_id": selected_spec.get("candidate_id"),
                    "candidate_family": V10_FAMILY,
                    "prespec_artifact_id": prespec_artifact,
                    "development_artifact_id": development_artifact,
                    "validation_opened": False,
                    "holdout_opened": False,
                    "model_invoked": False,
                    "execution_authority": False,
                    "next_action": "RUN_FRESH_VALIDATION",
                }
                return await self._finalize(
                    problem=problem,
                    run=run,
                    status="WAITING",
                    summary=summary,
                    next_stage=V10_VALIDATION_STAGE,
                    next_metadata={
                        "v10_campaign_id": V10_CAMPAIGN_ID,
                        "v10_epoch_index": epoch_index,
                        "v10_generation": 1,
                        "v10_candidate_spec": dict(selected_spec),
                        "v10_development_artifact_id": development_artifact,
                    },
                )

            next_stage, next_metadata = next_epoch_transition()
            summary = {
                "state": (
                    "V10_EPOCH_REJECTED_DEVELOPMENT"
                    if next_stage
                    else "V10_CAMPAIGN_EXHAUSTED"
                ),
                "decision": "CONTINUE_RESEARCH" if next_stage else "NEEDS_NEW_HYPOTHESIS_ENGINE",
                "status": "NO_DEVELOPMENT_SURVIVOR",
                "campaign_id": V10_CAMPAIGN_ID,
                "epoch": contract["name"],
                "epoch_index": epoch_index,
                "prespec_artifact_id": prespec_artifact,
                "development_artifact_id": development_artifact,
                "validation_opened": False,
                "holdout_opened": False,
                "model_invoked": False,
                "execution_authority": False,
                "next_action": (
                    "START_NEXT_UNTOUCHED_EPOCH"
                    if next_stage
                    else "MODEL_HYPOTHESIS_GENERATION_REQUIRED"
                ),
            }
            return await self._finalize(
                problem=problem,
                run=run,
                status="WAITING",
                summary=summary,
                next_stage=next_stage,
                next_metadata=next_metadata,
            )

        candidate_spec = metadata.get("v10_candidate_spec")
        if not isinstance(candidate_spec, Mapping):
            raise RuntimeError("v10_missing_frozen_candidate")

        if stage == V10_VALIDATION_STAGE:
            bars = await self._fetch_stage(
                V10_UNIVERSE,
                start=contract["validation_start"],
                end=contract["holdout_start"],
                warmup_hours=26,
            )
            validation_corpus = verify_v10_stage_corpus(
                bars,
                start=contract["validation_start"],
                end=contract["holdout_start"],
            )
            validation = evaluate_v10_validation(
                bars,
                candidate_spec=candidate_spec,
                start=contract["validation_start"],
                end=contract["holdout_start"],
                seed=103000 + epoch_index * 100,
            )
            validation_artifact = await record_stage(
                "CRYPTO_TREND_PULLBACK_V10_VALIDATION_RESULT",
                {
                    **validation,
                    "bar_counts": {symbol: len(rows) for symbol, rows in bars.items()},
                    "corpus_gate": validation_corpus,
                },
            )
            if validation.get("passed") is True:
                summary = {
                    "state": "V10_CANDIDATE_FROZEN_FOR_HOLDOUT",
                    "decision": "CONTINUE_RESEARCH",
                    "status": "VALIDATION_PASS",
                    "campaign_id": V10_CAMPAIGN_ID,
                    "epoch": contract["name"],
                    "epoch_index": epoch_index,
                    "candidate_id": candidate_spec.get("candidate_id"),
                    "candidate_family": V10_FAMILY,
                    "validation_artifact_id": validation_artifact,
                    "holdout_opened": False,
                    "model_invoked": False,
                    "execution_authority": False,
                    "next_action": "OPEN_FRESH_HOLDOUT",
                }
                return await self._finalize(
                    problem=problem,
                    run=run,
                    status="WAITING",
                    summary=summary,
                    next_stage=V10_HOLDOUT_STAGE,
                    next_metadata={
                        "v10_campaign_id": V10_CAMPAIGN_ID,
                        "v10_epoch_index": epoch_index,
                        "v10_generation": 1,
                        "v10_candidate_spec": dict(candidate_spec),
                        "v10_validation_artifact_id": validation_artifact,
                    },
                )

            next_stage, next_metadata = next_epoch_transition()
            summary = {
                "state": (
                    "V10_CANDIDATE_REJECTED_VALIDATION"
                    if next_stage
                    else "V10_CAMPAIGN_EXHAUSTED"
                ),
                "decision": "CONTINUE_RESEARCH" if next_stage else "NEEDS_NEW_HYPOTHESIS_ENGINE",
                "status": "VALIDATION_FAIL",
                "campaign_id": V10_CAMPAIGN_ID,
                "epoch": contract["name"],
                "epoch_index": epoch_index,
                "candidate_id": candidate_spec.get("candidate_id"),
                "candidate_family": V10_FAMILY,
                "validation_artifact_id": validation_artifact,
                "holdout_opened": False,
                "model_invoked": False,
                "execution_authority": False,
                "next_action": (
                    "START_NEXT_UNTOUCHED_EPOCH"
                    if next_stage
                    else "MODEL_HYPOTHESIS_GENERATION_REQUIRED"
                ),
            }
            return await self._finalize(
                problem=problem,
                run=run,
                status="WAITING",
                summary=summary,
                next_stage=next_stage,
                next_metadata=next_metadata,
            )

        if stage == V10_HOLDOUT_STAGE:
            bars = await self._fetch_stage(
                V10_UNIVERSE,
                start=contract["holdout_start"],
                end=contract["holdout_end"],
                warmup_hours=26,
            )
            holdout_corpus = verify_v10_stage_corpus(
                bars,
                start=contract["holdout_start"],
                end=contract["holdout_end"],
            )
            holdout = evaluate_v10_holdout(
                bars,
                candidate_spec=candidate_spec,
                start=contract["holdout_start"],
                end=contract["holdout_end"],
                seed=104000 + epoch_index * 100,
            )
            holdout_artifact = await record_stage(
                "CRYPTO_TREND_PULLBACK_V10_HOLDOUT_RESULT",
                {
                    **holdout,
                    "bar_counts": {symbol: len(rows) for symbol, rows in bars.items()},
                    "corpus_gate": holdout_corpus,
                },
            )
            if holdout.get("passed") is True:
                summary = {
                    "state": "V10_CANDIDATE_READY_FOR_VELUM",
                    "decision": "CONTINUE_RESEARCH",
                    "status": "HOLDOUT_PASS",
                    "campaign_id": V10_CAMPAIGN_ID,
                    "epoch": contract["name"],
                    "epoch_index": epoch_index,
                    "candidate_id": candidate_spec.get("candidate_id"),
                    "candidate_family": V10_FAMILY,
                    "holdout_artifact_id": holdout_artifact,
                    "holdout_opened": True,
                    "holdout_passed": True,
                    "model_invoked": False,
                    "execution_authority": False,
                    "next_action": "VELUM_CANDIDATE_REPLAY",
                }
                return await self._finalize(
                    problem=problem,
                    run=run,
                    status="WAITING",
                    summary=summary,
                    next_stage=V10_VELUM_STAGE,
                    next_metadata={
                        "v10_campaign_id": V10_CAMPAIGN_ID,
                        "v10_epoch_index": epoch_index,
                        "v10_generation": 1,
                        "v10_candidate_spec": dict(candidate_spec),
                        "v10_holdout_result": holdout,
                        "v10_holdout_artifact_id": holdout_artifact,
                    },
                )

            next_stage, next_metadata = next_epoch_transition()
            summary = {
                "state": (
                    "V10_CANDIDATE_REJECTED_HOLDOUT"
                    if next_stage
                    else "V10_CAMPAIGN_EXHAUSTED"
                ),
                "decision": "CONTINUE_RESEARCH" if next_stage else "NEEDS_NEW_HYPOTHESIS_ENGINE",
                "status": "HOLDOUT_FAIL",
                "campaign_id": V10_CAMPAIGN_ID,
                "epoch": contract["name"],
                "epoch_index": epoch_index,
                "candidate_id": candidate_spec.get("candidate_id"),
                "candidate_family": V10_FAMILY,
                "holdout_artifact_id": holdout_artifact,
                "holdout_opened": True,
                "holdout_passed": False,
                "model_invoked": False,
                "execution_authority": False,
                "next_action": (
                    "START_NEXT_UNTOUCHED_EPOCH"
                    if next_stage
                    else "MODEL_HYPOTHESIS_GENERATION_REQUIRED"
                ),
            }
            return await self._finalize(
                problem=problem,
                run=run,
                status="WAITING",
                summary=summary,
                next_stage=next_stage,
                next_metadata=next_metadata,
            )

        if stage == V10_VELUM_STAGE:
            holdout_artifact_id = metadata.get("v10_holdout_artifact_id")
            replay_start = contract["holdout_end"]
            replay_end = min(
                replay_start + timedelta(days=30),
                datetime.now(UTC) - timedelta(minutes=10),
            )
            if replay_end <= replay_start:
                return await self._finalize(
                    problem=problem,
                    run=run,
                    status="WAITING",
                    summary={
                        "state": "V10_VELUM_REPLAY_WAITING_FOR_DATA",
                        "decision": "CONTINUE_RESEARCH",
                        "status": "WAITING_FOR_REPLAY_WINDOW",
                        "campaign_id": V10_CAMPAIGN_ID,
                        "epoch": contract["name"],
                        "epoch_index": epoch_index,
                        "candidate_id": candidate_spec.get("candidate_id"),
                        "execution_authority": False,
                        "next_action": "RETRY_VELUM_REPLAY",
                    },
                    next_stage=V10_VELUM_STAGE,
                    next_metadata=dict(metadata),
                )

            velum_result = await self._replay_in_velum(
                problem_id=problem_id,
                graen_run_id=run_id,
                campaign_id=V10_CAMPAIGN_ID,
                epoch_index=epoch_index,
                generation=generation,
                candidate_methodology=V10_METHODOLOGY_VERSION,
                candidate_spec=candidate_spec,
                replay_start=replay_start,
                replay_end=replay_end,
                seed=105000 + epoch_index * 100,
            )
            velum_artifact = await record_stage(
                "CRYPTO_TREND_PULLBACK_V10_VELUM_RESULT",
                {
                    "velum_result": velum_result,
                    "holdout_artifact_id": holdout_artifact_id,
                    "replay_start": replay_start.isoformat(),
                    "replay_end": replay_end.isoformat(),
                },
            )
            gate = velum_result.get("engineering_gate")
            passed = bool(isinstance(gate, Mapping) and gate.get("passed") is True)
            if passed:
                shadow = await self._activate_forward_shadow(
                    problem_id=problem_id,
                    graen_run_id=run_id,
                    campaign_id=V10_CAMPAIGN_ID,
                    epoch_index=epoch_index,
                    generation=generation,
                    candidate_methodology=V10_METHODOLOGY_VERSION,
                    candidate_spec=candidate_spec,
                    velum_artifact_id=velum_artifact,
                )
                return await self._finalize(
                    problem=problem,
                    run=run,
                    status="WAITING",
                    summary={
                        "state": "FORWARD_SHADOW_RUNNING",
                        "decision": "COLLECT_FORWARD_EVIDENCE",
                        "status": "SHADOW_ACTIVE",
                        "campaign_id": V10_CAMPAIGN_ID,
                        "epoch": contract["name"],
                        "epoch_index": epoch_index,
                        "candidate_id": candidate_spec.get("candidate_id"),
                        "candidate_family": V10_FAMILY,
                        "holdout_artifact_id": holdout_artifact_id,
                        "velum_artifact_id": velum_artifact,
                        "velum_engineering_gate": dict(gate),
                        "shadow_activation": shadow,
                        "execution_authority": False,
                        "broker_orders_possible": False,
                        "promotion_authorized": False,
                        "production_state_changed": False,
                        "next_action": "AWAIT_NATIVE_SHADOW_CHECKPOINT",
                    },
                )

            next_stage, next_metadata = next_epoch_transition()
            return await self._finalize(
                problem=problem,
                run=run,
                status="WAITING",
                summary={
                    "state": (
                        "V10_CANDIDATE_REJECTED_VELUM"
                        if next_stage
                        else "V10_CAMPAIGN_EXHAUSTED"
                    ),
                    "decision": "CONTINUE_RESEARCH" if next_stage else "NEEDS_NEW_HYPOTHESIS_ENGINE",
                    "status": "VELUM_FAIL",
                    "campaign_id": V10_CAMPAIGN_ID,
                    "epoch": contract["name"],
                    "epoch_index": epoch_index,
                    "candidate_id": candidate_spec.get("candidate_id"),
                    "candidate_family": V10_FAMILY,
                    "holdout_artifact_id": holdout_artifact_id,
                    "velum_artifact_id": velum_artifact,
                    "velum_engineering_gate": dict(gate) if isinstance(gate, Mapping) else {},
                    "execution_authority": False,
                    "broker_orders_possible": False,
                    "production_state_changed": False,
                    "next_action": (
                        "START_NEXT_UNTOUCHED_EPOCH"
                        if next_stage
                        else "MODEL_HYPOTHESIS_GENERATION_REQUIRED"
                    ),
                },
                next_stage=next_stage,
                next_metadata=next_metadata,
            )

        raise RuntimeError(f"unsupported_v10_stage:{stage}")

    async def _execute_btc_trend_pullback_v11(
        self,
        problem: Mapping[str, Any],
        run: Mapping[str, Any],
    ) -> dict[str, Any]:
        problem_id = str(problem.get("problem_id"))
        run_id = str(run.get("run_id"))
        metadata = problem.get("metadata") if isinstance(problem.get("metadata"), Mapping) else {}
        stage = str(metadata.get("research_stage") or V11_DEVELOPMENT_STAGE)
        self.active_methodology_version = V11_METHODOLOGY_VERSION

        def artifact_id(response: Mapping[str, Any]) -> str | None:
            artifact = response.get("artifact")
            return (
                str(artifact.get("artifact_id"))
                if isinstance(artifact, Mapping) and artifact.get("artifact_id")
                else None
            )

        async def record_stage(artifact_type: str, content: dict[str, Any]) -> str | None:
            response = await self.gateway.record_artifact(
                problem_id=problem_id,
                run_id=run_id,
                artifact_type=artifact_type,
                methodology_version=V11_METHODOLOGY_VERSION,
                content={
                    **content,
                    "campaign_id": V11_CAMPAIGN_ID,
                    "source_commit": _source_commit(),
                    "deployment_id": _deployment_id(),
                    "research_only": True,
                    "execution_authority": False,
                    "broker_orders_possible": False,
                    "production_promotion_authority": False,
                    "production_state_changed": False,
                },
            )
            return artifact_id(response)

        if stage == V11_DEVELOPMENT_STAGE:
            prespec = {
                "schema_version": "graen.btc_trend_pullback_forward_v11.prespec.v1",
                "campaign_id": V11_CAMPAIGN_ID,
                "methodology_version": V11_METHODOLOGY_VERSION,
                "family": V11_FAMILY,
                "universe": list(V11_UNIVERSE),
                "candidate_registry": [row.to_dict() for row in v11_candidate_specs()],
                "candidate_count": len(v11_candidate_specs()),
                "historical_evidence_role": "DEVELOPMENT_ONLY",
                "historical_development": [
                    V11_DEVELOPMENT_START.isoformat(),
                    V11_DEVELOPMENT_END.isoformat(),
                ],
                "independent_historical_validation_available": False,
                "independent_historical_holdout_available": False,
                "fresh_confirmation_stage": "NATIVE_FORWARD_SHADOW",
                "forward_shadow_ready_gate": {
                    "min_trades": 30,
                    "min_independent_days": 20,
                    "expectancy_positive": True,
                    "profit_factor_min": 1.0,
                    "dependence_p_max": 0.05,
                },
                "selection_rule": (
                    "freeze one BTC-only candidate only if aggregate stressed-cost development "
                    "is positive and at least 5 of 7 fixed temporal folds independently pass "
                    "trade-count, day-count, profit-factor, expectancy, and delayed-entry gates"
                ),
                "authority": {
                    "research_only": True,
                    "execution_authority": False,
                    "broker_orders_possible": False,
                    "risk_or_sizing_authority": False,
                    "production_promotion_authority": False,
                },
            }
            prespec_artifact_id = await record_stage(
                "CRYPTO_BTC_TREND_PULLBACK_V11_PRESPEC",
                prespec,
            )
            bars = await self._fetch_stage(
                V11_UNIVERSE,
                start=V11_DEVELOPMENT_START,
                end=V11_DEVELOPMENT_END,
                warmup_hours=26,
            )
            corpus = verify_v11_development_corpus(
                bars,
                start=V11_DEVELOPMENT_START,
                end=V11_DEVELOPMENT_END,
            )
            development = await asyncio.to_thread(
                evaluate_v11_development,
                bars,
            )
            development_artifact_id = await record_stage(
                "CRYPTO_BTC_TREND_PULLBACK_V11_DEVELOPMENT_RESULT",
                {
                    "prespec_artifact_id": prespec_artifact_id,
                    "corpus": corpus,
                    "development": development,
                    "bar_counts": {
                        symbol: len(rows) for symbol, rows in bars.items()
                    },
                },
            )
            selected_spec = development.get("selected_candidate_spec")
            selected_id = development.get("selected_candidate_id")
            if not isinstance(selected_spec, Mapping) or not selected_id:
                return await self._finalize(
                    problem=problem,
                    run=run,
                    status="WAITING",
                    summary={
                        "state": "V11_NO_DEVELOPMENT_SURVIVOR",
                        "status": "NO_DEVELOPMENT_SURVIVOR",
                        "decision": "NEEDS_NEW_HYPOTHESIS_ENGINE",
                        "campaign_id": V11_CAMPAIGN_ID,
                        "candidate_family": V11_FAMILY,
                        "development_artifact_id": development_artifact_id,
                        "historical_evidence_role": "DEVELOPMENT_ONLY",
                        "fresh_confirmation_opened": False,
                        "next_action": "DESIGN_NEW_BTC_HYPOTHESIS",
                        "execution_authority": False,
                        "broker_orders_possible": False,
                    },
                )

            return await self._finalize(
                problem=problem,
                run=run,
                status="WAITING",
                summary={
                    "state": "V11_CANDIDATE_FROZEN_FOR_ENGINEERING_REPLAY",
                    "status": "DEVELOPMENT_PASS",
                    "decision": "CONTINUE_RESEARCH",
                    "campaign_id": V11_CAMPAIGN_ID,
                    "candidate_id": str(selected_id),
                    "candidate_family": V11_FAMILY,
                    "candidate_spec": dict(selected_spec),
                    "development_artifact_id": development_artifact_id,
                    "historical_evidence_role": "DEVELOPMENT_ONLY",
                    "fresh_confirmation_opened": False,
                    "next_action": "RUN_VELUM_ENGINEERING_REPLAY",
                    "execution_authority": False,
                    "broker_orders_possible": False,
                },
                next_stage=V11_VELUM_STAGE,
                next_metadata={
                    "v11_campaign_id": V11_CAMPAIGN_ID,
                    "v11_generation": 1,
                    "v11_candidate_spec": dict(selected_spec),
                    "v11_development_artifact_id": development_artifact_id,
                },
            )

        if stage == V11_VELUM_STAGE:
            candidate_spec = metadata.get("v11_candidate_spec")
            if not isinstance(candidate_spec, Mapping):
                raise RuntimeError("v11_velum_candidate_spec_missing")
            replay_start = V11_DEVELOPMENT_END - timedelta(days=30)
            replay = await self._replay_in_velum(
                problem_id=problem_id,
                graen_run_id=run_id,
                campaign_id=V11_CAMPAIGN_ID,
                epoch_index=0,
                generation=1,
                candidate_methodology=V11_METHODOLOGY_VERSION,
                candidate_spec=candidate_spec,
                replay_start=replay_start,
                replay_end=V11_DEVELOPMENT_END,
                seed=119000,
            )
            velum_artifact_id = await record_stage(
                "CRYPTO_BTC_TREND_PULLBACK_V11_VELUM_RESULT",
                {
                    "candidate_spec": dict(candidate_spec),
                    "replay": replay,
                    "replay_evidence_role": "POST_DEVELOPMENT_ENGINEERING_ONLY",
                    "independent_confirmatory_evidence": False,
                },
            )
            gate = replay.get("engineering_gate") if isinstance(replay.get("engineering_gate"), Mapping) else {}
            if not bool(gate.get("passed")):
                return await self._finalize(
                    problem=problem,
                    run=run,
                    status="WAITING",
                    summary={
                        "state": "V11_ENGINEERING_REPLAY_REJECTED",
                        "status": "ENGINEERING_REPLAY_FAIL",
                        "decision": "NEEDS_NEW_HYPOTHESIS_ENGINE",
                        "campaign_id": V11_CAMPAIGN_ID,
                        "candidate_id": candidate_spec.get("candidate_id"),
                        "candidate_family": V11_FAMILY,
                        "velum_artifact_id": velum_artifact_id,
                        "next_action": "DESIGN_NEW_BTC_HYPOTHESIS",
                        "execution_authority": False,
                        "broker_orders_possible": False,
                    },
                )

            activation = await self._activate_forward_shadow(
                problem_id=problem_id,
                graen_run_id=run_id,
                campaign_id=V11_CAMPAIGN_ID,
                epoch_index=0,
                generation=1,
                candidate_methodology=V11_METHODOLOGY_VERSION,
                candidate_spec=candidate_spec,
                velum_artifact_id=velum_artifact_id,
            )
            activation_artifact_id = await record_stage(
                "CRYPTO_BTC_TREND_PULLBACK_V11_FORWARD_SHADOW_ACTIVATION",
                {
                    "candidate_spec": dict(candidate_spec),
                    "velum_artifact_id": velum_artifact_id,
                    "activation": activation,
                    "fresh_confirmation": True,
                    "promotion_authorized": False,
                },
            )
            return await self._finalize(
                problem=problem,
                run=run,
                status="SUCCEEDED",
                summary={
                    "state": "V11_FORWARD_SHADOW_ACTIVATED",
                    "status": "FORWARD_SHADOW_ACTIVE",
                    "decision": "COLLECT_FRESH_FORWARD_EVIDENCE",
                    "campaign_id": V11_CAMPAIGN_ID,
                    "candidate_id": candidate_spec.get("candidate_id"),
                    "candidate_family": V11_FAMILY,
                    "velum_artifact_id": velum_artifact_id,
                    "activation_artifact_id": activation_artifact_id,
                    "next_action": "WAIT_FOR_FORWARD_SHADOW_GATE",
                    "promotion_authorized": False,
                    "execution_authority": False,
                    "broker_orders_possible": False,
                },
            )

        raise RuntimeError(f"unsupported_v11_research_stage:{stage}")

    async def _activate_forward_shadow(
        self,
        *,
        problem_id: str,
        graen_run_id: str,
        campaign_id: str,
        epoch_index: int,
        generation: int,
        candidate_methodology: str,
        candidate_spec: Mapping[str, Any],
        velum_artifact_id: str | None,
    ) -> dict[str, Any]:
        if not self.shadow_configured:
            raise RuntimeError("forward shadow service is not configured")
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                f"{self.shadow_base_url}/v1/candidate-shadow/activate",
                headers={"x-graen-shadow-token": self.shadow_token},
                json={
                    "problem_id": problem_id,
                    "graen_run_id": graen_run_id,
                    "campaign_id": campaign_id,
                    "epoch_index": epoch_index,
                    "generation": generation,
                    "candidate_methodology": candidate_methodology,
                    "candidate_spec": dict(candidate_spec),
                    "velum_artifact_id": str(velum_artifact_id or ""),
                },
            )
            response.raise_for_status()
            payload = response.json()
        activation = payload.get("activation")
        if not isinstance(activation, Mapping):
            raise RuntimeError("forward shadow activation returned no activation")
        return {
            "activation": dict(activation),
            "duplicate": bool(payload.get("duplicate")),
            "execution_authority": False,
            "broker_orders_possible": False,
            "promotion_authorized": False,
        }

    async def _replay_in_velum(
        self,
        *,
        problem_id: str,
        graen_run_id: str,
        campaign_id: str,
        epoch_index: int,
        generation: int,
        candidate_methodology: str,
        candidate_spec: Mapping[str, Any],
        replay_start: datetime,
        replay_end: datetime,
        seed: int,
    ) -> dict[str, Any]:
        if not self.velum_configured:
            raise RuntimeError("VELUM candidate replay is not configured")
        async with httpx.AsyncClient(timeout=180.0) as client:
            response = await client.post(
                f"{self.velum_base_url}/v1/graen/candidate-replay",
                headers={"x-graen-velum-token": self.velum_token},
                json={
                    "problem_id": problem_id,
                    "graen_run_id": graen_run_id,
                    "campaign_id": campaign_id,
                    "epoch_index": epoch_index,
                    "generation": generation,
                    "candidate_methodology": candidate_methodology,
                    "candidate_spec": dict(candidate_spec),
                    "replay_start": replay_start.isoformat(),
                    "replay_end": replay_end.isoformat(),
                    "seed": seed,
                },
            )
            response.raise_for_status()
            payload = response.json()
        result = payload.get("result")
        if not isinstance(result, Mapping):
            raise RuntimeError("VELUM candidate replay returned no result")
        return dict(result)

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


    async def _execute_compiled_hypothesis(self, problem, run):
        from importlib import import_module
        from graen.engineering import IntegrityError, digest, stage_window, validate_spec
        metadata = problem.get("metadata") or {}
        promotion = metadata.get("code_promotion") or {}
        spec = promotion.get("prespec") or {}
        spec_hash = validate_spec(spec)
        if (
            promotion.get("phase") != "COMPLETE"
            or promotion.get("spec_hash") != spec_hash
            or metadata.get("compiled_specification_hash") != spec_hash
        ):
            raise IntegrityError("verified_engineering_handoff_required")
        module_name = spec["hypothesis_id"].lower().replace("-", "_")
        implementation = import_module("graen.crypto.generated." + module_name)
        if implementation.SPEC_HASH != spec_hash or digest(implementation.SPEC) != spec_hash:
            raise IntegrityError("deployed_candidate_does_not_match_frozen_prespec")
        stage = str(metadata.get("research_stage", "")).removeprefix("CRYPTO_COMPILED_").lower()
        if stage not in {"development", "validation", "holdout"}:
            raise IntegrityError("compiled_stage_not_supported")
        prior = await self.gateway._request("POST", {
            "action": "compiled_stage_evidence",
            "problem_id": str(problem["problem_id"]), "spec_hash": spec_hash,
            "stage": stage, "epoch": spec["epoch"],
        })
        if prior.get("current"):
            # A completed stage is never re-evaluated after a failed continuation.
            result = prior["current"]
        else:
            predecessor = prior.get("predecessor")
            start, end = stage_window(spec, stage, predecessor)
            await self.gateway.record_artifact(
                problem_id=str(problem["problem_id"]), run_id=str(run["run_id"]),
                artifact_type="COMPILED_STAGE_OPENED", methodology_version="graen-crypto-flow-pressure-v1",
                content={"spec_hash": spec_hash, "stage": stage, "epoch": spec["epoch"],
                         "fetch_start": start.isoformat(), "fetch_end_exclusive": end.isoformat()},
            )
            # No future padding. Warmup is restricted to this stage: early
            # signals abstain until the entire lookback has accumulated.
            bars = {symbol: [] for symbol in spec["universe"]}
            current = start
            while current < end:
                limit = min(current + timedelta(days=20), end)
                chunk = await self.market_data.historical_crypto_bars_many(
                    spec["universe"], start=current, end=limit - timedelta(microseconds=1),
                )
                for symbol in spec["universe"]:
                    for row in chunk.get(symbol, []):
                        stamp = datetime.fromisoformat(str(row["t"]).replace("Z", "+00:00"))
                        if current <= stamp < limit:
                            bars[symbol].append(row)
                current = limit
            result = implementation.evaluate(bars, stage=stage, predecessor=predecessor)
            await self.gateway.record_artifact(
                problem_id=str(problem["problem_id"]), run_id=str(run["run_id"]),
                artifact_type="COMPILED_STAGE_RESULT", methodology_version="graen-crypto-flow-pressure-v1",
                content=result,
            )
        # Terminal stages must leave the claimable research stage. Otherwise
        # WAITING would repeatedly claim the same exhausted candidate.
        next_stage = (
            "CANDIDATE_READY_FOR_VELUM" if result.get("passed") is True and stage == "holdout"
            else "RESEARCH_IMPLEMENTATION_REQUIRED"
        )
        if result.get("passed") is True and stage != "holdout":
            next_stage = "CRYPTO_COMPILED_" + {"development": "VALIDATION", "validation": "HOLDOUT"}[stage]
        summary = {
            "state": "CANDIDATE_READY_FOR_VELUM" if result.get("passed") is True and stage == "holdout"
                else "COMPILED_STAGE_PASSED" if result.get("passed") is True else "COMPILED_CANDIDATE_REJECTED",
            "decision": "CONTINUE_RESEARCH" if result.get("passed") is True else "NEEDS_NEW_HYPOTHESIS_ENGINE",
            "next_action": "VELUM_CANDIDATE_REPLAY" if stage == "holdout" and result.get("passed") is True
                else "RUN_NEXT_FROZEN_STAGE" if result.get("passed") is True else "MODEL_HYPOTHESIS_GENERATION_REQUIRED",
            "candidate_id": spec["hypothesis_id"], "epoch": spec["epoch"], "spec_hash": spec_hash,
            "research_only": True, "execution_authority": False, "broker_orders_possible": False,
        }
        return await self._finalize(problem=problem, run=run, status="WAITING", summary=summary,
            next_stage=next_stage, next_metadata={"compiled_specification_hash": spec_hash} if next_stage else None)

    async def _reconcile_orphaned_confirmatory_claim(
        self,
        snapshot: Mapping[str, Any],
    ) -> dict[str, Any] | None:
        """Burn stale sealed-stage claims instead of reusing opened evidence."""
        problems = {
            str(row.get("problem_id")): row
            for row in (snapshot.get("problems") or [])
            if isinstance(row, Mapping) and row.get("problem_id")
        }
        runtime_state = snapshot.get("runtime_state")
        runtime_metadata = (
            runtime_state.get("metadata")
            if isinstance(runtime_state, Mapping)
            and isinstance(runtime_state.get("metadata"), Mapping)
            else {}
        )
        executor_state = (
            runtime_metadata.get("research_executor")
            if isinstance(runtime_metadata.get("research_executor"), Mapping)
            else {}
        )
        active_problem_id = str(executor_state.get("active_problem_id") or "")
        minimum_age = timedelta(seconds=max(300, self.interval_seconds * 4))
        now = datetime.now(UTC)

        for run in (snapshot.get("runs") or []):
            if not isinstance(run, Mapping) or run.get("status") != "RUNNING":
                continue
            problem_id = str(run.get("problem_id") or "")
            run_id = str(run.get("run_id") or "")
            problem = problems.get(problem_id)
            if not problem or problem.get("status") != "RUNNING" or active_problem_id == problem_id:
                continue
            metadata = problem.get("metadata") if isinstance(problem.get("metadata"), Mapping) else {}
            stage = str(metadata.get("research_stage") or "")
            if "VALIDATION" not in stage and "HOLDOUT" not in stage:
                continue
            try:
                started_at = datetime.fromisoformat(str(run.get("started_at")).replace("Z", "+00:00"))
                if started_at.tzinfo is None:
                    started_at = started_at.replace(tzinfo=UTC)
                started_at = started_at.astimezone(UTC)
            except (TypeError, ValueError):
                continue
            if now - started_at < minimum_age:
                continue

            error = "sealed_confirmatory_stage_orphaned:" + stage
            await self.gateway.block_research_claim(
                problem_id=problem_id,
                run_id=run_id,
                worker_id=self.worker_id,
                error=error,
            )
            await self.gateway.queue_research_stage(
                problem_id=problem_id,
                stage="RESEARCH_IMPLEMENTATION_REQUIRED",
                metadata={
                    "invalidated_run_id": run_id,
                    "invalidated_research_stage": stage,
                    "research_integrity_incident": "SEALED_STAGE_EXECUTION_ORPHANED",
                    "sealed_stage_invalidated": True,
                    "next_action": "MODEL_HYPOTHESIS_GENERATION_REQUIRED",
                },
            )
            return {
                "problem_id": problem_id,
                "run_id": run_id,
                "invalidated_stage": stage,
                "next_stage": "RESEARCH_IMPLEMENTATION_REQUIRED",
            }
        return None

    async def _recover_exhausted_v9_into_v10(
        self,
        snapshot: Mapping[str, Any],
    ) -> dict[str, Any] | None:
        """Repair the one-time v9 -> v10 handoff without opening execution authority."""
        problems = snapshot.get("problems") or []
        for problem in problems:
            if not isinstance(problem, Mapping):
                continue
            metadata = problem.get("metadata") if isinstance(problem.get("metadata"), Mapping) else {}
            if metadata.get("v10_campaign_id") == V10_CAMPAIGN_ID:
                return None
            if metadata.get("research_stage") in V10_STAGE_KEYS:
                return None

        latest_by_problem: dict[str, Mapping[str, Any]] = {}
        for run in snapshot.get("runs") or []:
            if not isinstance(run, Mapping):
                continue
            problem_id = str(run.get("problem_id") or "")
            if problem_id and problem_id not in latest_by_problem:
                latest_by_problem[problem_id] = run

        for problem in problems:
            if not isinstance(problem, Mapping):
                continue
            if problem.get("status") != "WAITING" or problem.get("domain") != PROBLEM_DOMAIN:
                continue
            metadata = problem.get("metadata") if isinstance(problem.get("metadata"), Mapping) else {}
            if metadata.get("research_stage"):
                continue
            problem_id = str(problem.get("problem_id") or "")
            run = latest_by_problem.get(problem_id)
            if not isinstance(run, Mapping):
                continue
            summary = run.get("result_summary") if isinstance(run.get("result_summary"), Mapping) else {}
            if not (
                summary.get("campaign_id") == V9_CAMPAIGN_ID
                and summary.get("state") == "V9_CAMPAIGN_EXHAUSTED"
                and summary.get("decision") == "NEEDS_NEW_HYPOTHESIS_ENGINE"
                and summary.get("next_action") == "MODEL_HYPOTHESIS_GENERATION_REQUIRED"
            ):
                continue
            queued = await self.gateway.queue_research_stage(
                problem_id=problem_id,
                stage=V10_DEVELOPMENT_STAGE,
                metadata={
                    "v10_campaign_id": V10_CAMPAIGN_ID,
                    "v10_epoch_index": 0,
                    "v10_generation": 1,
                    "v10_transition_source": "v9_campaign_exhausted",
                    "v9_terminal_run_id": str(run.get("run_id") or "") or None,
                },
            )
            if queued.get("problem"):
                return {
                    "recovered": True,
                    "problem_id": problem_id,
                    "next_research_stage": V10_DEVELOPMENT_STAGE,
                    "source": "V9_CAMPAIGN_EXHAUSTED",
                }
        return None

    async def _ensure_v10_campaign_seed(
        self,
        snapshot: Mapping[str, Any],
    ) -> dict[str, Any] | None:
        """Create exactly one canonical v10 research problem when the campaign is absent."""
        problems = snapshot.get("problems") or []
        for problem in problems:
            if not isinstance(problem, Mapping):
                continue
            metadata = problem.get("metadata") if isinstance(problem.get("metadata"), Mapping) else {}
            if metadata.get("v10_campaign_id") == V10_CAMPAIGN_ID:
                return None
            if metadata.get("research_stage") in V10_STAGE_KEYS:
                return None

        created = await self.gateway.create_problem({
            "title": "GRAEN Crypto Trend Pullback v10",
            "statement": (
                "Execute the frozen crypto trend-pullback v10 campaign through "
                "development, validation, untouched holdout, VELUM replay, and native "
                "forward shadow. Research only; no broker or production execution authority."
            ),
            "domain": PROBLEM_DOMAIN,
            "priority": 95,
            "source": "GRAEN_RESEARCH_EXECUTOR",
            "requested_by": "ANEVUM",
            "constraints": {
                "research_only": True,
                "execution_authority": False,
                "broker_orders_possible": False,
                "production_promotion_authority": False,
            },
            "success_criteria": {
                "frozen_stage_order": [
                    "DEVELOPMENT", "VALIDATION", "HOLDOUT", "VELUM_REPLAY", "FORWARD_SHADOW"
                ],
                "promotion_requires_all_gates": True,
            },
            "metadata": {
                "v10_campaign_id": V10_CAMPAIGN_ID,
                "v10_bootstrap_version": 1,
                "v10_generation": 1,
            },
        })
        problem = created.get("problem")
        if not isinstance(problem, Mapping) or not problem.get("problem_id"):
            raise RuntimeError("v10_campaign_bootstrap_problem_unavailable")
        problem_id = str(problem["problem_id"])
        queued = await self.gateway.queue_research_stage(
            problem_id=problem_id,
            stage=V10_DEVELOPMENT_STAGE,
            metadata={
                "v10_campaign_id": V10_CAMPAIGN_ID,
                "v10_epoch_index": 0,
                "v10_generation": 1,
                "v10_transition_source": "canonical_bootstrap",
                "v10_bootstrap_version": 1,
            },
        )
        if not queued.get("problem"):
            raise RuntimeError("v10_campaign_bootstrap_queue_failed")
        result = {
            "seeded": True,
            "problem_id": problem_id,
            "next_research_stage": V10_DEVELOPMENT_STAGE,
            "campaign_id": V10_CAMPAIGN_ID,
        }
        print("GRAEN_V10_BOOTSTRAP", result, flush=True)
        return result

    def _observe_blocked_v10(self, snapshot: Mapping[str, Any]) -> dict[str, Any] | None:
        """Surface the stored failure for a blocked v10 stage without mutating research state."""
        problems = snapshot.get("problems") or []
        runs = snapshot.get("runs") or []
        for problem in problems:
            if not isinstance(problem, Mapping):
                continue
            metadata = problem.get("metadata") if isinstance(problem.get("metadata"), Mapping) else {}
            stage = str(metadata.get("research_stage") or "")
            if problem.get("status") != "BLOCKED" or stage not in V10_STAGE_KEYS:
                continue
            problem_id = str(problem.get("problem_id") or "")
            for run in runs:
                if not isinstance(run, Mapping):
                    continue
                if str(run.get("problem_id") or "") != problem_id or run.get("status") != "BLOCKED":
                    continue
                summary = run.get("result_summary") if isinstance(run.get("result_summary"), Mapping) else {}
                if summary.get("state") != "RESEARCH_EXECUTION_BLOCKED":
                    continue
                run_id = str(run.get("run_id") or "")
                if run_id and run_id == self.last_observed_blocked_run_id:
                    return None
                result = {
                    "problem_id": problem_id,
                    "run_id": run_id or None,
                    "research_stage": stage,
                    "epoch_index": metadata.get("v10_epoch_index"),
                    "candidate_id": (
                        metadata.get("v10_candidate_spec", {}).get("candidate_id")
                        if isinstance(metadata.get("v10_candidate_spec"), Mapping)
                        else None
                    ),
                    "error": str(summary.get("error") or "unknown_research_execution_error")[:1000],
                    "next_action": summary.get("next_action"),
                    "execution_authority": False,
                }
                self.last_observed_blocked_run_id = run_id or None
                print("GRAEN_V10_BLOCKED", result, flush=True)
                return result
        return None

    async def _recover_blocked_v10_corpus_into_v11(
        self,
        snapshot: Mapping[str, Any],
    ) -> dict[str, Any] | None:
        """Convert an unusable terminal v10 corpus into the BTC-only v11 path."""
        for problem in snapshot.get("problems") or []:
            if not isinstance(problem, Mapping):
                continue
            metadata = problem.get("metadata") if isinstance(problem.get("metadata"), Mapping) else {}
            if metadata.get("v11_campaign_id") == V11_CAMPAIGN_ID:
                return None
            if metadata.get("research_stage") in V11_STAGE_KEYS:
                return None

        for problem in snapshot.get("problems") or []:
            if not isinstance(problem, Mapping):
                continue
            metadata = problem.get("metadata") if isinstance(problem.get("metadata"), Mapping) else {}
            if (
                problem.get("status") != "BLOCKED"
                or metadata.get("research_stage") != V10_VALIDATION_STAGE
            ):
                continue
            epoch_index = int(metadata.get("v10_epoch_index") or 0)
            if _v10_next_epoch(epoch_index) is not None:
                continue
            problem_id = str(problem.get("problem_id") or "")
            candidate_spec = (
                dict(metadata.get("v10_candidate_spec"))
                if isinstance(metadata.get("v10_candidate_spec"), Mapping)
                else {}
            )
            for run in snapshot.get("runs") or []:
                if not isinstance(run, Mapping):
                    continue
                if str(run.get("problem_id") or "") != problem_id or run.get("status") != "BLOCKED":
                    continue
                summary = run.get("result_summary") if isinstance(run.get("result_summary"), Mapping) else {}
                error = str(summary.get("error") or "")
                if not error.startswith("ValueError: v9_stage_corpus_incomplete:"):
                    continue
                blocked_run_id = str(run.get("run_id") or "")
                artifact = await self.gateway.record_artifact(
                    problem_id=problem_id,
                    run_id=blocked_run_id or None,
                    artifact_type="CRYPTO_TREND_PULLBACK_V10_CORPUS_EXHAUSTION",
                    methodology_version=V10_METHODOLOGY_VERSION,
                    content={
                        "campaign_id": V10_CAMPAIGN_ID,
                        "state": "V10_CAMPAIGN_EXHAUSTED_CORPUS",
                        "epoch_index": epoch_index,
                        "candidate_id": candidate_spec.get("candidate_id"),
                        "blocked_stage": V10_VALIDATION_STAGE,
                        "corpus_error": error,
                        "validation_strategy_evaluation_performed": False,
                        "validation_corpus_availability_inspected": True,
                        "validation_window_burned": True,
                        "historical_promotion_eligible": False,
                        "next_methodology": V11_METHODOLOGY_VERSION,
                        "next_campaign_id": V11_CAMPAIGN_ID,
                        "execution_authority": False,
                        "broker_orders_possible": False,
                        "production_promotion_authority": False,
                    },
                )
                queued = await self.gateway.queue_research_stage(
                    problem_id=problem_id,
                    stage=V11_DEVELOPMENT_STAGE,
                    metadata={
                        "v11_campaign_id": V11_CAMPAIGN_ID,
                        "v11_generation": 1,
                        "v11_origin": "v10_terminal_corpus_exhaustion",
                        "v10_terminal_candidate_id": candidate_spec.get("candidate_id"),
                        "v10_terminal_error": error,
                        "v10_terminal_artifact_id": (
                            (artifact.get("artifact") or {}).get("artifact_id")
                            if isinstance(artifact.get("artifact"), Mapping)
                            else None
                        ),
                    },
                )
                if not queued.get("problem"):
                    raise RuntimeError("v11_queue_after_v10_corpus_exhaustion_failed")
                result = {
                    "recovered": True,
                    "problem_id": problem_id,
                    "state": "V10_CAMPAIGN_EXHAUSTED_CORPUS",
                    "next_research_stage": V11_DEVELOPMENT_STAGE,
                    "v11_campaign_id": V11_CAMPAIGN_ID,
                    "execution_authority": False,
                }
                print("GRAEN_V10_TO_V11", result, flush=True)
                return result
        return None

    async def process_once(self) -> dict[str, Any]:
        snapshot = await self.gateway.snapshot()
        blocked_v10 = self._observe_blocked_v10(snapshot)
        v10_corpus_recovery = await self._recover_blocked_v10_corpus_into_v11(snapshot)
        if v10_corpus_recovery is not None:
            snapshot = await self.gateway.snapshot()
        reconciliation = await self._reconcile_orphaned_confirmatory_claim(snapshot)
        if reconciliation is not None:
            snapshot = await self.gateway.snapshot()
        v10_transition_recovery = await self._recover_exhausted_v9_into_v10(snapshot)
        if v10_transition_recovery is not None:
            snapshot = await self.gateway.snapshot()
        v10_campaign_seed = await self._ensure_v10_campaign_seed(snapshot)
        if v10_campaign_seed is not None:
            snapshot = await self.gateway.snapshot()
        # Research-code promotion is an explicit bounded step in the same
        # deterministic executor loop. Process eligible handoffs without
        # starving unrelated staged research.
        promotion_results: list[dict[str, Any]] = []
        for promotion_problem_id in list(engineering_problem_ids(snapshot))[:4]:
            promotion_state = await self.research_promotion.tick(promotion_problem_id)
            promotion_results.append({
                "problem_id": promotion_problem_id,
                "phase": promotion_state.get("phase"),
                "blocked_reason": promotion_state.get("blocked_reason"),
            })
        staged_v11 = any(
            isinstance(row, Mapping)
            and row.get("status") == "WAITING"
            and row.get("domain") == PROBLEM_DOMAIN
            and isinstance(row.get("metadata"), Mapping)
            and row.get("metadata", {}).get("research_stage") in V11_STAGE_KEYS
            for row in (snapshot.get("problems") or [])
        )
        staged_v10 = any(
            isinstance(row, Mapping)
            and row.get("status") == "WAITING"
            and row.get("domain") == PROBLEM_DOMAIN
            and isinstance(row.get("metadata"), Mapping)
            and row.get("metadata", {}).get("research_stage") in V10_STAGE_KEYS
            for row in (snapshot.get("problems") or [])
        )
        staged_v9 = any(
            isinstance(row, Mapping)
            and row.get("status") == "WAITING"
            and row.get("domain") == PROBLEM_DOMAIN
            and isinstance(row.get("metadata"), Mapping)
            and row.get("metadata", {}).get("research_stage") in V9_STAGE_KEYS
            for row in (snapshot.get("problems") or [])
        )
        staged_autonomous = any(
            isinstance(row, Mapping)
            and row.get("status") == "WAITING"
            and row.get("domain") == PROBLEM_DOMAIN
            and isinstance(row.get("metadata"), Mapping)
            and row.get("metadata", {}).get("research_stage") in AUTONOMOUS_STAGE_KEYS
            for row in (snapshot.get("problems") or [])
        )
        staged_leadlag = any(
            isinstance(row, Mapping)
            and row.get("status") == "WAITING"
            and row.get("domain") == PROBLEM_DOMAIN
            and isinstance(row.get("metadata"), Mapping)
            and row.get("metadata", {}).get("research_stage") == LEADLAG_STAGE_KEY
            for row in (snapshot.get("problems") or [])
        )
        self.active_methodology_version = (
            V11_METHODOLOGY_VERSION
            if staged_v11
            else V10_METHODOLOGY_VERSION
            if staged_v10
            else V9_METHODOLOGY_VERSION
            if staged_v9
            else AUTONOMOUS_METHODOLOGY_PREFIX
            if staged_autonomous
            else LEADLAG_METHODOLOGY_VERSION
            if staged_leadlag
            else V7_METHODOLOGY_VERSION
        )
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
            return {
                "status": "IDLE",
                "claimed": False,
                "research_promotion": promotion_results,
                "reconciliation": reconciliation,
                "v10_transition_recovery": v10_transition_recovery,
                "v10_campaign_seed": v10_campaign_seed,
                "v10_corpus_recovery": v10_corpus_recovery,
                "blocked_v10": blocked_v10,
            }

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

        metadata = problem.get("metadata") if isinstance(problem.get("metadata"), Mapping) else {}
        try:
            if str(metadata.get("research_stage", "")).startswith("CRYPTO_COMPILED_"):
                return await self._execute_compiled_hypothesis(problem, run)
            if metadata.get("research_stage") in V11_STAGE_KEYS:
                return await self._execute_btc_trend_pullback_v11(problem, run)
            if metadata.get("research_stage") in V10_STAGE_KEYS:
                return await self._execute_trend_pullback_v10(problem, run)
            if metadata.get("research_stage") in V9_STAGE_KEYS:
                return await self._execute_activity_shock_v9(problem, run)
            if metadata.get("research_stage") in AUTONOMOUS_STAGE_KEYS:
                return await self._execute_autonomous_campaign(problem, run)
            if metadata.get("research_stage") == LEADLAG_STAGE_KEY:
                return await self._execute_leadlag_r2(problem, run)
            return await self._execute_v7_staged(problem, run)
        except Exception as exc:
            # A claimed run must never be left RUNNING after the worker has
            # abandoned it. Preserve the frozen stage and block it for an
            # explicit repair/reconciliation decision.
            failure = f"{type(exc).__name__}: {exc}"[:1000]
            print(
                "GRAEN_RESEARCH_FAILURE",
                {
                    "problem_id": problem_id,
                    "run_id": run_id,
                    "research_stage": metadata.get("research_stage"),
                    "epoch_index": metadata.get("v10_epoch_index"),
                    "candidate_id": (
                        metadata.get("v10_candidate_spec", {}).get("candidate_id")
                        if isinstance(metadata.get("v10_candidate_spec"), Mapping)
                        else None
                    ),
                    "error": failure,
                    "execution_authority": False,
                },
                flush=True,
            )
            try:
                await self.gateway.block_research_claim(
                    problem_id=problem_id,
                    run_id=run_id,
                    worker_id=self.worker_id,
                    error=failure,
                )
            finally:
                self.active_problem_id = None
                self.last_error = failure
            raise


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


@app.get("/live")
async def live():
    running = runtime.task is not None and not runtime.task.done()
    if not running:
        raise HTTPException(status_code=503, detail={"ok": False, "running": False})
    return {
        "ok": True,
        "service": "graen-research-executor",
        "running": True,
        "runtime_version": RUNTIME_VERSION,
    }


@app.get("/health")
async def health():
    state = runtime.health()
    if not state["ok"]:
        raise HTTPException(status_code=503, detail=state)
    return state


@app.get("/v1/status")
async def status():
    return runtime.health()
