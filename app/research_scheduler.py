from __future__ import annotations

import asyncio
from datetime import date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from .research_reporting import (
    classify_daily,
    enrich_excursions,
    next_research_action,
    reconstruct_closed_trades,
    scan_funnel,
    serialize,
    trade_metrics,
)

NY = ZoneInfo("America/New_York")
REPORT_AFTER = time(16, 20)


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
            "live_configuration_changes_allowed": False,
        }

    async def start(self) -> None:
        if self.task is None:
            self.task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        self.stop_event.set()
        if self.task is not None:
            await self.task
            self.task = None

    async def _run(self) -> None:
        while not self.stop_event.is_set():
            try:
                await self._tick()
            except Exception as exc:
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

        orders = await self.client.recent_orders(limit=500)
        data_quality_warnings: list[str] = []
        if len(orders) >= 500:
            data_quality_warnings.append(
                "recent order response reached the 500-order request limit"
            )

        fills: list[dict[str, Any]] = []
        for session in sessions:
            session_fills = await self.client.fill_activities(
                date=session.isoformat(),
                limit=100,
            )
            if len(session_fills) >= 100:
                data_quality_warnings.append(
                    f"{session.isoformat()} fill activity reached the 100-record request limit"
                )
            fills.extend(session_fills)

        positions = await self.client.positions()

        rebuilt = reconstruct_closed_trades(
            fills,
            orders,
            owner_tag=str(getattr(self.settings, "order_owner_tag", "") or ""),
        )
        trades = rebuilt["trades"]

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

    async def generate_daily(self, session: date) -> dict[str, Any]:
        evidence = await self._collect(session, session)
        account = await self.client.account()
        runtime = self._runtime_snapshot()
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
        payload = serialize(
            {
                "report_type": "daily",
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
                "data_quality_warnings": evidence["data_quality_warnings"],
                "reconstruction": evidence["reconstruction"],
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
        self.last_daily_report = payload
        self.last_error = None
        self.event_sink.emit(
            event_type="research_daily_report",
            event_key=(
                f"{getattr(self.settings, 'trading_run_id', '')}:"
                f"research_daily_report:{session.isoformat()}"
            ),
            occurred_at=datetime.now(NY).isoformat(),
            payload=payload,
        )
        return payload

    async def generate_weekly(
        self,
        start_date: date,
        end_date: date,
    ) -> dict[str, Any]:
        evidence = await self._collect(start_date, end_date)
        runtime = self._runtime_snapshot()
        payload = serialize(
            {
                "report_type": "weekly",
                "week_start": start_date.isoformat(),
                "week_end": end_date.isoformat(),
                "generated_at": datetime.now(NY),
                "sessions": evidence["sessions"],
                "trading_run_id": getattr(self.settings, "trading_run_id", "") or None,
                "strategy_version_id": getattr(self.settings, "strategy_version_id", "") or None,
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
                "data_quality_warnings": evidence["data_quality_warnings"],
                "reconstruction": evidence["reconstruction"],
                "candidate_funnel": evidence["funnel"],
                "runtime": runtime,
                "research_gate": {
                    "live_promotion_requires_separate_decision": True,
                    "historical_or_weekly_performance_is_not_promotion_authority": True,
                    "offline_validation_required": True,
                    "forward_shadow_required_when_applicable": True,
                },
                "live_configuration_changed": False,
                "promotion_authorized": False,
                "capital_scaling_authorized": False,
            }
        )
        self.last_weekly_report = payload
        self.last_error = None
        self.event_sink.emit(
            event_type="research_weekly_report",
            event_key=(
                f"{getattr(self.settings, 'trading_run_id', '')}:"
                f"research_weekly_report:{end_date.isoformat()}"
            ),
            occurred_at=datetime.now(NY).isoformat(),
            payload=payload,
        )
        return payload
