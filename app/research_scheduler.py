from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os

import httpx
from datetime import date, datetime, time, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

from .post_event_evidence import PostEventEvidenceRunner
from .research_agent.adaptation_proposal import proposal_from_counterfactual
from .research_agent.adaptive_shadow import (
    aggregate_shadow_validation,
    build_adaptive_shadow_plan,
    evaluate_shadow_session,
    prepare_shadow_rows,
)
from .research_agent.counterfactual_lab import (
    COST_MODEL_VERSION,
    PARAMETER_FEATURES,
    STRESS_ROUND_TRIP_COST_V1,
    aggregate_counterfactual_searches,
    prepare_counterfactual_rows,
    run_counterfactual_search,
)
from .research_agent.control_state import transition_control_state
from .research_agent.graen_adaptive_validation import assess_adaptive_validation
from .research_agent.nostra_session import derive_nostra_regime_timeline
from .research_agent.nostra_transition import (
    build_transition_model,
    evaluate_transition_calibration,
    forecast_next_regime,
)
from .research_agent.parameter_pressure import compute_parameter_pressure
from .research_agent.promotion_gate import evaluate_promotion_gate
from .research_agent.strategy_family_registry import (
    build_strategy_family_registry,
)
from .research_agent.strategy_health import compute_strategy_health
from .research_agent.strategy_router import rank_strategy_families
from .research_agent.shadow_economics_validation import evaluate_shadow_economics
from .research_agent.shadow_allocation_validation import evaluate_shadow_allocation
from .research_reporting import (
    classify_daily,
    enrich_excursions,
    next_research_action,
    reconstruct_closed_trades,
    scan_funnel,
    serialize,
    trade_metrics,
)
from .weekly_reporting import REPORT_VERSION, build_weekly_report

NY = ZoneInfo("America/New_York")
REPORT_AFTER = time(16, 5)
DAILY_REPORT_VERSION = "rhen-daily-v1.7"


def is_last_session_of_week(current: date, next_session: date | None) -> bool:
    if next_session is None:
        return True
    return current.isocalendar()[:2] != next_session.isocalendar()[:2]


class ResearchReportScheduler:
    """Read-only post-close research reporter embedded in the trading service."""

    def __init__(
        self,
        settings: Any,
        client: Any,
        market_data: Any,
        state: Any,
        event_sink: Any,
    ) -> None:
        self.settings = settings
        self.client = client
        self.market_data = market_data
        self.state = state
        self.event_sink = event_sink
        self.stop_event = asyncio.Event()
        self.task: asyncio.Task | None = None
        self.daily_done: set[date] = set()
        self.weekly_done: set[date] = set()
        self.last_daily_report: dict[str, Any] | None = None
        self.last_weekly_report: dict[str, Any] | None = None
        self.last_post_event_summary: dict[str, Any] | None = None
        self.last_error: str | None = None

    def status(self) -> dict[str, Any]:
        return {
            "running": self.task is not None and not self.task.done(),
            "report_after_et": REPORT_AFTER.strftime("%H:%M"),
            "last_daily_session": (
                self.last_daily_report.get("session")
                if self.last_daily_report
                else None
            ),
            "last_weekly_end": (
                self.last_weekly_report.get("week_end")
                if self.last_weekly_report
                else None
            ),
            "last_error": self.last_error,
            "daily_report_version": DAILY_REPORT_VERSION,
            "weekly_report_version": REPORT_VERSION,
            "last_post_event_summary": self.last_post_event_summary,
            "last_weekly_completeness": (
                self.last_weekly_report.get("completeness_state")
                if self.last_weekly_report
                else None
            ),
            "live_configuration_changes_allowed": False,
        }

    async def start(self) -> None:
        if self.task is None:
            self.task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        self.stop_event.set()
        if self.task is not None:
            try:
                await asyncio.wait_for(self.task, timeout=10)
            except TimeoutError:
                self.task.cancel()
                await asyncio.gather(self.task, return_exceptions=True)
            self.task = None

    async def _run(self) -> None:
        catch_up_done = False
        canonical_scheduler = os.getenv(
            "RHEN_CANONICAL_SCHEDULER_ENABLED", ""
        ).strip().lower() in {"1", "true", "yes", "on"}
        while not self.stop_event.is_set():
            # Daily/weekly clock orchestration belongs exclusively to the
            # canonical IREN scheduler when enabled.
            try:
                if not canonical_scheduler:
                    if not catch_up_done:
                        await self._catch_up_latest_completed()
                        catch_up_done = True
                    await self._tick()
                self.last_error = None
            except Exception as exc:
                logger.exception("post-close research reporting failed")
                message = f"research reporting {type(exc).__name__}: {exc}"
                self.last_error = message
                self.state.record_event(
                    kind="research_reporting",
                    action="warning",
                    message="post-close research report failed",
                    reason=message,
                )
            try:
                await asyncio.wait_for(self.stop_event.wait(), timeout=60)
            except asyncio.TimeoutError:
                pass

    async def _catch_up_latest_completed(
        self,
        now: datetime | None = None,
    ) -> None:
        """Backfill the latest completed session after restarts or downtime."""

        current = (now or datetime.now(NY)).astimezone(NY)
        sessions = await self.market_data.market_calendar(
            start=current.date() - timedelta(days=10),
            end=current.date(),
        )
        completed = [
            session
            for session in sessions
            if session < current.date()
            or (
                session == current.date()
                and current.timetz().replace(tzinfo=None) >= REPORT_AFTER
            )
        ]
        if not completed:
            return

        latest = completed[-1]
        if getattr(self.event_sink, "enabled", False):
            stored = await self.fetch_daily_report(session=latest)
            commit = os.environ.get("RAILWAY_GIT_COMMIT_SHA")
            if (stored and commit and stored.get("runtime_git_commit") == commit
                    and stored.get("report_version") == DAILY_REPORT_VERSION
                    and stored.get("session") == latest.isoformat()):
                self.last_daily_report = stored
                self.daily_done.add(latest)
        if latest not in self.daily_done:
            if getattr(self.event_sink, "enabled", False):
                await self.generate_post_event_evidence(latest)
            await self.generate_daily(latest)
            self.daily_done.add(latest)

        if latest in self.weekly_done:
            return

        future_sessions = await self.market_data.market_calendar(
            start=latest + timedelta(days=1),
            end=latest + timedelta(days=10),
        )
        next_session = future_sessions[0] if future_sessions else None
        if is_last_session_of_week(latest, next_session):
            week_start = latest - timedelta(days=latest.weekday())
            await self.generate_weekly(week_start, latest)
            self.weekly_done.add(latest)

    async def _tick(self, now: datetime | None = None) -> None:
        current = (now or datetime.now(NY)).astimezone(NY)
        if current.timetz().replace(tzinfo=None) < REPORT_AFTER:
            return
        if not self.settings.credentials_configured:
            return

        sessions = await self.market_data.market_calendar(
            start=current.date(),
            end=current.date(),
        )
        if current.date() not in sessions:
            return

        if current.date() not in self.daily_done:
            if getattr(self.event_sink, "enabled", False):
                await self.generate_post_event_evidence(current.date())
            await self.generate_daily(current.date())
            self.daily_done.add(current.date())

        if current.date() in self.weekly_done:
            return

        future_sessions = await self.market_data.market_calendar(
            start=current.date() + timedelta(days=1),
            end=current.date() + timedelta(days=10),
        )
        next_session = future_sessions[0] if future_sessions else None
        if is_last_session_of_week(current.date(), next_session):
            week_start = current.date() - timedelta(days=current.weekday())
            await self.generate_weekly(week_start, current.date())
            self.weekly_done.add(current.date())

    async def _collect(
        self,
        start_date: date,
        end_date: date,
    ) -> dict[str, Any]:
        sessions = await self.market_data.market_calendar(
            start=start_date,
            end=end_date,
        )
        if not sessions:
            return {
                "sessions": [],
                "orders": [],
                "fills": [],
                "trades": [],
                "metrics": trade_metrics([]),
                "funnel": scan_funnel(self.state.decision_history),
            }

        # Research reads must not quietly truncate broker history. Use stable
        # order-ID and activity-ID pagination, never the live order writer.
        start_at = datetime.combine(sessions[0], time.min, tzinfo=NY)
        end_at = datetime.combine(
            sessions[-1] + timedelta(days=1), time.min, tzinfo=NY
        )
        orders = await self.client.research_orders_for_window(
            start_at=start_at, end_at=end_at
        )
        data_quality_warnings: list[str] = []
        fills: list[dict[str, Any]] = []
        for session in sessions:
            fills.extend(
                await self.client.research_fills_for_session(
                    date=session.isoformat()
                )
            )

        positions = await self.client.positions()

        rebuilt = reconstruct_closed_trades(
            fills,
            orders,
            owner_tag=str(getattr(self.settings, "order_owner_tag", "") or ""),
        )
        trades = rebuilt["trades"]
        if any(qty > 0 for qty in rebuilt.get("unmatched_sell_qty", {}).values()):
            data_quality_warnings.append(
                "Broker sell fills could not be paired with same-window buy lots; "
                "carry positions require earlier immutable fills before P/L inference."
            )

        symbols = sorted({trade["symbol"] for trade in trades})
        if symbols:
            start = datetime.combine(sessions[0], time(9, 30), tzinfo=NY)
            end = datetime.combine(sessions[-1], time(16, 0), tzinfo=NY)
            bars = await self.market_data.historical_bars_many(
                symbols,
                start=start,
                end=end,
            )
            enrich_excursions(trades, bars)

        return {
            "sessions": [session.isoformat() for session in sessions],
            "orders": orders,
            "fills": fills,
            "positions": positions,
            "data_quality_warnings": data_quality_warnings,
            "broker_history": {
                "pagination": "EXHAUSTED_WITHIN_BOUNDS",
                "order_count": len(orders),
                "fill_count": len(fills),
                "requested_session_dates": [day.isoformat() for day in sessions],
                "start_at": start_at.isoformat(),
                "end_at": end_at.isoformat(),
                "does_not_certify_candidate_quote_or_live_replay_parity": True,
            },
            "trades": trades,
            "reconstruction": {
                key: value
                for key, value in rebuilt.items()
                if key != "trades"
            },
            "metrics": trade_metrics(trades),
            "funnel": scan_funnel(self.state.decision_history),
        }

    def _runtime_snapshot(self) -> dict[str, Any]:
        persistence = self.event_sink.status()
        return {
            "last_error": self.state.last_error,
            "reconciliation_safe": self.state.reconciliation_safe,
            "startup_reconciled": self.state.startup_reconciled,
            "last_reconciliation": self.state.last_reconciliation,
            "persistence_error": persistence.get("last_error"),
            "persistence": persistence,
        }

    async def generate_post_event_evidence(self, session: date) -> dict[str, Any]:
        """Compute analytics only after the session and all requested horizons mature."""
        async def post_event_evidence_reader(**params: str) -> dict[str, Any]:
            requested = params.get("evidence_session")
            if not requested:
                raise RuntimeError("post-event evidence session is required")
            return await self._report_api_get(
                timeout=90.0,
                post_event_evidence_session=requested,
            )

        runner = PostEventEvidenceRunner(
            settings=self.settings,
            market_data=self.market_data,
            event_sink=self.event_sink,
            evidence_reader=post_event_evidence_reader,
        )
        summary = await runner.run_session(session)
        if getattr(self.event_sink, "enabled", False):
            await self.event_sink.queue.join()
            refreshed = await self.event_sink.emit_critical(
                event_type="ads002_postclose_refresh",
                event_key=f"ads002_postclose_refresh:{session.isoformat()}:ADS-002-v1",
                occurred_at=datetime.now(NY).isoformat(),
                payload={
                    "session": session.isoformat(),
                    "methodology_version": "ADS-002-v1",
                    "analytics_only": True,
                    "live_configuration_changed": False,
                },
            )
            if not refreshed:
                raise RuntimeError(
                    self.event_sink.last_error
                    or "ADS-002 post-close refresh could not be persisted"
                )
        payload = {
            "session": summary.session,
            "candidates": summary.candidates,
            "outcome_events": summary.outcome_events,
            "comparison_events": summary.comparison_events,
            "complete_outcomes": summary.complete_outcomes,
            "incomplete_outcomes": summary.incomplete_outcomes,
            "error_outcomes": summary.error_outcomes,
            "skipped_complete_outcomes": summary.skipped_complete_outcomes,
            "measurable_candidates": summary.measurable_candidates,
            "unmeasurable_candidates": summary.unmeasurable_candidates,
            "unmeasurable_reasons": summary.unmeasurable_reasons or {},
            "reused_existing_evidence": summary.reused_existing_evidence,
            "analytics_only": True,
        }
        self.last_post_event_summary = payload
        return payload

    async def _daily_post_event_inputs(self, session: date) -> dict[str, Any]:
        try:
            payload = await self._report_api_get(
                evidence_session=session.isoformat(),
            )
        except Exception as exc:
            return {
                "post_event": {},
                "ads002": {},
                "ads002_v2": {},
                "candidates": [],
                "evidence_readiness": {},
                "latest_daily_report": None,
                "warning": f"canonical post-event evidence unavailable: {type(exc).__name__}: {exc}",
            }
        return {
            "post_event": payload.get("post_event") or {},
            "ads002": payload.get("ads002") or {},
            "ads002_v2": payload.get("ads002_v2") or {},
            "candidates": payload.get("candidates") or [],
            "evidence_readiness": payload.get("evidence_readiness") or {},
            "latest_daily_report": payload.get("latest_daily_report"),
            "warning": None,
        }

    async def _counterfactual_history_reports(
        self,
        session: date,
    ) -> tuple[list[dict[str, Any]], str | None]:
        start = session - timedelta(days=35)
        try:
            inputs = await self._canonical_weekly_inputs(start, session)
        except Exception as exc:
            return [], (
                "ASC-005 rolling history unavailable: "
                f"{type(exc).__name__}: {exc}"
            )

        reports: list[dict[str, Any]] = []
        for record in inputs.get("daily_reports") or []:
            if not isinstance(record, dict):
                continue
            payload = record.get("payload")
            if not isinstance(payload, dict):
                continue
            if str(payload.get("session") or "") == session.isoformat():
                continue
            reports.append(payload)
        return reports, None

    async def _build_counterfactual_lab(
        self,
        session: date,
        candidates: list[dict[str, Any]],
    ) -> tuple[dict[str, Any], str | None]:
        current_version = str(
            getattr(self.settings, "strategy_version_id", "") or ""
        )
        scoped_candidates = [
            row
            for row in candidates
            if isinstance(row, dict)
            and (
                not current_version
                or str(row.get("strategy_version_id") or "") == current_version
            )
        ]

        baseline_parameters = {
            parameter: getattr(self.settings, parameter, None)
            for parameter in PARAMETER_FEATURES
        }
        session_searches: dict[str, Any] = {}
        for parameter, current_value in baseline_parameters.items():
            if current_value in (None, ""):
                continue
            rows = prepare_counterfactual_rows(
                scoped_candidates,
                parameter=parameter,
            )
            session_searches[parameter] = run_counterfactual_search(
                rows=rows,
                parameter=parameter,
                current_value=current_value,
                horizon_minutes=15,
                round_trip_cost=STRESS_ROUND_TRIP_COST_V1,
                cadence="daily",
            )

        prior_reports, history_warning = await self._counterfactual_history_reports(
            session
        )
        rolling_searches: dict[str, Any] = {}
        for parameter, current_search in session_searches.items():
            searches: list[dict[str, Any]] = []
            for report in prior_reports:
                if (
                    current_version
                    and str(report.get("strategy_version_id") or "")
                    != current_version
                ):
                    continue
                lab = report.get("counterfactual_lab")
                if not isinstance(lab, dict):
                    continue
                prior = (lab.get("session_searches") or {}).get(parameter)
                if isinstance(prior, dict):
                    searches.append(prior)
            searches.append(current_search)
            rolling = aggregate_counterfactual_searches(searches)
            if rolling is not None:
                rolling_searches[parameter] = rolling

        ready = sorted(
            parameter
            for parameter, result in rolling_searches.items()
            if isinstance(result, dict) and result.get("validity_passed") is True
        )

        return (
            {
                "methodology_version": "asc-counterfactual-lab-v1",
                "session": session.isoformat(),
                "strategy_version_id": current_version or None,
                "baseline_parameters": baseline_parameters,
                "candidate_rows_received": len(candidates),
                "candidate_rows_in_strategy_scope": len(scoped_candidates),
                "history_window_calendar_days": 35,
                "cost_model": {
                    "version": COST_MODEL_VERSION,
                    "round_trip_cost": str(STRESS_ROUND_TRIP_COST_V1),
                    "round_trip_bps": "22",
                    "role": "conservative research stress floor",
                },
                "session_searches": session_searches,
                "rolling_searches": rolling_searches,
                "proposal_ready_parameters": ready,
                "screening_only": True,
                "counterfactual_not_realized_trades": True,
                "read_only": True,
                "execution_authority": False,
                "risk_or_sizing_authority": False,
                "live_configuration_changed": False,
                "promotion_authorized": False,
            },
            history_warning,
        )

    def _adaptive_baseline_configuration(self) -> dict[str, str]:
        return {
            parameter: str(getattr(self.settings, parameter))
            for parameter in PARAMETER_FEATURES
            if getattr(self.settings, parameter, None) not in (None, "")
        }

    async def _build_nostra_research(
        self,
        session: date,
        candidates: list[dict[str, Any]],
    ) -> tuple[dict[str, Any], str | None]:
        current_version = str(
            getattr(self.settings, "strategy_version_id", "") or ""
        )
        session_state = derive_nostra_regime_timeline(candidates)
        prior_reports, warning = await self._counterfactual_history_reports(
            session
        )

        prior_observations: list[dict[str, Any]] = []
        historical_predictions: list[dict[str, Any]] = []
        for report in prior_reports:
            if (
                current_version
                and str(report.get("strategy_version_id") or "")
                != current_version
            ):
                continue
            prior_nostra = report.get("nostra")
            if not isinstance(prior_nostra, dict):
                continue
            prior_state = prior_nostra.get("session_state")
            if isinstance(prior_state, dict):
                for row in prior_state.get("timeline") or []:
                    if isinstance(row, dict):
                        prior_observations.append(row)
            for row in prior_nostra.get("transition_predictions") or []:
                if isinstance(row, dict):
                    historical_predictions.append(row)

        current_timeline = [
            row
            for row in session_state.get("timeline") or []
            if isinstance(row, dict)
        ]
        frozen_prior_model = build_transition_model(prior_observations)
        current_predictions: list[dict[str, Any]] = []
        if int(frozen_prior_model.get("total_transitions") or 0) > 0:
            for current_row, next_row in zip(
                current_timeline,
                current_timeline[1:],
            ):
                current_regime = current_row.get("regime")
                realized_regime = next_row.get("regime")
                if not current_regime or not realized_regime:
                    continue
                forecast = forecast_next_regime(
                    frozen_prior_model,
                    current_regime=str(current_regime),
                )
                current_predictions.append(
                    {
                        "session": session.isoformat(),
                        "observed_at": current_row.get("observed_at"),
                        "current_regime": current_regime,
                        "probabilities": forecast.get("probabilities") or {},
                        "forecast_confidence": forecast.get("confidence"),
                        "forecast_minimums_met": forecast.get("minimums_met"),
                        "realized_regime": realized_regime,
                        "evaluation": "prior_sessions_to_next_5m_within_session",
                    }
                )

        transition_calibration = evaluate_transition_calibration(
            [*historical_predictions, *current_predictions]
        )
        transition_model = build_transition_model(
            [*prior_observations, *current_timeline]
        )

        return (
            {
                "methodology_version": "nostra-research-v1",
                "session": session.isoformat(),
                "strategy_version_id": current_version or None,
                "session_state": session_state,
                "transition_model": transition_model,
                "transition_predictions": current_predictions,
                "transition_calibration": transition_calibration,
                "next_regime_forecast": None,
                "next_regime_forecast_reason": (
                    "POST_CLOSE_SESSION_BOUNDARY_NOT_MODELED"
                ),
                "research_only": True,
                "execution_authority": False,
                "live_configuration_changed": False,
                "promotion_authorized": False,
            },
            warning,
        )

    @staticmethod
    def _ads002_evidence_quality(
        ads002_v2: dict[str, Any],
    ) -> dict[str, Any]:
        models = [
            row
            for row in ads002_v2.get("models") or []
            if isinstance(row, dict)
        ]
        samples = []
        direct_coverages = []
        forward_coverages = []
        for model in models:
            confidence = model.get("confidence")
            if not isinstance(confidence, dict):
                continue
            sample = confidence.get("samples")
            coverage = confidence.get("coverage")
            if isinstance(sample, dict):
                samples.append(sample)
            if isinstance(coverage, dict):
                try:
                    direct_coverages.append(
                        float(coverage.get("direct_attribution") or 0)
                    )
                    forward_coverages.append(
                        float(coverage.get("forward_15m") or 0)
                    )
                except (TypeError, ValueError):
                    pass

        def minimum_sample(key: str) -> int:
            values = []
            for row in samples:
                try:
                    values.append(int(row.get(key) or 0))
                except (TypeError, ValueError):
                    values.append(0)
            return min(values) if values else 0

        return {
            "independent_sessions": minimum_sample("independent_sessions"),
            "eligible_candidates": minimum_sample(
                "research_eligible_candidates"
            ),
            "directly_attributed_closed_trades": minimum_sample(
                "closed_direct_trades"
            ),
            "direct_attribution_coverage": (
                min(direct_coverages) if direct_coverages else 0
            ),
            "forward_15m_coverage": (
                min(forward_coverages) if forward_coverages else 0
            ),
            "source": "ads002_v2_confidence_state",
        }

    def _bounded_proposals_from_lab(
        self,
        *,
        counterfactual_lab: dict[str, Any],
        strategy_health: dict[str, Any],
    ) -> dict[str, Any]:
        state = str(strategy_health.get("control_state") or "")
        if state not in {"ADAPT", "RESEARCH"}:
            return {}

        current_version = str(
            getattr(self.settings, "strategy_version_id", "") or ""
        )
        baseline = counterfactual_lab.get("baseline_parameters") or {}
        rolling = counterfactual_lab.get("rolling_searches") or {}
        proposals: dict[str, Any] = {}
        for parameter in counterfactual_lab.get(
            "proposal_ready_parameters"
        ) or []:
            result = rolling.get(parameter)
            current_value = baseline.get(parameter)
            if not isinstance(result, dict) or current_value in (None, ""):
                continue
            proposal = proposal_from_counterfactual(
                parameter=str(parameter),
                current_value=current_value,
                alternatives=result.get("results") or [],
                control_state=state,
                source_strategy_version=current_version,
            )
            if proposal is not None:
                proposals[str(parameter)] = proposal
        return proposals

    async def _build_adaptive_research(
        self,
        *,
        session: date,
        candidates: list[dict[str, Any]],
        counterfactual_lab: dict[str, Any],
        nostra: dict[str, Any],
        metrics: dict[str, Any],
        runtime: dict[str, Any],
        outcome_status: dict[str, Any],
        ads002_v2: dict[str, Any],
    ) -> tuple[dict[str, Any], str | None]:
        current_version = str(
            getattr(self.settings, "strategy_version_id", "") or ""
        )
        prior_reports, warning = await self._counterfactual_history_reports(
            session
        )
        scoped_prior_reports = [
            report
            for report in prior_reports
            if isinstance(report, dict)
            and (
                not current_version
                or str(report.get("strategy_version_id") or "")
                == current_version
            )
        ]
        health_source = {
            "session": session.isoformat(),
            "strategy_version_id": current_version or None,
            "metrics": metrics,
            "runtime": runtime,
            "candidate_forward_evidence": {"status": outcome_status},
            "ads002_v2": ads002_v2,
            "counterfactual_lab": counterfactual_lab,
            "nostra": nostra,
        }
        session_state = nostra.get("session_state") or {}
        latest_nostra_raw = (
            session_state.get("latest")
            if isinstance(session_state, dict)
            else None
        )
        transition_calibration = nostra.get("transition_calibration")
        latest_nostra = (
            {
                **latest_nostra_raw,
                "calibration": transition_calibration,
            }
            if isinstance(latest_nostra_raw, dict)
            and isinstance(transition_calibration, dict)
            else latest_nostra_raw
        )
        ordered_prior = sorted(
            scoped_prior_reports,
            key=lambda report: str(report.get("session") or ""),
        )
        pressure_reports = [
            *ordered_prior,
            {
                **health_source,
                "counterfactual_lab": counterfactual_lab,
            },
        ]
        parameter_pressure = compute_parameter_pressure(pressure_reports)
        strategy_health = compute_strategy_health(
            daily_report=health_source,
            weekly_report=None,
            nostra_state=(
                latest_nostra if isinstance(latest_nostra, dict) else None
            ),
            parameter_pressure=parameter_pressure,
        )

        def prior_control_state(report: dict[str, Any]) -> str | None:
            adaptive = report.get("adaptive_strategy_control")
            if not isinstance(adaptive, dict):
                return None
            transition = adaptive.get("control_transition")
            if isinstance(transition, dict) and transition.get("state"):
                return str(transition.get("state"))
            prior_health = adaptive.get("strategy_health")
            if isinstance(prior_health, dict) and prior_health.get("control_state"):
                return str(prior_health.get("control_state"))
            return None

        previous_state = (
            prior_control_state(ordered_prior[-1])
            if ordered_prior else None
        ) or "NORMAL"
        prior_counters: dict[str, Any] = {}
        if ordered_prior:
            adaptive = ordered_prior[-1].get("adaptive_strategy_control")
            if isinstance(adaptive, dict):
                transition = adaptive.get("control_transition")
                if isinstance(transition, dict):
                    counters = transition.get("counters")
                    if isinstance(counters, dict):
                        prior_counters = counters

        requested_state = str(
            strategy_health.get("control_state") or "NORMAL"
        )
        clear_count = (
            int(prior_counters.get("consecutive_clear_observations") or 0) + 1
            if requested_state == "NORMAL"
            else 0
        )
        adapt_count = (
            int(prior_counters.get("consecutive_adapt_observations") or 0) + 1
            if requested_state == "ADAPT"
            else 0
        )
        research_count = (
            int(prior_counters.get("consecutive_research_observations") or 0) + 1
            if requested_state == "RESEARCH"
            else 0
        )
        control_transition = transition_control_state(
            previous_state=previous_state,
            health_snapshot=strategy_health,
            consecutive_clear_observations=clear_count,
            consecutive_adapt_observations=adapt_count,
            consecutive_research_observations=research_count,
        )
        effective_health = {
            **strategy_health,
            "requested_control_state": requested_state,
            "control_state": control_transition.get("state"),
            "control_transition": control_transition,
        }
        proposals = self._bounded_proposals_from_lab(
            counterfactual_lab=counterfactual_lab,
            strategy_health=effective_health,
        )

        current_evaluation = None
        previous_plan = None
        for report in reversed(ordered_prior):
            adaptive = report.get("adaptive_strategy_control")
            if not isinstance(adaptive, dict):
                continue
            plan = adaptive.get("next_session_shadow_plan")
            if isinstance(plan, dict):
                previous_plan = plan
                break

        scoped_candidates = [
            row
            for row in candidates
            if isinstance(row, dict)
            and (
                not current_version
                or str(row.get("strategy_version_id") or "")
                == current_version
            )
        ]
        if (
            isinstance(previous_plan, dict)
            and str(previous_plan.get("source_strategy_version") or "")
            == current_version
            and str(previous_plan.get("source_session") or "")
            < session.isoformat()
        ):
            current_evaluation = evaluate_shadow_session(
                rows=prepare_shadow_rows(scoped_candidates),
                plan=previous_plan,
                evaluation_session=session.isoformat(),
                round_trip_cost=STRESS_ROUND_TRIP_COST_V1,
            )

        evaluations = []
        for report in ordered_prior:
            adaptive = report.get("adaptive_strategy_control")
            if not isinstance(adaptive, dict):
                continue
            evaluation = adaptive.get("shadow_evaluation")
            if isinstance(evaluation, dict):
                evaluations.append(evaluation)
        if current_evaluation is not None:
            evaluations.append(current_evaluation)

        shadow_validation = aggregate_shadow_validation(evaluations)

        next_plan = None
        if proposals:
            next_plan = build_adaptive_shadow_plan(
                source_session=session.isoformat(),
                source_strategy_version=current_version,
                baseline_configuration=self._adaptive_baseline_configuration(),
                proposals=proposals,
            )

        graen_validation = assess_adaptive_validation(
            counterfactual_lab=counterfactual_lab,
            shadow_validation=shadow_validation,
            holdout_result=None,
        )
        evidence_quality = self._ads002_evidence_quality(ads002_v2)

        promotion_previews: dict[str, Any] = {}
        for parameter, proposal in proposals.items():
            promotion_previews[parameter] = evaluate_promotion_gate(
                proposal=proposal,
                source_strategy_version=current_version,
                strategy_health=effective_health,
                shadow_validation=shadow_validation,
                evidence_quality=evidence_quality,
                graen_validation=graen_validation,
                human_authorization=None,
            )

        family_registry = build_strategy_family_registry()
        family_routing = rank_strategy_families(
            regime_state=(
                latest_nostra
                if isinstance(latest_nostra, dict)
                else {"regime": "UNKNOWN"}
            ),
            families=family_registry["families"],
        )

        return (
            {
                "methodology_version": "iren-asc-v1",
                "session": session.isoformat(),
                "strategy_version_id": current_version or None,
                "strategy_health": effective_health,
                "parameter_pressure": parameter_pressure,
                "control_transition": control_transition,
                "strategy_family_registry": family_registry,
                "strategy_family_routing": family_routing,
                "parameter_proposals": proposals,
                "proposal_count": len(proposals),
                "shadow_evaluation": current_evaluation,
                "shadow_validation": shadow_validation,
                "next_session_shadow_plan": next_plan,
                "graen_validation": graen_validation,
                "evidence_quality": evidence_quality,
                "promotion_previews": promotion_previews,
                "automatic_application_authorized": False,
                "execution_authority": False,
                "risk_or_sizing_authority": False,
                "live_configuration_changed": False,
                "promotion_authorized": False,
            },
            warning,
        )

    def _record_asc_notifications(
        self,
        adaptive_control: dict[str, Any],
    ) -> None:
        transition = adaptive_control.get("control_transition")
        if isinstance(transition, dict) and transition.get("changed") is True:
            previous = str(transition.get("previous_state") or "UNKNOWN")
            state = str(transition.get("state") or "UNKNOWN")
            reasons = ", ".join(
                str(code)
                for code in transition.get("transition_reason_codes") or []
            )
            self.state.record_event(
                kind="asc",
                action=state.lower(),
                message=(
                    f"IREN ASC {previous} -> {state}; "
                    f"{reasons or 'state transition'}; production unchanged"
                ),
                payload={
                    "previous_state": previous,
                    "state": state,
                    "reason_codes": transition.get(
                        "transition_reason_codes"
                    ) or [],
                    "execution_authority": False,
                },
            )

        previews = adaptive_control.get("promotion_previews")
        if not isinstance(previews, dict):
            return
        ready = [
            parameter
            for parameter, preview in previews.items()
            if isinstance(preview, dict)
            and preview.get("eligible_for_human_authorization") is True
        ]
        if ready:
            self.state.record_event(
                kind="asc",
                action="promotion_ready",
                message=(
                    "ASC research gate is ready for proposal-specific human "
                    f"review: {', '.join(sorted(ready))}; no deployment occurred"
                ),
                payload={
                    "parameters": sorted(ready),
                    "execution_authority": False,
                    "production_mutation_performed": False,
                },
            )

    async def generate_daily(self, session: date) -> dict[str, Any]:
        evidence = await self._collect(session, session)
        account = await self.client.account()
        canonical = await self._daily_post_event_inputs(session)
        post_event = canonical.get("post_event") or {}
        ads002 = canonical.get("ads002") or {}
        ads002_v2 = canonical.get("ads002_v2") or {}
        candidates = list(canonical.get("candidates") or [])
        evidence_readiness = canonical.get("evidence_readiness") or {}
        outcome_status = post_event.get("forward_outcome_status") or {}
        shadow_economics_validation = evaluate_shadow_economics(candidates)
        shadow_allocation_validation = evaluate_shadow_allocation(candidates)
        counterfactual_lab, counterfactual_warning = (
            await self._build_counterfactual_lab(
                session,
                candidates,
            )
        )
        nostra, nostra_warning = await self._build_nostra_research(
            session,
            candidates,
        )
        runtime = self._runtime_snapshot()
        adaptive_control, adaptive_warning = (
            await self._build_adaptive_research(
                session=session,
                candidates=candidates,
                counterfactual_lab=counterfactual_lab,
                nostra=nostra,
                metrics=evidence["metrics"],
                runtime=runtime,
                outcome_status=outcome_status,
                ads002_v2=ads002_v2,
            )
        )
        classification = classify_daily(evidence["metrics"], runtime)
        if (
            evidence["data_quality_warnings"]
            and classification.get("classification") == "KEEP"
        ):
            classification = {
                "classification": "INVESTIGATE",
                "reason": "broker evidence may be truncated; completeness must be resolved first",
                "defects": [],
                "data_quality_warnings": list(evidence["data_quality_warnings"]),
            }
        action = next_research_action(
            evidence["metrics"],
            evidence["funnel"],
            classification,
        )
        ads_readiness = ads002.get("readiness") or {}
        ads_reason_codes = [
            str(code)
            for code in (ads_readiness.get("reason_codes") or [])
            if code
        ]
        hard_ads_blockers = {
            "AMBIGUOUS_ATTRIBUTION",
            "UNLINKED_EXECUTABLE_SIGNAL",
            "DIRECT_ATTRIBUTION_COVERAGE",
        }
        if hard_ads_blockers.intersection(ads_reason_codes):
            action = (
                "repair ADS-002 attribution integrity before interpreting "
                "candidate-score performance"
            )
        daily_warnings = list(evidence["data_quality_warnings"])
        persistence = runtime.get("persistence") or {}
        if persistence.get("storage_analytics_shedding") or int(persistence.get("shed_count") or 0):
            daily_warnings.append(
                "Canonical storage shed analytics during this runtime; computed outcomes are not proof of durable coverage. Resolve retention pressure before interpreting the cohort as complete."
            )
        if canonical.get("warning"):
            daily_warnings.append(str(canonical["warning"]))
        if counterfactual_warning:
            daily_warnings.append(counterfactual_warning)
        if nostra_warning:
            daily_warnings.append(nostra_warning)
        if adaptive_warning and adaptive_warning not in daily_warnings:
            daily_warnings.append(adaptive_warning)
        if ads_reason_codes:
            daily_warnings.append(
                "ADS-002 readiness: " + ", ".join(ads_reason_codes)
            )
        if int(outcome_status.get("incomplete_rows") or 0):
            daily_warnings.append(
                "Some candidate horizons are incomplete because the requested window did not have sufficient regular-session data."
            )
        if int(outcome_status.get("error_rows") or 0):
            daily_warnings.append(
                "One or more candidate forward-outcome measurements failed and remain explicitly recorded as errors."
            )
        post_event_summary = self.last_post_event_summary or {}
        unmeasurable_candidates = int(
            post_event_summary.get("unmeasurable_candidates") or 0
        )
        if unmeasurable_candidates:
            reasons = post_event_summary.get("unmeasurable_reasons") or {}
            rendered = ", ".join(
                f"{key}={value}"
                for key, value in sorted(reasons.items())
                if value
            )
            daily_warnings.append(
                "Forward-outcome measurement excluded "
                f"{unmeasurable_candidates} candidates lacking exact durable "
                "decision evidence"
                + (f" ({rendered})." if rendered else ".")
            )
        readiness_state = str(
            evidence_readiness.get("state") or ""
        ).upper()
        if readiness_state == "AWAITING_MEASURABLE_COHORT":
            daily_warnings.append(
                "Forward-outcome validation is awaiting the first post-fix measurable live cohort; legacy unmeasurable rows are retained as historical evidence only."
            )
        elif readiness_state == "PARTIAL":
            daily_warnings.append(
                "Forward-outcome evidence is partially measurable; valid rows remain usable while missing decision-time references are investigated."
            )
        elif readiness_state == "DEGRADED":
            daily_warnings.append(
                "Forward-outcome validation is degraded because the sampled cohort lacks exact decision-time price/time evidence."
            )
        shadow_horizons = shadow_economics_validation.get("horizons") or []
        shadow_observed = [
            row for row in shadow_horizons
            if isinstance(row, dict)
            and int(row.get("shadow_candidate_count") or 0) > 0
        ]
        if shadow_observed and all(
            str(row.get("state") or "") in {"COLLECTING", "OBSERVING"}
            for row in shadow_observed
        ):
            daily_warnings.append(
                "Shadow opportunity economics is collecting post-event calibration evidence; no production gate is changed by this evidence."
            )
        allocation_horizons = (
            shadow_allocation_validation.get("horizons") or []
        )
        allocation_observed = [
            row for row in allocation_horizons
            if isinstance(row, dict)
            and int(row.get("selected_entry_count") or 0) > 0
        ]
        if allocation_observed and all(
            str(row.get("state") or "") in {"COLLECTING", "OBSERVING"}
            for row in allocation_observed
        ):
            daily_warnings.append(
                "Shadow capital allocation is collecting post-event contribution evidence; no live sizing or capital authority is changed by this evidence."
            )
        live_offline = post_event.get("live_offline_summary") or []
        if any(int(row.get("unreconstructable") or 0) for row in live_offline):
            daily_warnings.append(
                "Some live-vs-offline decisions are unreconstructable because required decision-time evidence was not historically retained."
            )

        fingerprint_material = serialize(
            {
                "version": DAILY_REPORT_VERSION,
                "session": session.isoformat(),
                "strategy_version_id": getattr(
                    self.settings, "strategy_version_id", ""
                )
                or None,
                "metrics": evidence["metrics"],
                "forward_outcomes": post_event.get(
                    "forward_outcomes_by_horizon"
                )
                or [],
                "live_offline": live_offline,
                "ads002": ads002,
                "ads002_v2": ads002_v2,
                "evidence_readiness": evidence_readiness,
                "shadow_economics_validation": shadow_economics_validation,
                "shadow_allocation_validation": shadow_allocation_validation,
                "counterfactual_lab": counterfactual_lab,
                "nostra": nostra,
                "adaptive_strategy_control": adaptive_control,
            }
        )
        source_fingerprint = hashlib.sha256(
            json.dumps(
                fingerprint_material,
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        ).hexdigest()
        report_key = (
            f"{session.isoformat()}:{DAILY_REPORT_VERSION}:"
            f"{source_fingerprint[:16]}"
        )
        previous = canonical.get("latest_daily_report")
        previous_payload = (
            previous.get("payload")
            if isinstance(previous, dict) and isinstance(previous.get("payload"), dict)
            else {}
        )
        supersedes = (
            previous.get("event_id")
            if isinstance(previous, dict)
            and previous_payload.get("report_key") != report_key
            else None
        )

        payload = serialize(
            {
                "report_type": "daily",
                "report_version": DAILY_REPORT_VERSION,
                "report_key": report_key,
                "source_fingerprint": source_fingerprint,
                "supersedes_report_event_id": supersedes,
                "title": f"Daily review — {session.isoformat()}",
                "summary": classification.get("reason"),
                "focus": action,
                "session": session.isoformat(),
                "generated_at": datetime.now(NY),
                "trading_run_id": getattr(self.settings, "trading_run_id", "") or None,
                "strategy_version_id": getattr(self.settings, "strategy_version_id", "") or None,
                "account": {
                    "equity": account.get("equity"),
                    "last_equity": account.get("last_equity"),
                    "cash": account.get("cash"),
                    "buying_power": account.get("buying_power"),
                },
                "metrics": evidence["metrics"],
                "trades": evidence["trades"],
                "open_positions": [
                    {
                        key: position.get(key)
                        for key in (
                            "symbol",
                            "qty",
                            "avg_entry_price",
                            "current_price",
                            "market_value",
                            "unrealized_pl",
                            "unrealized_plpc",
                        )
                    }
                    for position in evidence["positions"]
                ],
                "data_quality_warnings": daily_warnings,
                "candidate_forward_evidence": {
                    "by_horizon": post_event.get("forward_outcomes_by_horizon") or [],
                    "status": outcome_status,
                    "readiness": evidence_readiness,
                    "post_event_only": True,
                    "counterfactual_not_realized_trades": True,
                },
                "live_vs_offline_consistency": {
                    "summary": live_offline,
                    "methodology": "live-offline-v1",
                    "post_event_only": True,
                },
                "ads002": ads002,
                "ads002_v2": ads002_v2,
                "shadow_economics_validation": shadow_economics_validation,
                "shadow_allocation_validation": shadow_allocation_validation,
                "counterfactual_lab": counterfactual_lab,
                "nostra": nostra,
                "adaptive_strategy_control": adaptive_control,
                "strategy_health": adaptive_control.get("strategy_health") or {},
                "graen_validation": adaptive_control.get("graen_validation") or {},
                "reconstruction": evidence["reconstruction"],
                "broker_history": evidence.get("broker_history"),
                "candidate_funnel": evidence["funnel"],
                "runtime": runtime,
                "classification": classification,
                "next_offline_research_action": action,
                "slippage_vs_signal_reference": {
                    "available": False,
                    "reason": (
                        "broker fills are available, but this reporter does not yet read "
                        "the canonical signal-reference export; no slippage estimate is fabricated"
                    ),
                },
                "live_configuration_changed": False,
                "promotion_authorized": False,
                "capital_scaling_authorized": False,
            }
        )
        payload["runtime_git_commit"] = os.environ.get("RAILWAY_GIT_COMMIT_SHA")
        persisted = await self.event_sink.emit_critical(
            event_type="research_daily_report",
            event_key=f"research_daily_report:{report_key}",
            occurred_at=datetime.now(NY).isoformat(),
            payload=payload,
        )
        if not persisted:
            raise RuntimeError("canonical daily report could not be durably persisted")
        print(json.dumps({"event": "daily_research_confirmed", "session": session.isoformat(),
            "classification": payload.get("classification"), "metrics": payload.get("metrics"),
            "warnings": payload.get("data_quality_warnings"), "focus": payload.get("focus")},
            sort_keys=True, default=str), flush=True)
        self.last_daily_report = payload
        self.last_error = None
        self._record_asc_notifications(adaptive_control)
        self.state.record_event(kind="research_reporting", action="completed",
                                message=f"Daily research report persisted for {session.isoformat()}")
        return payload

    @property
    def _report_read_url(self) -> str:
        ingest_url = str(getattr(self.settings, "trading_ingest_url", "") or "")
        if not ingest_url:
            raise RuntimeError("canonical trading persistence is not configured")
        return f"{ingest_url.rsplit('/', 1)[0]}/trading-report-read"

    async def _report_api_get(
        self,
        *,
        timeout: float = 30.0,
        **params: str,
    ) -> dict[str, Any]:
        token = str(getattr(self.settings, "trading_ingest_token", "") or "")
        if not token:
            raise RuntimeError("canonical trading persistence token is not configured")
        async with httpx.AsyncClient(timeout=timeout) as http:
            response = await http.get(
                self._report_read_url,
                headers={"x-anevum-ingest-token": token},
                params=params,
            )
            response.raise_for_status()
            payload = response.json()
        if not isinstance(payload, dict) or not payload.get("ok"):
            raise RuntimeError("canonical report read returned no result")
        return payload

    async def _canonical_weekly_inputs(
        self,
        start_date: date,
        end_date: date,
    ) -> dict[str, Any]:
        payload = await self._report_api_get(
            start=start_date.isoformat(),
            end=end_date.isoformat(),
        )
        inputs = payload.get("inputs")
        if not isinstance(inputs, dict):
            raise RuntimeError("canonical weekly reporting inputs are unavailable")
        return inputs

    async def fetch_weekly_report(
        self,
        *,
        end_date: date | None = None,
    ) -> dict[str, Any] | None:
        params = {"latest": "weekly"}
        if end_date is not None:
            params["week_end"] = end_date.isoformat()
        payload = await self._report_api_get(**params)
        report = payload.get("report")
        return report if isinstance(report, dict) else None

    async def fetch_daily_report(
        self,
        *,
        session: date | None = None,
    ) -> dict[str, Any] | None:
        params = {"latest": "daily"}
        if session is not None:
            params["session"] = session.isoformat()
        payload = await self._report_api_get(**params)
        report = payload.get("report")
        return report if isinstance(report, dict) else None

    async def fetch_command_evidence(self) -> dict[str, Any]:
        payload = await self._report_api_get(latest="command")
        if not isinstance(payload, dict):
            raise RuntimeError("canonical Command evidence is unavailable")
        return payload

    async def generate_weekly(
        self,
        start_date: date,
        end_date: date,
    ) -> dict[str, Any]:
        """Build the canonical weekly report from durable daily reports + telemetry."""
        if self.event_sink.enabled:
            # A daily close report is queued immediately before the weekly report
            # on the final session. Drain that queue first so the weekly read
            # cannot race the authoritative daily-report write.
            await self.event_sink.queue.join()

        if hasattr(self.market_data, "market_calendar_details"):
            calendar = await self.market_data.market_calendar_details(
                start=start_date,
                end=end_date,
            )
        else:
            sessions = await self.market_data.market_calendar(
                start=start_date,
                end=end_date,
            )
            calendar = [{"date": session, "open": None, "close": None} for session in sessions]

        inputs = await self._canonical_weekly_inputs(start_date, end_date)
        persistence = self.event_sink.status()
        report = build_weekly_report(
            inputs,
            calendar,
            period_start=start_date,
            period_end=end_date,
            generation_provenance={
                "report_version": REPORT_VERSION,
                "generator": "alpaca-trader",
                "trading_run_id": getattr(self.settings, "trading_run_id", "") or None,
                "strategy_version_id": getattr(self.settings, "strategy_version_id", "") or None,
                "runtime_git_commit": os.environ.get("RAILWAY_GIT_COMMIT_SHA"),
                "runtime_deployment_id": os.environ.get("RAILWAY_DEPLOYMENT_ID"),
                "runtime_service_id": os.environ.get("RAILWAY_SERVICE_ID"),
                "runtime_environment_id": os.environ.get("RAILWAY_ENVIRONMENT_ID"),
                "source": "canonical_daily_reports_plus_telemetry",
            },
        )
        report_key = str(report.get("report_key") or "")
        if not report_key:
            raise RuntimeError("canonical weekly report has no report key")

        persisted = await self.event_sink.emit_critical(
            event_type="research_weekly_report",
            event_key=f"research_weekly_report:{report_key}",
            occurred_at=datetime.now(NY).isoformat(),
            payload=report,
        )
        if not persisted:
            raise RuntimeError(
                self.event_sink.last_error
                or "canonical weekly report could not be durably persisted"
            )

        stored = await self.fetch_weekly_report(end_date=end_date)
        self.last_weekly_report = stored or report
        completeness = str(
            self.last_weekly_report.get("completeness_state") or "INCOMPLETE"
        )
        self.last_error = (
            None
            if completeness in {"COMPLETE", "PARTIAL"}
            else (
                "canonical weekly report is INCOMPLETE; "
                "one or more required daily reports are missing"
            )
        )
        return self.last_weekly_report

    async def regenerate_weekly(self, end_date: date) -> dict[str, Any]:
        """Manual, read-only regeneration for the ISO trading week containing end_date."""
        start_date = end_date - timedelta(days=end_date.weekday())
        sessions = await self.market_data.market_calendar(
            start=start_date,
            end=end_date,
        )
        if end_date not in sessions:
            raise ValueError("week_end must be an actual US equity trading session")
        return await self.generate_weekly(start_date, end_date)
