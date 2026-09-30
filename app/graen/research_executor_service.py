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


UTC = timezone.utc
RUNTIME_VERSION = "graen-research-executor-v1.1.0"
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
        fetch_end = end + timedelta(minutes=40)
        raw: dict[str, list[dict[str, Any]]] = {symbol: [] for symbol in symbols}
        chunk_start = fetch_start
        while chunk_start < fetch_end:
            chunk_end = min(chunk_start + timedelta(days=20), fetch_end)
            chunk = await self.market_data.historical_crypto_bars_many(
                list(symbols),
                start=chunk_start,
                end=chunk_end,
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
        snapshot = await self.gateway.snapshot()
        staged_leadlag = any(
            isinstance(row, Mapping)
            and row.get("status") == "WAITING"
            and row.get("domain") == PROBLEM_DOMAIN
            and isinstance(row.get("metadata"), Mapping)
            and row.get("metadata", {}).get("research_stage") == LEADLAG_STAGE_KEY
            for row in (snapshot.get("problems") or [])
        )
        self.active_methodology_version = (
            LEADLAG_METHODOLOGY_VERSION if staged_leadlag else V7_METHODOLOGY_VERSION
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

        metadata = problem.get("metadata") if isinstance(problem.get("metadata"), Mapping) else {}
        if metadata.get("research_stage") == LEADLAG_STAGE_KEY:
            return await self._execute_leadlag_r2(problem, run)

        return await self._execute_v7_staged(problem, run)


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
